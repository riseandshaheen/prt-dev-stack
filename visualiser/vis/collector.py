"""Background collection: one loop polls every source and publishes a snapshot.

Requests are served from the last published snapshot, so page load and the
number of open tabs never add load on the nodes or the chain.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import traceback

from .chain import ChainIndex
from .model import Model
from .reference import ReferenceSource
from .sling import SlingSource


VOLATILE = {"generated_at", "last_ok_at", "poll_ms", "checked_at", "version"}


def _strip(value):
    """Drop fields that change every cycle without meaning anything changed."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, (list, tuple)):
        return [_strip(v) for v in value]
    return value


class Collector:
    def __init__(self, cfg):
        self.cfg = cfg
        self.chain = ChainIndex(cfg.chain_rpc, cfg.dave_app_factory, cfg.ledger_limit)
        self.sources = [SlingSource(n) if n.kind == "sling" else ReferenceSource(n) for n in cfg.nodes]
        self.model = Model(cfg, self.chain, self.sources)
        self.health: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.changed = threading.Condition(self.lock)
        self.version = 0
        self.digest = None
        self.snapshot: dict = {"overview": None, "apps": {}, "epochs": {}, "ledgers": {}}
        self.last_cycle_at: float | None = None
        self._stop = threading.Event()

    # ------------------------------------------------------------------ loop

    def start(self) -> None:
        threading.Thread(target=self._run, name="collector", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            started = time.time()
            try:
                self.cycle()
            except Exception:  # noqa: BLE001 - never let the loop die
                traceback.print_exc()
            self._stop.wait(max(0.2, self.cfg.poll_seconds - (time.time() - started)))

    def _timed(self, key: str, fn) -> bool:
        started = time.time()
        entry = self.health.setdefault(key, {})
        try:
            fn()
            entry.update(ok=True, error=None, last_ok_at=time.time())
            return True
        except Exception as exc:  # noqa: BLE001 - surfaced in the UI
            entry.update(ok=False, error=str(exc) or exc.__class__.__name__)
            return False
        finally:
            entry["poll_ms"] = round((time.time() - started) * 1000)
            entry["checked_at"] = time.time()

    def cycle(self) -> None:
        self.cfg.reload_labels()
        for source in self.sources:
            self._timed(source.cfg.id, source.poll)
        wanted = set(self.cfg.apps)
        for source in self.sources:
            if self.health.get(source.cfg.id, {}).get("ok"):
                wanted |= source.apps()
        generation = self.chain.generation
        self._timed("chain", lambda: self.chain.poll(wanted))
        if self.chain.generation != generation:
            # The chain was reset or reorged: rows the nodes' caches hold may belong to
            # the old history, so drop them and read every node again from scratch.
            for source in self.sources:
                source.reset()
                self._timed(source.cfg.id, source.poll)
        self.publish()
        self.last_cycle_at = time.time()

    # --------------------------------------------------------------- publish

    def publish(self) -> None:
        overview = self.model.overview(self.health)
        apps, epochs, ledgers = {}, {}, {}
        for address in list(self.chain.apps):
            detail = self.model.app_detail(address)
            apps[address] = detail
            ledgers[address] = self.model.ledger(address)
            for view in detail["epochs"]:
                epochs[(address, view["number"])] = self.model.epoch_detail(address, view["number"])
        # Only bump the version (and wake streams) when something a viewer sees changed.
        stable = json.dumps(_strip([overview, apps, sorted(epochs.items(), key=str), ledgers]),
                            sort_keys=True, default=str).encode()
        digest = hashlib.sha256(stable).hexdigest()
        with self.changed:
            self.snapshot = {"overview": overview, "apps": apps, "epochs": epochs, "ledgers": ledgers}
            if digest != self.digest:
                self.digest = digest
                self.version += 1
            overview["version"] = self.version
            self.changed.notify_all()

    def wait_for_change(self, seen: int, timeout: float) -> int:
        with self.changed:
            self.changed.wait_for(lambda: self.version != seen, timeout=timeout)
            return self.version

    def healthy(self) -> tuple[bool, dict]:
        now = time.time()
        fresh = self.last_cycle_at is not None and now - self.last_cycle_at < max(15, self.cfg.poll_seconds * 5)
        chain_ok = self.health.get("chain", {}).get("ok", False)
        body = {"ok": fresh and chain_ok, "last_cycle_age_s": round(now - self.last_cycle_at, 1) if self.last_cycle_at else None,
                "sources": {k: {"ok": v.get("ok"), "error": v.get("error")} for k, v in self.health.items()}}
        return fresh and chain_ok, body
