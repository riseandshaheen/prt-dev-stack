#!/usr/bin/env python3
"""Fault-injecting JSON-RPC proxy between the sling node and anvil.

Sits in front of anvil so a scenario can point the node at it
(--web3-rpc-url http://127.0.0.1:<listen>) and perturb only what the node
sees on the wire, while the honest oracle and sybils keep talking to anvil
directly. Every request is forwarded upstream; selected responses are
mutated. All perturbations are logged to stderr and counted.

Usage:
  rpc_proxy.py --listen 18545 --upstream http://127.0.0.1:8545 [modes...]

Modes (may combine):
  --count
      Pass through unchanged; just tally method calls (baseline/control).
  --drop-log TOPIC0[:NTH]
      In eth_getLogs results, remove logs whose topics[0] == TOPIC0. With
      :NTH, drop only the NTH such log seen across the whole run (0-based);
      without it, drop every match. Simulates a provider that silently
      omits one InputAdded (or any) event.
  --error-range CODE:MINSPAN
      Reply to any eth_getLogs whose (toBlock-fromBlock) >= MINSPAN with a
      JSON-RPC error {code: CODE}. Exercises the node's range-bisection
      path (blockchain_reader logs_bisecting).
  --delay-ms MS
      Sleep MS before forwarding every request (slow provider).

The proxy is deliberately single-file and dependency-free (http.server).
It is a test instrument, not a production component.

Vendored from Mugen-Builders/qa-slingnode-v0 (findings/SLN-38.../harness/rpc_proxy.py).
Local changes: --bind, and GET / also reports eth_call counts per (to, selector) so a
caller can measure per-tick read load.
"""
import argparse
import json
import random
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ARGS = None
INPUT_ADDED = "0xc05d337121a6e8605c6ec0b72aa29c4210ffe6e5b9cefdd6a7058188a8f66f98"
STATE = {"drop_seen": {}, "method_counts": {}, "call_counts": {}}


def log(msg):
    sys.stderr.write("[rpc_proxy] " + msg + "\n")
    sys.stderr.flush()


def forward(body_bytes):
    req = urllib.request.Request(
        ARGS.upstream, data=body_bytes,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def hexspan(params):
    """Return (toBlock - fromBlock) for an eth_getLogs param, or None."""
    try:
        f = params[0].get("fromBlock")
        t = params[0].get("toBlock")
        if not isinstance(f, str) or not isinstance(t, str):
            return None
        if not f.startswith("0x") or not t.startswith("0x"):
            return None
        return int(t, 16) - int(f, 16)
    except (IndexError, AttributeError, ValueError, TypeError):
        return None


def maybe_error_rate(rpc):
    """--error-rate CODE:PROB: fail eth_getLogs at random (a throttling provider)."""
    if ARGS.error_rate is None or rpc.get("method") != "eth_getLogs":
        return None
    if random.random() >= ARGS.error_rate_prob:
        return None
    STATE["drop_seen"]["error-rate"] = STATE["drop_seen"].get("error-rate", 0) + 1
    return {"jsonrpc": "2.0", "id": rpc.get("id"),
            "error": {"code": ARGS.error_rate_code, "message": ARGS.error_message or "Rate limit exceeded"}}


def wants_send_lost(rpc):
    """--send-lost PROB: forward eth_sendRawTransaction but tell the client it failed (the tx lands)."""
    return (ARGS.send_lost and rpc.get("method") == "eth_sendRawTransaction"
            and random.random() < ARGS.send_lost)


def maybe_error_range(rpc):
    if not ARGS.error_range:
        return None
    if rpc.get("method") != "eth_getLogs":
        return None
    span = hexspan(rpc.get("params", []))
    if span is None or span < ARGS.error_range_minspan:
        return None
    log("inject error %d for eth_getLogs span=%d id=%s"
        % (ARGS.error_range_code, span, rpc.get("id")))
    return {"jsonrpc": "2.0", "id": rpc.get("id"),
            "error": {"code": ARGS.error_range_code,
                      "message": ARGS.error_message or "injected long-range error"}}


def apply_drop_log(rpc, resp_obj):
    """Remove targeted logs from an eth_getLogs result in place."""
    if not ARGS.drop_specs and ARGS.drop_input_index is None:
        return resp_obj
    if rpc.get("method") != "eth_getLogs":
        return resp_obj
    result = resp_obj.get("result")
    if not isinstance(result, list):
        return resp_obj
    kept = []
    for entry in result:
        drop = False
        topics = entry.get("topics") or []
        t0 = topics[0].lower() if topics else None
        if (ARGS.drop_input_index is not None and t0 == INPUT_ADDED and len(topics) > 2
                and int(topics[2], 16) == ARGS.drop_input_index):
            STATE["drop_seen"]["input-%d" % ARGS.drop_input_index] = \
                STATE["drop_seen"].get("input-%d" % ARGS.drop_input_index, 0) + 1
            log("drop InputAdded index=%d block=%s" % (ARGS.drop_input_index, entry.get("blockNumber")))
            continue
        for spec in ARGS.drop_specs:
            if t0 != spec["topic0"]:
                continue
            seen = STATE["drop_seen"].get(spec["key"], 0)
            STATE["drop_seen"][spec["key"]] = seen + 1
            if spec["nth"] is None or spec["nth"] == seen:
                drop = True
                log("drop log topic0=%s occurrence=%d block=%s"
                    % (t0, seen, entry.get("blockNumber")))
                break
        if not drop:
            kept.append(entry)
    resp_obj["result"] = kept
    return resp_obj


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send_json(self, obj):
        data = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        if ARGS.delay_ms:
            time.sleep(ARGS.delay_ms / 1000.0)
        try:
            rpc = json.loads(body)
        except json.JSONDecodeError:
            self.wfile.write(forward(body))
            return

        rpcs = rpc if isinstance(rpc, list) else [rpc]
        for one in rpcs:
            m = one.get("method", "?")
            STATE["method_counts"][m] = STATE["method_counts"].get(m, 0) + 1
            if m == "eth_call":
                try:
                    call = one["params"][0]
                    key = (call.get("to") or "").lower() + ":" + (call.get("data") or call.get("input") or "")[:10]
                    STATE["call_counts"][key] = STATE["call_counts"].get(key, 0) + 1
                except (KeyError, IndexError, TypeError, AttributeError):
                    pass

        # Batch requests: forward wholesale, only drop-log rewriting applies.
        if isinstance(rpc, list):
            raw = forward(body)
            try:
                arr = json.loads(raw)
            except json.JSONDecodeError:
                self.wfile.write(raw)
                return
            self._send_json(arr)
            return

        err = maybe_error_rate(rpc) or maybe_error_range(rpc)
        if err is not None:
            self._send_json(err)
            return

        if wants_send_lost(rpc):
            forward(body)   # the transaction reaches the node's mempool...
            STATE["drop_seen"]["send-lost"] = STATE["drop_seen"].get("send-lost", 0) + 1
            log("send-lost: forwarded eth_sendRawTransaction id=%s, replied with an error" % rpc.get("id"))
            self._send_json({"jsonrpc": "2.0", "id": rpc.get("id"),    # ...but the client is told it failed
                             "error": {"code": -32000, "message": "request timed out"}})
            return
        raw = forward(body)
        try:
            resp_obj = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json({"jsonrpc": "2.0", "id": rpc.get("id"),
                             "error": {"code": -32000,
                                       "message": "upstream parse error"}})
            return
        resp_obj = apply_drop_log(rpc, resp_obj)
        self._send_json(resp_obj)

    def do_GET(self):
        # health / counters endpoint
        self._send_json({"ok": True, "counts": STATE["method_counts"],
                         "calls": STATE["call_counts"], "drops": STATE["drop_seen"]})


def parse_args(argv):
    p = argparse.ArgumentParser()
    p.add_argument("--listen", type=int, required=True)
    p.add_argument("--bind", default="127.0.0.1")
    p.add_argument("--upstream", default="http://127.0.0.1:8545")
    p.add_argument("--count", action="store_true")
    p.add_argument("--drop-log", action="append", default=[])
    p.add_argument("--error-range", default=None)
    p.add_argument("--delay-ms", type=int, default=0)
    p.add_argument("--send-lost", type=float, default=0.0, help="PROB of losing the reply to eth_sendRawTransaction")
    p.add_argument("--error-rate", default=None, help="CODE:PROB, fail eth_getLogs at random")
    p.add_argument("--error-message", default=None, help="message for injected errors")
    p.add_argument("--drop-input-index", type=int, default=None,
                   help="drop the InputAdded log with this global input index, every time it is served")
    a = p.parse_args(argv)
    a.drop_specs = []
    for spec in a.drop_log:
        if ":" in spec:
            topic, nth = spec.rsplit(":", 1)
            a.drop_specs.append({"topic0": topic.lower(),
                                 "nth": int(nth), "key": spec})
        else:
            a.drop_specs.append({"topic0": spec.lower(),
                                 "nth": None, "key": spec})
    if a.error_rate:
        code, prob = a.error_rate.split(":")
        a.error_rate_code = int(code)
        a.error_rate_prob = float(prob)
    if a.error_range:
        code, minspan = a.error_range.split(":")
        a.error_range_code = int(code)
        a.error_range_minspan = int(minspan)
    return a


def main():
    global ARGS
    ARGS = parse_args(sys.argv[1:])
    server = ThreadingHTTPServer((ARGS.bind, ARGS.listen), Handler)
    log("listening on %s:%d -> %s (drop=%s error_range=%s delay_ms=%d)"
        % (ARGS.bind, ARGS.listen, ARGS.upstream, ARGS.drop_log, ARGS.error_range,
           ARGS.delay_ms))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
