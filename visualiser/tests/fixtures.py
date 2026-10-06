"""Fake sling database and fake reference node, seeded to match a driven Anvil.

The sling database is created from dave's real schema.sql, so its triggers
enforce the same append-only and density rules as a live node.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Sling's real schema, from a dave checkout (tag v3.0.0-alpha.5):
#   DAVE_DIR=/path/to/dave python3 tests/stack.py
SCHEMA = Path(os.environ.get("DAVE_DIR", "../dave")) / "cartesi-rollups/node/src/storage/sql/schema.sql"


def build_sling_db(path: Path, app: str, template: str, tournament: str, epoch0_final: str,
                   epoch0_commitment: str, inputs: list[bytes], processed_block: int) -> None:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA.read_text())
    conn.execute("PRAGMA journal_mode=WAL")
    b = lambda h: bytes.fromhex(h[2:])  # noqa: E731
    conn.execute("INSERT INTO node_metadata VALUES (0, 'sling 3.0.0-alpha.5', ?)", (b"\0" * 32,))
    conn.execute("INSERT INTO sling_config VALUES (0, 48, 44, 20, ?, ?, '0.20.0')", (b(app), b(template)))
    conn.execute("UPDATE latest_processed SET block = ?", (processed_block,))
    conn.execute("INSERT INTO epochs VALUES (0, 0, ?, 25)", (tournament[2:],))
    z32, z1888 = b"\0" * 32, b"\0" * 1888
    conn.execute("INSERT INTO settlement_info VALUES (0, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (b(epoch0_commitment), b(epoch0_final), z32, z1888, z32, z1888, z32, z1888))
    for i, blob in enumerate(inputs):
        conn.execute("INSERT INTO inputs VALUES (1, ?, ?)", (i, blob))
    conn.commit()
    conn.close()


class FakeReference:
    """Answers the cartesi_* methods the visualiser uses, with real pagination."""

    def __init__(self, port: int, app: str, epochs: list[dict], inputs: list[dict], outputs: list[dict]):
        self.port = port
        self.app = app
        self.data = {"epochs": epochs, "inputs": inputs, "outputs": outputs}
        self.fail = False

    def handle(self, method: str, params: dict):
        if self.fail:
            raise RuntimeError("database is locked")
        page = lambda rows: self._page(rows, params)  # noqa: E731
        if method == "cartesi_getNodeInfo":
            return {"version": "2.0.0-dev", "chain_id": "0x7a69"}
        if method == "cartesi_listApplications":
            return page([{"name": "echo", "iapplication_address": self.app, "iconsensus_address":
                          "0x570c32cb7eac495d59e30c78d775facb4d027f68", "consensus_type": "PRT",
                          "template_hash": "0xc8217d7fa39a7a4ba65e5efacb1cfca9996dd76ceea945299fea9f4f2786f3b2",
                          "state": "ENABLED", "reason": None, "processed_inputs": "0x2",
                          "last_input_check_block": "0x20", "last_epoch_check_block": "0x20",
                          "last_tournament_check_block": "0x20"}])
        if method == "cartesi_listEpochs":
            return page([e for e in self.data["epochs"] if int(e["index"], 16) >= int(params.get("from", "0x0"), 16)])
        if method == "cartesi_listInputs":
            return page([i for i in self.data["inputs"] if int(i["index"], 16) >= int(params.get("from", "0x0"), 16)])
        if method == "cartesi_listOutputs":
            return page([o for o in self.data["outputs"] if int(o["index"], 16) >= int(params.get("from", "0x0"), 16)])
        if method in ("cartesi_listReports", "cartesi_listCommitments", "cartesi_listMatches"):
            return page([])
        raise KeyError(method)

    @staticmethod
    def _page(rows, params):
        offset, limit = params.get("offset", 0), params.get("limit", 50)
        return {"data": rows[offset: offset + limit],
                "pagination": {"total_count": len(rows), "limit": limit, "offset": offset}}

    def serve(self):
        owner = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                return

            def do_POST(self):  # noqa: N802
                req = json.loads(self.rfile.read(int(self.headers["content-length"])))
                try:
                    reply = {"jsonrpc": "2.0", "id": req["id"], "result": owner.handle(req["method"], req["params"])}
                except KeyError as exc:
                    reply = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": f"no {exc}"}}
                except RuntimeError as exc:
                    reply = {"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32000, "message": str(exc)}}
                body = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
