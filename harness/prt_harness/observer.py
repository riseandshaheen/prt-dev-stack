"""Three-way observer: chain, sling and the reference node, through the visualiser's model."""

from __future__ import annotations

import json
import tempfile

from vis import config as vis_config
from vis.collector import Collector

from . import stack

CONFIG = {
    "listen": {"host": "127.0.0.1", "port": 0},
    "chain": {"rpc": stack.RPC, "poll_seconds": 1, "dave_app_factory": None},
    "apps": [stack.APP],
    "nodes": [
        {"id": "sling", "kind": "sling", "label": "Sling", "container": stack.CONTAINERS["sling"],
         "accounts": [stack.SLING]},
        {"id": "reference", "kind": "reference", "label": "Reference node", "rpc": stack.REFERENCE_RPC,
         "accounts": [stack.ACCOUNTS[0], stack.REFERENCE_PRT]},
    ],
}


class Observer:
    def __init__(self):
        path = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(CONFIG, path)
        path.close()
        self.collector = Collector(vis_config.load(path.name))

    def poll(self) -> dict:
        """One collection cycle; returns {health, epochs: [epoch views], nodes: {...}}."""
        c = self.collector
        c.cycle()
        app = c.chain.apps.get(stack.APP)
        views = c.model.epoch_views(app) if app else []
        return {
            "health": {k: dict(v) for k, v in c.health.items()},
            "epochs": views,
            "head": c.chain.head,
            "finalized": c.chain.finalized,
            "sling": dict(c.sources[0].state),
            "reference": (c.sources[1].state.get("apps") or {}).get(stack.APP, {}),
        }

    def epoch_detail(self, number: int) -> dict | None:
        return self.collector.model.epoch_detail(stack.APP, number)
