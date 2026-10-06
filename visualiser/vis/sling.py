"""Sling node source: its SQLite state, read-only.

Two ways to reach the database:
  * db: a path to db.sqlite3 on a volume mounted read-only (recommended, and
    what the compose service does). SQLite's WAL lets us read while sling writes.
  * container: `docker cp` of db.sqlite3 and its -wal/-shm files, for running on
    the host on macOS where Docker volumes are not visible. The three copies are
    not atomic, so a read that fails integrity checks is retried.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

from .config import NodeConfig


TESTED_SLING = "dave v3.0.0-alpha.5 (node_version 2.0.0)"


class SlingSource:
    kind = "sling"

    def __init__(self, cfg: NodeConfig):
        self.cfg = cfg
        self.inputs: dict[tuple[int, int], dict] = {}  # (epoch, index_in_epoch) -> summary
        self.state: dict = {}

    def reset(self) -> None:
        self.inputs = {}

    # ------------------------------------------------------------ database

    def _open(self):
        """Return (connection, cleanup) for a consistent read of the database."""
        if self.cfg.db:
            path = Path(self.cfg.db)
            if not path.exists():
                raise RuntimeError(f"{path} does not exist; is the sling state volume mounted?")
            uri = f"file:{path}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            return conn, lambda: conn.close()
        temp = Path(tempfile.mkdtemp(prefix="vis-sling-"))
        copied = []
        for name in ("db.sqlite3", "db.sqlite3-wal", "db.sqlite3-shm"):
            result = subprocess.run(
                ["docker", "cp", f"{self.cfg.container}:/state/{name}", str(temp / name)],
                capture_output=True, text=True, timeout=20)
            if result.returncode == 0:
                copied.append(name)
            elif name == "db.sqlite3":
                shutil.rmtree(temp, ignore_errors=True)
                raise RuntimeError(result.stderr.strip() or f"docker cp from {self.cfg.container} failed")
        conn = sqlite3.connect(temp / "db.sqlite3", timeout=5)

        def cleanup():
            conn.close()
            shutil.rmtree(temp, ignore_errors=True)
        return conn, cleanup

    def poll(self) -> dict:
        last_error = None
        for attempt in range(3):
            conn, cleanup = self._open()
            try:
                conn.row_factory = sqlite3.Row
                self.state = self._read(conn)
                return self.state
            except sqlite3.OperationalError as exc:
                if "no such table" in str(exc) or "no such column" in str(exc):
                    raise RuntimeError(f"sling database schema is not the one this visualiser reads "
                                       f"(built for {TESTED_SLING}): {exc}") from exc
                last_error = exc  # locked or mid-write; try again
                time.sleep(0.2 * (attempt + 1))
            except sqlite3.DatabaseError as exc:
                last_error = exc  # torn copy or a write in flight; try again
                time.sleep(0.2 * (attempt + 1))
            finally:
                cleanup()
        raise RuntimeError(f"sling database unreadable after 3 attempts: {last_error}")

    def _read(self, conn: sqlite3.Connection) -> dict:
        one = lambda sql: conn.execute(sql).fetchone()  # noqa: E731
        config = one("SELECT log2_input_span, log2_barch_span, log2_uarch_span, hex(app) AS app, "
                     "hex(template_hash) AS template_hash, emulator_version FROM sling_config")
        if config is None:
            raise RuntimeError("sling_config is empty; the node has not initialised yet")
        meta = one("SELECT node_version FROM node_metadata")
        latest = one("SELECT block FROM latest_processed")
        completion = one("SELECT next_epoch, hex(claimant) AS claimant FROM epoch_completion")

        epochs = []
        previous_boundary = 0
        for row in conn.execute("SELECT epoch_number, input_index_boundary, root_tournament, "
                                "block_created_number FROM epochs ORDER BY epoch_number"):
            tournament = (row["root_tournament"] or "").lower()
            if tournament and not tournament.startswith("0x"):
                tournament = "0x" + tournament
            epochs.append({
                "number": row["epoch_number"], "lower": previous_boundary,
                "upper": row["input_index_boundary"], "tournament": tournament,
                "block": row["block_created_number"],
            })
            previous_boundary = row["input_index_boundary"]

        settlements = {
            row["epoch_number"]: {"commitment": "0x" + row["ch"].lower(), "final_state": "0x" + row["fs"].lower()}
            for row in conn.execute("SELECT epoch_number, hex(computation_hash) AS ch, "
                                    "hex(final_state) AS fs FROM settlement_info")
        }

        # inputs is append-only and dense: only read rows we have not seen. If the last row
        # we cached is gone or changed, sling's state was wiped and rebuilt: start over.
        if self.inputs:
            key = max(self.inputs)
            row = conn.execute("SELECT input FROM inputs WHERE epoch_number = ? AND input_index_in_epoch = ?",
                               key).fetchone()
            if row is None or hashlib.sha256(bytes(row["input"])).hexdigest() != self.inputs[key]["sha256"]:
                self.inputs = {}
        last = max(self.inputs) if self.inputs else (-1, -1)
        for row in conn.execute(
            "SELECT epoch_number, input_index_in_epoch, input FROM inputs "
            "WHERE epoch_number > ? OR (epoch_number = ? AND input_index_in_epoch > ?) "
            "ORDER BY epoch_number, input_index_in_epoch", (last[0], last[0], last[1])):
            blob = bytes(row["input"])
            self.inputs[(row["epoch_number"], row["input_index_in_epoch"])] = {
                "size": len(blob), "sha256": hashlib.sha256(blob).hexdigest(),
            }

        def optional(sql):  # detail tables: a sling without them still shows epochs and settlements
            try:
                return conn.execute(sql).fetchall()
            except sqlite3.OperationalError:
                return []
        hexkey = lambda t: t.lower() if t.startswith("0x") else "0x" + t.lower()  # noqa: E731
        events = {hexkey(r["t"]): r["n"] for r in optional(
            "SELECT root_tournament AS t, count(*) AS n FROM tournament_events GROUP BY 1")}
        watermarks = {hexkey(r["t"]): r["b"] for r in optional(
            "SELECT root_tournament AS t, finalized_block AS b FROM tournament_events_watermark")}
        tree_nodes = {r["epoch"]: r["n"] for r in optional("SELECT epoch, count(*) AS n FROM sling_nodes GROUP BY epoch")}

        mtime = None
        if self.cfg.db:
            paths = [Path(self.cfg.db), Path(self.cfg.db + "-wal")]
            mtime = max((p.stat().st_mtime for p in paths if p.exists()), default=None)

        return {
            "app": "0x" + config["app"].lower(),
            "template_hash": "0x" + config["template_hash"].lower(),
            "node_version": meta["node_version"] if meta else None,
            "emulator_version": config["emulator_version"],
            "spans": {"input": config["log2_input_span"], "barch": config["log2_barch_span"],
                      "uarch": config["log2_uarch_span"]},
            "latest_processed_block": latest["block"] if latest else None,
            "next_epoch": completion["next_epoch"] if completion else None,
            "claimant": ("0x" + completion["claimant"].lower()) if completion and completion["claimant"] else None,
            "epochs": epochs,
            "settlements": settlements,
            "tournament_events": events,
            "tournament_watermarks": watermarks,
            "tree_nodes": tree_nodes,
            "db_written_at": mtime,
        }

    # ------------------------------------------------------------- queries

    def apps(self) -> set[str]:
        return {self.state["app"]} if self.state.get("app") else set()

    def input_digest(self, epoch: int, index_in_epoch: int) -> dict | None:
        return self.inputs.get((epoch, index_in_epoch))

