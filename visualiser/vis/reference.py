"""Reference (rollups-node) source: its JSON-RPC API, paginated and incremental.

Settled rows never change, so after the first full read each poll only asks for
epochs and inputs from the first one that can still change. The cache is dropped
when the chain is reset (see Collector) or the node re-creates the application.
"""

from __future__ import annotations

from .config import NodeConfig
from .eth import JsonRpc, RpcError

PAGE = 1000
FINAL_EPOCH = {"CLAIM_ACCEPTED", "CLAIM_REJECTED", "CLAIM_FORECLOSED"}


def hex_int(value) -> int | None:
    if value is None or value == "":
        return None
    text = str(value)
    return int(text, 16) if text.startswith("0x") else int(text)


def _lower(value):
    return value.lower() if isinstance(value, str) else value


class ReferenceSource:
    kind = "reference"

    def __init__(self, cfg: NodeConfig):
        self.cfg = cfg
        self.rpc = JsonRpc(cfg.rpc, timeout=10)
        self.apps_state: dict[str, dict] = {}
        self.state: dict = {}

    def reset(self) -> None:
        self.apps_state = {}

    def _list(self, method: str, params: dict) -> list[dict]:
        rows: list[dict] = []
        offset = 0
        while True:
            reply = self.rpc.call(method, dict(params, limit=PAGE, offset=offset))
            data = reply.get("data", []) if isinstance(reply, dict) else (reply or [])
            rows.extend(data)
            total = (reply.get("pagination") or {}).get("total_count") if isinstance(reply, dict) else None
            offset += len(data)
            if not data or total is None or offset >= total:
                return rows

    def poll(self) -> dict:
        info = self.rpc.call("cartesi_getNodeInfo") or {}
        if isinstance(info, dict) and "data" in info and "version" not in info:
            info = info["data"]
        apps = []
        errors = []
        for raw in self._list("cartesi_listApplications", {}):
            address = _lower(raw.get("iapplication_address"))
            app = {
                "name": raw.get("name"), "address": address,
                "consensus": _lower(raw.get("iconsensus_address")),
                "consensus_type": raw.get("consensus_type"),
                "template_hash": _lower(raw.get("template_hash")),
                "state": raw.get("state") or raw.get("status"),
                "reason": raw.get("reason"),
                "enabled": raw.get("enabled", raw.get("state") in (None, "ENABLED")),
                "processed_inputs": hex_int(raw.get("processed_inputs")),
                "last_input_check_block": hex_int(raw.get("last_input_check_block")),
                "last_epoch_check_block": hex_int(raw.get("last_epoch_check_block")),
                "last_tournament_check_block": hex_int(raw.get("last_tournament_check_block")),
                "created_at": raw.get("created_at"),
            }
            try:
                self._poll_app(app)
            except RpcError as exc:
                errors.append(f"{app['name'] or address}: {exc}")
            apps.append(app)
        self.state = {
            "version": info.get("version") if isinstance(info, dict) else None,
            "chain_id": hex_int(info.get("chain_id")) if isinstance(info, dict) else None,
            "apps": {a["address"]: a for a in apps if a["address"]},
            "errors": errors,
        }
        return self.state

    def _poll_app(self, app: dict) -> None:
        key = app["address"]
        name = app["name"] or key
        cache = self.apps_state.get(key)
        if cache is None or cache["created_at"] != app["created_at"]:
            # First read, or the node's database was rebuilt and the app registered again.
            cache = self.apps_state[key] = {"created_at": app["created_at"], "epochs": {}, "inputs": {},
                                            "outputs": {}, "reports": {}}

        open_epochs = [i for i, e in cache["epochs"].items() if e["status"] not in FINAL_EPOCH]
        start = min(open_epochs) if open_epochs else (max(cache["epochs"]) + 1 if cache["epochs"] else 0)
        for raw in self._list("cartesi_listEpochs", {"application": name, "from": hex(start)}):
            index = hex_int(raw.get("index"))
            cache["epochs"][index] = {
                "index": index, "status": raw.get("status"),
                "lower": hex_int(raw.get("input_index_lower_bound")),
                "upper": hex_int(raw.get("input_index_upper_bound")),
                "first_block": hex_int(raw.get("first_block")), "last_block": hex_int(raw.get("last_block")),
                "machine_hash": _lower(raw.get("machine_hash")),
                "commitment": _lower(raw.get("commitment")),
                "tournament": _lower(raw.get("tournament_address")),
                "staged_at_block": hex_int(raw.get("staged_at_block")),
                "claim_tx": raw.get("claim_transaction_hash"),
            }

        unfinished = [i for i, x in cache["inputs"].items() if x["status"] in (None, "NONE")]
        start = min(unfinished) if unfinished else (max(cache["inputs"]) + 1 if cache["inputs"] else 0)
        for raw in self._list("cartesi_listInputs", {"application": name, "from": hex(start)}):
            index = hex_int(raw.get("index"))
            decoded = raw.get("decoded_data") or {}
            cache["inputs"][index] = {
                "index": index, "epoch": hex_int(raw.get("epoch_index")), "status": raw.get("status"),
                "block": hex_int(raw.get("block_number")), "sender": _lower(decoded.get("sender")),
                "tx": raw.get("transaction_hash"),
            }

        for kind, method in (("outputs", "cartesi_listOutputs"), ("reports", "cartesi_listReports")):
            seen = cache[kind]
            start = max(seen) + 1 if seen else 0
            for raw in self._list(method, {"application": name, "from": hex(start)}):
                seen[hex_int(raw.get("index"))] = hex_int(raw.get("input_index"))

        app["epochs"] = cache["epochs"]
        app["inputs"] = cache["inputs"]
        counts: dict[int, list[int]] = {}
        for kind, slot in (("outputs", 0), ("reports", 1)):
            for input_index in cache[kind].values():
                counts.setdefault(input_index, [0, 0])[slot] += 1
        app["output_counts"] = counts

        try:
            app["commitments"] = [
                {"epoch": hex_int(c.get("epoch_index")), "tournament": _lower(c.get("tournament_address")),
                 "root": _lower(c.get("commitment")), "final_state": _lower(c.get("final_state_hash")),
                 "submitter": _lower(c.get("submitter_address")), "block": hex_int(c.get("block_number"))}
                for c in self._list("cartesi_listCommitments", {"application": name})
            ]
            app["matches"] = [
                {"epoch": hex_int(m.get("epoch_index")), "tournament": _lower(m.get("tournament_address")),
                 "id": _lower(m.get("id_hash")), "deleted_block": hex_int(m.get("deletion_block_number"))}
                for m in self._list("cartesi_listMatches", {"application": name})
            ]
        except RpcError:
            app["commitments"], app["matches"] = [], []

    def apps(self) -> set[str]:
        return set(self.state.get("apps", {}))
