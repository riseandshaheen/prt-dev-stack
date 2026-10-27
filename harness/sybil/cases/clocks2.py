#!/usr/bin/env python3
"""clocks.py TOURNAMENT FROM_BLOCK : per-move clock accounting from MatchCreated/MatchAdvanced events.
Prints, for each advance, the block, the remaining clock of the side that just moved (its new deadline
appears in the NEXT event where it is the responder), and the delta versus its previous remaining."""
import json, sys, urllib.request
RPC = "http://127.0.0.1:8546"; T = sys.argv[1]; FROM = int(sys.argv[2])
def rpc(m, p): return json.loads(urllib.request.urlopen(urllib.request.Request(RPC, json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(), {"content-type": "application/json"})).read())["result"]
def topic(sig):
    import subprocess; return subprocess.run(["cast", "sig-event", sig], capture_output=True, text=True).stdout.strip()
MC = "0x" + __import__("hashlib").sha3_256(b"").hexdigest()[:0]  # placeholder, filled below
import subprocess
MC = subprocess.run([__import__("os").path.expanduser("~/.foundry/bin/cast"), "keccak", "MatchCreated(bytes32,bytes32,bytes32,bytes32,uint64,uint64)"], capture_output=True, text=True).stdout.strip()
MA = subprocess.run([__import__("os").path.expanduser("~/.foundry/bin/cast"), "keccak", "MatchAdvanced(bytes32,bytes32,bytes32,bytes32,bytes32,uint256,uint64,uint64,uint64)"], capture_output=True, text=True).stdout.strip()
logs = rpc("eth_getLogs", [{"fromBlock": hex(FROM), "toBlock": "latest", "address": T, "topics": [[MC, MA]]}])
rows = []
for l in logs:
    d = l["data"][2:]; w = [d[i:i+64] for i in range(0, len(d), 64)]; b = int(l["blockNumber"], 16)
    if l["topics"][0] == MC:
        rows.append(("created", b, int(w[1], 16), None))
    else:
        rows.append(("advanced", b, int(w[6], 16), int(w[5], 16)))
# responders alternate; the event's deadline belongs to the responder named by the event (the side to move next)
# remaining of the side that must respond = deadline - block. Track two streams by alternation.
streams = {0: [], 1: []}
for i, (kind, b, deadline, height) in enumerate(rows):
    streams[i % 2].append((b, deadline - b, height))
for sid, st in streams.items():
    prev = None; out = []
    for b, rem, h in st:
        out.append(f"blk {b} h{h if h is not None else '-'} rem {rem}" + (f" ({rem - prev:+d})" if prev is not None else ""))
        prev = rem
    print(f"responder stream {sid}: " + " | ".join(out))
