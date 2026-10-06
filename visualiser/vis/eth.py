"""Small JSON-RPC client: batching, timeouts, and eth_getLogs range splitting."""

from __future__ import annotations

import itertools
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class RpcError(RuntimeError):
    def __init__(self, message: str, code: int | None = None, data=None):
        super().__init__(message)
        self.code = code
        self.data = data


class JsonRpc:
    def __init__(self, url: str, timeout: float = 10.0):
        self.url = url
        self.timeout = timeout
        self._ids = itertools.count(1)

    def _post(self, payload):
        body = json.dumps(payload).encode()
        req = Request(self.url, data=body, headers={"content-type": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except HTTPError as exc:
            raise RpcError(f"HTTP {exc.code} from {self.url}") from exc
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            raise RpcError(f"cannot reach {self.url}: {getattr(exc, 'reason', exc)}") from exc

    @staticmethod
    def _unwrap(reply: dict):
        if "error" in reply and reply["error"]:
            err = reply["error"]
            raise RpcError(err.get("message") or str(err), err.get("code"), err.get("data"))
        return reply.get("result")

    def call(self, method: str, params=None):
        reply = self._post({"jsonrpc": "2.0", "id": next(self._ids), "method": method,
                            "params": [] if params is None else params})
        return self._unwrap(reply)

    def batch(self, calls: list[tuple[str, object]]) -> list:
        """Run several calls in one round trip. Each slot holds a result or an RpcError."""
        if not calls:
            return []
        start = next(self._ids)
        payload = [{"jsonrpc": "2.0", "id": start + i, "method": m, "params": p if p is not None else []}
                   for i, (m, p) in enumerate(calls)]
        for _ in range(len(calls)):
            next(self._ids)
        replies = self._post(payload)
        if isinstance(replies, dict):  # server refused the batch as a whole
            raise RpcError(replies.get("error", {}).get("message", "batch refused"))
        by_id = {r.get("id"): r for r in replies}
        out = []
        for i in range(len(calls)):
            reply = by_id.get(start + i)
            if reply is None:
                out.append(RpcError("missing reply in batch"))
                continue
            try:
                out.append(self._unwrap(reply))
            except RpcError as exc:
                out.append(exc)
        return out


class Eth(JsonRpc):
    """Ethereum helpers on top of JsonRpc."""

    RANGE_ERRORS = (-32005, -32602, -32000)

    def block_number(self, tag: str = "latest") -> int | None:
        if tag == "latest":
            return int(self.call("eth_blockNumber"), 16)
        block = self.call("eth_getBlockByNumber", [tag, False])
        return int(block["number"], 16) if block else None

    def eth_call(self, to: str, data: str, block: str = "latest") -> str:
        return self.call("eth_call", [{"to": to, "data": data}, block])

    def get_logs(self, addresses: list[str], topics: list | None, start: int, end: int,
                 max_span: int = 5000) -> list[dict]:
        """Fetch logs over [start, end], halving the window when the node refuses a range."""
        logs: list[dict] = []
        cursor = start
        span = max_span
        while cursor <= end:
            upper = min(end, cursor + span - 1)
            params = {"address": addresses, "fromBlock": hex(cursor), "toBlock": hex(upper)}
            if topics:
                params["topics"] = topics
            try:
                logs.extend(self.call("eth_getLogs", [params]) or [])
                cursor = upper + 1
            except RpcError as exc:
                # Providers reject oversized ranges with many different codes (dRPC 35, Cloudflare
                # -32047, ...). Any error the provider answered with is worth a smaller range; a
                # network error (no code) is not. A one-block range that still fails is real.
                if span > 1 and (exc.code in self.RANGE_ERRORS or exc.code is not None):
                    span = max(1, span // 2)
                    continue
                raise
        return logs
