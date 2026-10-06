"""Anvil control: the harness is the only miner.

Anvil's default accounts are unlocked, so adversary transactions go through
eth_sendTransaction with no signing on our side.
"""

from __future__ import annotations

import time

from vis import abi
from vis.eth import Eth, RpcError

from . import stack


class Chain(Eth):
    def __init__(self, url: str = stack.RPC):
        super().__init__(url, timeout=30)
        self.mined = 0

    # ------------------------------------------------------------- mining
    def automine(self, on: bool) -> None:
        self.call("evm_setAutomine", [on])

    def is_automine(self) -> bool:
        return bool(self.call("anvil_getAutomine"))

    def mine(self, blocks: int = 1) -> int:
        while blocks > 0:  # large mines time out the RPC; chunk them
            step = min(blocks, 1000)
            self.call("anvil_mine", [hex(step)])
            self.mined += step
            blocks -= step
        return self.head()

    def head(self) -> int:
        return int(self.call("eth_blockNumber"), 16)

    def snapshot(self) -> str:
        return self.call("evm_snapshot")

    def revert_to(self, snap: str) -> bool:
        return bool(self.call("evm_revert", [snap]))

    # ------------------------------------------------------------ mempool
    def pending(self) -> list[dict]:
        """Pending txs as {hash, from, to, function, gas_price, nonce}."""
        content = self.call("txpool_content") or {}
        out = []
        for by_sender in (content.get("pending") or {}).values():
            for tx in by_sender.values():
                out.append({
                    "hash": tx["hash"], "from": tx["from"].lower(), "to": (tx.get("to") or "").lower(),
                    "function": abi.function_name(tx.get("input")),
                    "gas_price": int(tx.get("maxFeePerGas") or tx.get("gasPrice") or "0x0", 16),
                    "nonce": int(tx["nonce"], 16),
                })
        return out

    def wait_pending(self, want, timeout: float, poll: float = 0.25) -> list[dict]:
        """Wait, without mining, until want(pending) is truthy; return the pending list."""
        deadline = time.time() + timeout
        while True:
            txs = self.pending()
            if want(txs) or time.time() >= deadline:
                return txs
            time.sleep(poll)

    # -------------------------------------------------------------- sends
    def send(self, sender: str, to: str, data: str, value: int = 0, gas: int = 3_000_000,
             priority_gwei: int | None = None) -> str:
        tx = {"from": sender, "to": to, "data": data, "gas": hex(gas), "value": hex(value)}
        if priority_gwei is not None:
            base = int(self.call("eth_getBlockByNumber", ["pending", False])["baseFeePerGas"], 16)
            tip = priority_gwei * 10**9
            tx.update(maxPriorityFeePerGas=hex(tip), maxFeePerGas=hex(base * 2 + tip))
        return self.call("eth_sendTransaction", [tx])

    def receipt(self, tx_hash: str) -> dict | None:
        return self.call("eth_getTransactionReceipt", [tx_hash])

    def view(self, to: str, signature: str, types: list[str], *args) -> list:
        return abi.decode(types, self.eth_call(to, abi.encode_call(signature, *args)))

    # ------------------------------------------------------------- ledger
    def txs(self, start: int, end: int) -> list[dict]:
        """Every tx in [start, end] with status and decoded revert reason."""
        out = []
        for chunk in range(start, end + 1, 100):
            numbers = list(range(chunk, min(end, chunk + 99) + 1))
            blocks = self.batch([("eth_getBlockByNumber", [hex(n), True]) for n in numbers])
            txs = [(int(b["number"], 16), i, tx) for b in blocks if isinstance(b, dict)
                   for i, tx in enumerate(b["transactions"])]
            receipts = self.batch([("eth_getTransactionReceipt", [tx["hash"]]) for _, _, tx in txs])
            for (number, position, tx), rc in zip(txs, receipts):
                ok = isinstance(rc, dict) and rc.get("status") == "0x1"
                entry = {"block": number, "position": position, "hash": tx["hash"],
                         "from": tx["from"].lower(), "to": (tx.get("to") or "").lower(),
                         "function": abi.function_name(tx.get("input")), "input": tx.get("input"),
                         "ok": ok, "revert": None, "logs": rc.get("logs", []) if isinstance(rc, dict) else []}
                if not ok:
                    entry["revert"] = self.revert_reason(tx, number)
                out.append(entry)
        return out

    def revert_reason(self, tx: dict, block: int) -> str | None:
        """Decode the revert from a trace of the tx itself, so earlier txs in its block count."""
        try:
            trace = self.call("debug_traceTransaction", [tx["hash"], {"tracer": "callTracer"}])
            data = trace.get("output")
            return abi.decode_revert(data) or trace.get("revertReason") or trace.get("error") or "reverted"
        except RpcError as exc:
            return f"reverted (trace unavailable: {exc})"

def events(chain: Chain, address: str | list[str], start: int, end: int, names: set[str] | None = None) -> list[dict]:
    addresses = address if isinstance(address, list) else [address]
    out = []
    for log in chain.get_logs(addresses, None, start, end):
        decoded = abi.decode_log(log)
        if decoded and (names is None or decoded["name"] in names):
            out.append({"name": decoded["name"], "args": decoded["args"], "address": log["address"].lower(),
                        "block": int(log["blockNumber"], 16), "tx": log["transactionHash"],
                        "log_index": int(log["logIndex"], 16)})
    return out
