"""Configuration: a JSON file, with environment-variable fallbacks for the old CLI."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_LABELS = {
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266": "Anvil 0 (reference claims)",
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8": "Anvil 1 (inputs)",
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc": "Anvil 2 (tests)",
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906": "Anvil 3",
    "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65": "Anvil 4",
    "0x9965507d1a55bcc2695c58ba16fb37d819b0a4dc": "Anvil 5",
    "0x976ea74026e726554db657fa54763abd0c3a0aa9": "Anvil 6 (reference PRT)",
    "0x14dc79964da2c08b23698b3d3cc7ca32193d9955": "Anvil 7 (sling, sentry)",
    "0x23618e81e3f5cdf7f54c3d65f7fbc0abf5b21e8f": "Anvil 8",
    "0xa0ee7a142d267c1f36714e4a8f75612f20a79720": "Anvil 9",
}


@dataclass
class NodeConfig:
    id: str
    kind: str                      # "sling" | "reference"
    label: str
    rpc: str | None = None         # reference: JSON-RPC URL
    db: str | None = None          # sling: path to db.sqlite3 (volume mounted read-only)
    container: str | None = None   # sling: container name, used with docker cp when db is unset
    accounts: list[str] = field(default_factory=list)  # signer addresses, for the tx ledger
    optional: bool = False         # expected to be off at times: shown as "off", not raised as an alert


@dataclass
class Config:
    host: str = "127.0.0.1"
    port: int = 8787
    chain_rpc: str = "http://127.0.0.1:8545"
    poll_seconds: float = 3.0
    dave_app_factory: str | None = None
    apps: list[str] = field(default_factory=list)
    app_names: dict[str, str] = field(default_factory=dict)   # address -> name, used when no node reports one
    labels: dict[str, str] = field(default_factory=dict)
    nodes: list[NodeConfig] = field(default_factory=list)
    ledger_limit: int = 20000      # transactions kept per application
    path: str | None = None
    labels_mtime: float | None = None

    def reload_labels(self) -> bool:
        """Pick up label edits in the config file without a restart. True when they changed."""
        if not self.path:
            return False
        try:
            mtime = os.stat(self.path).st_mtime
            if mtime == self.labels_mtime:
                return False
            raw = json.loads(Path(self.path).read_text())
        except (OSError, ValueError):
            return False  # mid-write or briefly invalid: keep the current labels
        self.labels_mtime = mtime
        labels = _labels(raw, self.nodes)
        if labels == self.labels:
            return False
        self.labels = labels
        return True

    def label(self, address: str | None) -> str | None:
        return self.labels.get((address or "").lower()) if address else None


def _node(raw: dict) -> NodeConfig:
    kind = raw["kind"]
    if kind not in ("sling", "reference"):
        raise ValueError(f"node {raw.get('id')}: kind must be sling or reference, not {kind!r}")
    node = NodeConfig(
        id=raw["id"], kind=kind, label=raw.get("label", raw["id"]), rpc=raw.get("rpc"),
        db=raw.get("db"), container=raw.get("container"),
        accounts=[a.lower() for a in raw.get("accounts", [])],
        optional=bool(raw.get("optional", False)),
    )
    if kind == "reference" and not node.rpc:
        raise ValueError(f"node {node.id}: reference nodes need rpc")
    if kind == "sling" and not (node.db or node.container):
        raise ValueError(f"node {node.id}: sling nodes need db or container")
    return node


def load(path: str | None = None) -> Config:
    path = path or os.environ.get("VIS_CONFIG")
    if path:
        raw = json.loads(Path(path).read_text())
        chain = raw.get("chain", {})
        listen = raw.get("listen", {})
        nodes = [_node(n) for n in raw.get("nodes", [])]
        cfg = Config(
            path=path, labels_mtime=os.stat(path).st_mtime,
            host=os.environ.get("VIS_HOST", listen.get("host", "127.0.0.1")),
            port=int(os.environ.get("VIS_PORT", listen.get("port", 8787))),
            chain_rpc=os.environ.get("VIS_CHAIN_RPC", chain.get("rpc", "http://127.0.0.1:8545")),
            poll_seconds=float(chain.get("poll_seconds", 3)),
            dave_app_factory=(chain.get("dave_app_factory") or "").lower() or None,
            apps=[a.lower() for a in raw.get("apps", [])],
            app_names={k.lower(): v for k, v in raw.get("app_names", {}).items()},
            labels=_labels(raw, nodes),
            nodes=nodes,
            ledger_limit=int(raw.get("ledger_limit", 20000)),
        )
    else:
        # Same variables as the original server.py, so the old command still works.
        cfg = Config(
            host=os.environ.get("VIS_HOST", "127.0.0.1"),
            port=int(os.environ.get("VIS_PORT", os.environ.get("SLING_STATUS_PORT", "8787"))),
            chain_rpc=os.environ.get("SLING_RPC_URL", "http://127.0.0.1:8545"),
            labels=dict(DEFAULT_LABELS),
            nodes=[
                NodeConfig(id="sling", kind="sling", label="Sling",
                           container=os.environ.get("SLING_NODE_CONTAINER", "prt-sling-node-1"),
                           accounts=["0x14dc79964da2c08b23698b3d3cc7ca32193d9955"]),
                NodeConfig(id="reference", kind="reference", label="Reference node",
                           rpc=os.environ.get("REFERENCE_RPC_URL", "http://127.0.0.1:10011/rpc"),
                           accounts=["0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",
                                     "0x976ea74026e726554db657fa54763abd0c3a0aa9"]),
            ],
        )
    ids = [n.id for n in cfg.nodes]
    if len(ids) != len(set(ids)):
        raise ValueError("node ids must be unique")
    if not path:
        cfg.labels = _labels({}, cfg.nodes)
    return cfg


def _labels(raw: dict, nodes: list[NodeConfig]) -> dict[str, str]:
    """Built-in Anvil names, then the config's labels, then node accounts for anything left."""
    labels = dict(DEFAULT_LABELS)
    labels.update({k.lower(): v for k, v in raw.get("labels", {}).items()})
    for node in nodes:
        for account in node.accounts:
            labels.setdefault(account.lower(), node.label)
    return labels
