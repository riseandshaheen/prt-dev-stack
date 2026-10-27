#!/usr/bin/env python3
# -I recommended. stdlib only.
# Fault-injecting JSON-RPC proxy for the sling-node RPC-trust lab (L4-rpc).
# Sits between the sling node and an upstream anvil. Every request/response
# pair is logged with timestamps. Behaviour is chosen by environment vars so
# one script serves every lead; copy this exact file into each candidate.
#
# Env:
#   PROXY_PORT          listen port (127.0.0.1)
#   UPSTREAM            upstream JSON-RPC url  (e.g. http://127.0.0.1:18580)
#   LOGFILE             request/response log (jsonl)
#   TAG                 free label written into the log header
#
#   Fault toggles (any combination; "control" = none set):
#   DROP_INPUT_N        drop the Nth (0-based) InputAdded log within each
#                       eth_getLogs response (counts only InputAdded logs)
#   DROP_LAST           "1": drop the LAST log of each eth_getLogs response
#   DROP_ONLY_TOBLOCK_LE  int: apply DROP_INPUT_N/DROP_LAST only when the
#                       request toBlock <= this (targets the first catch-up chunk)
#   MAXBLOCKS           int K: reject eth_getLogs spanning > K blocks
#   ERRCODE / ERRMSG    JSON-RPC error returned for the oversize range
#   STRIP_BLOCKNUMBER   "1": remove blockNumber from the first log of each
#                       eth_getLogs response
#   FREEZE_FINALIZED    int N: answer eth_getBlockByNumber("finalized") as if
#                       it were block N (rewrites the upstream call to N)
#   DROP_SENDRAW        "drop": return JSON-RPC error on eth_sendRawTransaction
#                       "500":  return HTTP 500 on eth_sendRawTransaction
#   SENDRAW_ERRCODE/MSG error for DROP_SENDRAW=drop (default -32000 / "submit refused")
#   RPCERR_METHOD       method name on which to return a 200-body JSON-RPC error
#   RPCERR_CODE/MSG     that error's code/message (e.g. eth_chainId, -32602)
#
#   Added for prt-dev-stack's funds-safety plan (E2, E5), 2026-10-22:
#   DROP_TOPIC          topic0 (0x..) of a log to omit from eth_getLogs responses
#   DROP_TOPIC_TIMES    int N: omit only the first N matching logs seen (default: every time)
#   HOLD_SENDRAW_SECS   float R: hold every eth_sendRawTransaction R seconds before forwarding it
#                       (the relay "holds a nonce"; the tx still lands afterwards)
#   HOLD_BLOCKS         int N: hold eth_sendRawTransaction until the chain head has advanced N blocks
#                       past the head at receipt (exact block lateness, whoever mines); with
#                       HOLD_SCHEDULE "n1,n2,..." the k-th held transaction waits n_k blocks and
#                       later ones are not held; HOLD_SELECTOR 0x12345678 holds only that 4-byte
#                       selector of the raw transaction's calldata (RLP-decoded, legacy or 1559)
#   HOLD_MINE           "1": the proxy itself mines the missing blocks (anvil_mine) instead of waiting
#   HOLD_UNTIL_BLOCK    int B: hold the matching eth_sendRawTransaction until the head is >= B (one-shot
#                       target instead of a relative count)
#                       A re-send of a held transaction (same sender nonce) waits for the same release.
#   PAD_REPLAY_PK       with HOLD_SELECTOR: before forwarding a held transaction, re-send its calldata
#                       (plus PAD_BYTES zero bytes) to the same contract from this key via `cast send`,
#                       and log that receipt (M5: padded-calldata farming of the per-byte refund)
import json, os, sys, time, threading, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:18580")
PORT = int(os.environ.get("PROXY_PORT", "18581"))
LOGFILE = os.environ.get("LOGFILE", "/tmp/rpc_proxy.jsonl")
TAG = os.environ.get("TAG", "")

def envi(k):
    v = os.environ.get(k)
    return int(v) if v not in (None, "") else None

DROP_INPUT_N = envi("DROP_INPUT_N")
DROP_LAST = os.environ.get("DROP_LAST") == "1"
DROP_ONLY_TOBLOCK_LE = envi("DROP_ONLY_TOBLOCK_LE")
MAXBLOCKS = envi("MAXBLOCKS")
ERRCODE = envi("ERRCODE") or -32005
ERRMSG = os.environ.get("ERRMSG", "query returned more than 10000 results")
STRIP_BLOCKNUMBER = os.environ.get("STRIP_BLOCKNUMBER") == "1"
STRIP_ONLY_FROMBLOCK_GT = envi("STRIP_ONLY_FROMBLOCK_GT")
FREEZE_FINALIZED = envi("FREEZE_FINALIZED")
DROP_SENDRAW = os.environ.get("DROP_SENDRAW")
SENDRAW_ERRCODE = envi("SENDRAW_ERRCODE") or -32000
SENDRAW_MSG = os.environ.get("SENDRAW_MSG", "submit refused")
RPCERR_METHOD = os.environ.get("RPCERR_METHOD")
REWRITE_REVERT_MSG = os.environ.get("REWRITE_REVERT_MSG")  # on eth_estimateGas, replace a revert error's message (drop "revert"), pass successes through
RPCERR_CODE = envi("RPCERR_CODE") or -32602
RPCERR_MSG = os.environ.get("RPCERR_MSG", "invalid argument")
INPUTADDED = "0xc05d337121a6e8605c6ec0b72aa29c4210ffe6e5b9cefdd6a7058188a8f66f98"
DROP_TOPIC = (os.environ.get("DROP_TOPIC") or "").lower() or None
DROP_TOPIC_TIMES = envi("DROP_TOPIC_TIMES")
HOLD_SENDRAW_SECS = float(os.environ.get("HOLD_SENDRAW_SECS") or 0)
HOLD_BLOCKS = envi("HOLD_BLOCKS")
# schedule entries: an integer N (hold N blocks) or "d<+/-n>" (hold until the latest responderDeadline
# of the transaction's target tournament plus n, read from its last MatchAdvanced/MatchCreated log)
HOLD_SCHEDULE = [ (x.strip() if x.strip().startswith("d") else int(x)) for x in os.environ.get("HOLD_SCHEDULE", "").split(",") if x.strip()]
MA_TOPIC = "0x7f64b1b1c9315d0c13cf8aab2cec06ca48d1e7bccd4b409bb8cd0774647bc67e"
MC_TOPIC = "0xb98467806859c2b452dbfe60905ffe650a4ac0bada72cbe887f71bdb014f8d7f"
def _latest_deadline(address):
    head = _head()
    q = {"jsonrpc":"2.0","id":97,"method":"eth_getLogs","params":[{"fromBlock":hex(max(0, head-5000)),"toBlock":"latest","address":address,"topics":[[MA_TOPIC, MC_TOPIC]]}]}
    logs = json.loads(upstream_call(json.dumps(q).encode()))["result"]
    if not logs: return None
    l = logs[-1]; d = l["data"][2:]; w = [d[i:i+64] for i in range(0, len(d), 64)]
    return int(w[6], 16) if l["topics"][0].lower() == MA_TOPIC else int(w[1], 16)
HOLD_SELECTOR = (os.environ.get("HOLD_SELECTOR") or "").lower() or None
HOLD_MINE = os.environ.get("HOLD_MINE") == "1"
_holds = {"n": 0}
PAD_REPLAY_PK = os.environ.get("PAD_REPLAY_PK")
HOLD_UNTIL_BLOCK = envi("HOLD_UNTIL_BLOCK")
_hold_targets = {}   # sender nonce -> target block, so a node's re-send of a held tx waits for the same release
PAD_BYTES = int(os.environ.get("PAD_BYTES") or 0)

def _rlp_calldata(raw_hex):
    """Selector of a raw signed tx (legacy or typed 0x01/0x02): best effort, None on failure."""
    try:
        b = bytes.fromhex(raw_hex[2:] if raw_hex.startswith("0x") else raw_hex)
        if b and b[0] in (1, 2, 3):
            b = b[1:]
        def rlp(buf, i=0):
            p = buf[i]
            if p < 0x80: return buf[i:i+1], i+1
            if p < 0xb8: n = p-0x80; return buf[i+1:i+1+n], i+1+n
            if p < 0xc0: l = p-0xb7; n = int.from_bytes(buf[i+1:i+1+l], "big"); return buf[i+1+l:i+1+l+n], i+1+l+n
            if p < 0xf8: n = p-0xc0; end = i+1+n; items=[]; j=i+1
            else: l = p-0xf7; n = int.from_bytes(buf[i+1:i+1+l], "big"); end=i+1+l+n; items=[]; j=i+1+l
            while j < end:
                it, j = rlp(buf, j); items.append(it)
            return items, end
        items, _ = rlp(b)
        # legacy: nonce,gasPrice,gas,to,value,data,...  1559: chainId,nonce,maxPrio,maxFee,gas,to,value,data,...
        data = items[5] if len(items) in (9,) else items[7]
        to = items[3] if len(items) in (9,) else items[5]
        nonce = items[0] if len(items) in (9,) else items[1]
        _rlp_calldata.last = ("0x" + to.hex(), "0x" + data.hex(), int.from_bytes(nonce, "big") if nonce else 0)
        return "0x" + data[:4].hex() if len(data) >= 4 else None
    except Exception:
        return None

def _head():
    return int(json.loads(upstream_call(json.dumps({"jsonrpc":"2.0","id":99,"method":"eth_blockNumber","params":[]}).encode()))["result"], 16)

_lock = threading.Lock()
_counters = {"getlogs": 0, "dropped": 0, "topic_dropped": 0}

def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S") + ".%03d" % (int(time.time()*1000)%1000)
    with _lock:
        with open(LOGFILE, "a") as f:
            f.write(json.dumps(rec) + "\n")

def upstream_call(raw):
    req = urllib.request.Request(UPSTREAM, data=raw,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def hexint(x):
    if isinstance(x, int): return x
    if isinstance(x, str) and x.startswith("0x"): return int(x, 16)
    return None

def handle_one(obj, raw_single):
    method = obj.get("method")
    rid = obj.get("id")
    params = obj.get("params") or []

    # 200-body JSON-RPC error on a chosen method (e.g. startup eth_chainId)
    if RPCERR_METHOD and method == RPCERR_METHOD:
        log({"dir": "inject", "method": method, "note": "200-body jsonrpc error",
             "code": RPCERR_CODE})
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": RPCERR_CODE, "message": RPCERR_MSG}}

    # estimateGas: forward; if upstream returns a revert error, reword it to drop "revert"
    if method == "eth_estimateGas" and REWRITE_REVERT_MSG is not None:
        up = json.loads(upstream_call(raw_single))
        err = up.get("error") if isinstance(up, dict) else None
        if err and "revert" in str(err.get("message", "")).lower():
            old = err["message"]; err["message"] = REWRITE_REVERT_MSG
            log({"dir": "inject", "method": method, "note": "revert message reworded",
                 "from": old[:60], "to": REWRITE_REVERT_MSG})
        return up

    # sendRawTransaction held N blocks, then forwarded
    if method == "eth_sendRawTransaction" and (HOLD_BLOCKS is not None or HOLD_SCHEDULE or HOLD_UNTIL_BLOCK is not None):
        sel = _rlp_calldata(params[0]) if params else None
        nonce = _rlp_calldata.last[2] if hasattr(_rlp_calldata, "last") else None
        with _lock:
            same = _hold_targets.get(nonce)
        if same is not None:
            log({"dir": "inject", "method": method, "note": "re-send of a held tx waits for the same release", "nonce": nonce, "target": same})
            while _head() < same:
                time.sleep(0.2)
            up = json.loads(upstream_call(raw_single))
            log({"dir": "inject", "method": method, "note": "released re-send", "nonce": nonce, "at_block": _head(), "result": up.get("result"), "error": up.get("error")})
            return up
        if HOLD_SELECTOR is None or sel == HOLD_SELECTOR:
            with _lock:
                k = _holds["n"]; _holds["n"] += 1
            if HOLD_UNTIL_BLOCK is not None:
                n = max(0, HOLD_UNTIL_BLOCK - _head()) if k == 0 else None
            else:
                n = HOLD_SCHEDULE[k] if HOLD_SCHEDULE and k < len(HOLD_SCHEDULE) else (HOLD_BLOCKS if not HOLD_SCHEDULE else None)
            if PAD_REPLAY_PK and sel == HOLD_SELECTOR:
                import subprocess
                to, data, _nonce = _rlp_calldata.last
                cast = os.path.expanduser("~/.foundry/bin/cast")
                r = subprocess.run([cast, "send", "--rpc-url", UPSTREAM, "--private-key", PAD_REPLAY_PK, "--gas-limit", "12000000",
                                    "--json", to, data + "00" * PAD_BYTES], capture_output=True, text=True)
                log({"dir": "inject", "method": method, "note": "padded replay", "to": to, "bytes": len(data)//2 - 1, "pad": PAD_BYTES,
                     "stdout": r.stdout, "stderr": r.stderr[-600:]})
            if isinstance(n, str):
                to = _rlp_calldata.last[0]
                dl = _latest_deadline(to)
                b0 = _head()
                off = int(n[1:])
                n = (dl + off - b0) if dl is not None else 0
                log({"dir": "inject", "method": method, "note": "dynamic hold", "to": to, "deadline": dl, "offset": off, "blocks": n, "target": (dl + off) if dl else None})
            if n is not None and n > 0:
                b0 = _head()
                with _lock:
                    _hold_targets[nonce] = b0 + n
                log({"dir": "inject", "method": method, "note": "holding sendRaw %d blocks" % n, "selector": sel, "from_block": b0, "target": b0 + n, "k": k, "nonce": nonce})
                if HOLD_MINE:
                    left = n
                    while left > 0:
                        step = min(left, 500)
                        upstream_call(json.dumps({"jsonrpc":"2.0","id":98,"method":"anvil_mine","params":[hex(step)]}).encode()); left -= step
                else:
                    while _head() < b0 + n:
                        time.sleep(0.2)
                up = json.loads(upstream_call(raw_single))
                log({"dir": "inject", "method": method, "note": "released sendRaw", "selector": sel, "at_block": _head(), "held_blocks": n, "result": up.get("result"), "error": up.get("error")})
                return up

    # sendRawTransaction held for R seconds, then forwarded
    if method == "eth_sendRawTransaction" and HOLD_SENDRAW_SECS > 0:
        log({"dir": "inject", "method": method, "note": "holding sendRaw %.1fs" % HOLD_SENDRAW_SECS})
        time.sleep(HOLD_SENDRAW_SECS)
        up = json.loads(upstream_call(raw_single))
        log({"dir": "inject", "method": method, "note": "released sendRaw", "result": up.get("result"), "error": up.get("error")})
        return up

    # sendRawTransaction drop / 500
    if method == "eth_sendRawTransaction" and DROP_SENDRAW:
        if DROP_SENDRAW == "500":
            raise _Http500()
        log({"dir": "inject", "method": method, "note": "drop sendRaw"})
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": SENDRAW_ERRCODE, "message": SENDRAW_MSG}}

    # freeze finalized: rewrite the block tag to a fixed number
    if method == "eth_getBlockByNumber" and FREEZE_FINALIZED is not None \
            and params and params[0] == "finalized":
        newp = [hex(FREEZE_FINALIZED)] + params[1:]
        newobj = dict(obj); newobj["params"] = newp
        up = upstream_call(json.dumps(newobj).encode())
        log({"dir": "inject", "method": method,
             "note": "finalized frozen at %d" % FREEZE_FINALIZED})
        return json.loads(up)

    # eth_getLogs range cap
    if method == "eth_getLogs" and params:
        flt = params[0]
        fb = hexint(flt.get("fromBlock")); tb = hexint(flt.get("toBlock"))
        if MAXBLOCKS is not None and fb is not None and tb is not None \
                and (tb - fb + 1) > MAXBLOCKS:
            log({"dir": "inject", "method": method, "from": fb, "to": tb,
                 "span": tb-fb+1, "note": "range>cap rejected code=%d" % ERRCODE})
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": ERRCODE, "message": ERRMSG}}

    # pass to upstream
    up = json.loads(upstream_call(raw_single))

    # mutate eth_getLogs responses
    if method == "eth_getLogs" and isinstance(up.get("result"), list):
        with _lock:
            _counters["getlogs"] += 1
        flt = params[0] if params else {}
        tb = hexint(flt.get("toBlock"))
        gate_ok = (DROP_ONLY_TOBLOCK_LE is None) or \
                  (tb is not None and tb <= DROP_ONLY_TOBLOCK_LE)
        res = up["result"]
        before = len(res)
        if gate_ok and (DROP_INPUT_N is not None) and res:
            ia_idx = [i for i, l in enumerate(res)
                      if (l.get("topics") or [""])[0].lower() == INPUTADDED]
            if DROP_INPUT_N < len(ia_idx):
                drop_at = ia_idx[DROP_INPUT_N]
                dropped = res.pop(drop_at)
                _counters["dropped"] += 1
                log({"dir": "inject", "method": method, "from": hexint(flt.get("fromBlock")),
                     "to": tb, "note": "dropped InputAdded #%d block=%s" %
                     (DROP_INPUT_N, dropped.get("blockNumber"))})
        if gate_ok and DROP_LAST and res:
            ia_idx = [i for i, l in enumerate(res)
                      if (l.get("topics") or [""])[0].lower() == INPUTADDED]
            if ia_idx:
                drop_at = ia_idx[-1]
                dropped = res.pop(drop_at)
                _counters["dropped"] += 1
                log({"dir": "inject", "method": method, "to": tb,
                     "note": "dropped LAST InputAdded block=%s" % dropped.get("blockNumber")})
        if DROP_TOPIC and res:
            with _lock:
                budget_left = DROP_TOPIC_TIMES is None or _counters["topic_dropped"] < DROP_TOPIC_TIMES
            if budget_left:
                idx = [i for i, l in enumerate(res) if (l.get("topics") or [""])[0].lower() == DROP_TOPIC]
                if idx:
                    dropped = res.pop(idx[0])
                    with _lock:
                        _counters["topic_dropped"] += 1
                    log({"dir": "inject", "method": method, "from": hexint(flt.get("fromBlock")), "to": tb,
                         "note": "dropped log topic %s block=%s address=%s" % (DROP_TOPIC[:10], dropped.get("blockNumber"), dropped.get("address"))})
        strip_gate = (STRIP_ONLY_FROMBLOCK_GT is None) or \
                     (tb is not None and tb > STRIP_ONLY_FROMBLOCK_GT)
        if STRIP_BLOCKNUMBER and strip_gate and res:
            if "blockNumber" in res[0]:
                del res[0]["blockNumber"]
                log({"dir": "inject", "method": method,
                     "note": "stripped blockNumber from first log"})
        up["result"] = res
        if before != len(res):
            log({"dir": "note", "method": method, "result_before": before,
                 "result_after": len(res)})
    return up


class _Http500(Exception):
    pass


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n)
        try:
            payload = json.loads(raw)
        except Exception as e:
            self.send_error(400, "bad json"); return
        is_batch = isinstance(payload, list)
        reqs = payload if is_batch else [payload]
        methods = [r.get("method") for r in reqs]
        log({"dir": "req", "methods": methods, "batch": is_batch, "bytes": len(raw)})
        try:
            if is_batch:
                out = [handle_one(o, json.dumps(o).encode()) for o in reqs]
                body = json.dumps(out).encode()
            else:
                out = handle_one(payload, raw)
                body = json.dumps(out).encode()
        except _Http500:
            log({"dir": "inject", "methods": methods, "note": "HTTP 500 on sendRaw"})
            self.send_response(500); self.send_header("Content-Length", "0")
            self.end_headers(); return
        except Exception as e:
            log({"dir": "error", "methods": methods, "err": repr(e)})
            self.send_response(502); self.send_header("Content-Length", "0")
            self.end_headers(); return
        # brief response summary
        def summ(o):
            if isinstance(o, dict) and "error" in o: return {"error": o["error"]}
            if isinstance(o, dict) and isinstance(o.get("result"), list):
                return {"result_len": len(o["result"])}
            return {"ok": True}
        log({"dir": "resp", "methods": methods,
             "summary": [summ(o) for o in (out if is_batch else [out])]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    log({"dir": "start", "tag": TAG, "port": PORT, "upstream": UPSTREAM,
         "faults": {k: os.environ.get(k) for k in
            ["DROP_INPUT_N","DROP_LAST","DROP_ONLY_TOBLOCK_LE","MAXBLOCKS","ERRCODE",
             "STRIP_BLOCKNUMBER","FREEZE_FINALIZED","DROP_SENDRAW","RPCERR_METHOD","RPCERR_CODE","REWRITE_REVERT_MSG",
             "DROP_TOPIC","DROP_TOPIC_TIMES","HOLD_SENDRAW_SECS","HOLD_BLOCKS","HOLD_SCHEDULE","HOLD_SELECTOR","HOLD_MINE"]}})
    srv = ThreadingHTTPServer((os.environ.get("BIND","127.0.0.1"), PORT), H)
    print("proxy on 127.0.0.1:%d -> %s tag=%s" % (PORT, UPSTREAM, TAG), flush=True)
    srv.serve_forever()
