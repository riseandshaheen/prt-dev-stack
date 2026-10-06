#!/usr/bin/env bash
# Cross-check of qa-slingnode-v0 SLN-46 (PR #55) on prt-dev-stack, using that finding's own
# commands/rpc-fault-proxy.py unchanged. The sling is the only validator (the reference node
# is never started) and reads the chain through the proxy; one variable, REJECT_SEND, changes
# between phases:
#   TRIGGER   underpriced  -> expect: no WARN/ERROR at info, nonce 0, no CommitmentJoined
#   CONTROL B insufficient -> expect: ERROR "failed to submit joinTournament ... insufficient funds"
#   CONTROL A pass-through -> expect: the join lands (nonce > 0, CommitmentJoined)
#   TRACE     underpriced with provider=trace -> the swallowed rejection, visible only at trace
#
#   PROXY=/path/to/SLN-46-.../commands/rpc-fault-proxy.py OUT=/tmp/sln46 bash harness/qa/sln46_repro.sh
# Rebuilds the stack from scratch first (wipes sling and reference state).
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROXY="${PROXY:?path to the SLN-46 rpc-fault-proxy.py}"
OUT="${OUT:-$ROOT/harness/runs/sln46-$(date +%Y%m%d-%H%M%S)}"; mkdir -p "$OUT"
RPC=http://127.0.0.1:8545
SIGNER=0x14dC79964da2C08b23698B3D3cc7Ca32193d9955
CONSENSUS=0x570c32cb7eac495d59e30c78d775facb4d027f68
PORT=18620
cd "$ROOT"

proxy_pid=""
start_proxy(){ # <REJECT_SEND> <logname>
  [ -n "$proxy_pid" ] && kill "$proxy_pid" 2>/dev/null && wait "$proxy_pid" 2>/dev/null
  PORT=$PORT TARGET_URL=$RPC REJECT_SEND="$1" python3 "$PROXY" > "$OUT/$2" 2>&1 & proxy_pid=$!
  for _ in $(seq 1 30); do grep -q "forwarding to" "$OUT/$2" && return; sleep 0.2; done
  echo "proxy did not start"; exit 1
}
tick(){ for _ in $(seq 1 "$1"); do cast rpc anvil_mine 1 --rpc-url $RPC >/dev/null; sleep 2; done; }
tournament(){ cast call $CONSENSUS "getCurrentSealedEpoch()(uint256,uint256,uint256,address,bool,uint256,bytes32,bytes32)" --rpc-url $RPC | sed -n 4p; }
joins(){ cast logs --from-block 0 --address "$(tournament)" "CommitmentJoined(bytes32,bytes32,address)" --rpc-url $RPC 2>/dev/null | grep -c "transactionHash" || true; }
observe(){ # <label> <since>
  local logs; logs=$(docker logs --since "$2" prt-sling-node-1 2>&1)
  {
    echo "=== $1 ==="
    echo "  join actions       : $(grep -c 'submit Hero action: join tournament' <<<"$logs")"
    echo "  proxy REJECT_SEND  : $(grep -c REJECT_SEND "$OUT/$3" || true)"
    echo "  WARN/ERROR lines   : $(grep -cE ' WARN | ERROR ' <<<"$logs")"
    echo "  signer nonce       : $(cast nonce $SIGNER --rpc-url $RPC)"
    echo "  CommitmentJoined   : $(joins)  (epoch 0 root tournament $(tournament))"
  } | tee -a "$OUT/summary.txt"
  echo "$logs" > "$OUT/sling-$1.log"
}

# 0. From scratch: wipe both nodes, fresh Anvil from the dump, reference node NOT started.
( cd sling && docker compose down -v ) >/dev/null 2>&1; ( cd reference && docker compose down -v ) >/dev/null 2>&1
( cd anvil && docker compose down && docker compose up -d --wait ) >/dev/null 2>&1
cast rpc anvil_mine 2 --rpc-url $RPC >/dev/null
echo "fresh chain at block $(cast block-number --rpc-url $RPC); signer nonce $(cast nonce $SIGNER --rpc-url $RPC); joins $(joins)" | tee "$OUT/summary.txt"

# 1. TRIGGER: every eth_sendRawTransaction answered "transaction underpriced".
start_proxy underpriced proxy-trigger-underpriced.log
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose -f sling/compose.yaml -f harness/stack/sling-proxy.yaml up -d >/dev/null 2>&1
tick 30
observe TRIGGER "$since" proxy-trigger-underpriced.log

# 2. CONTROL B: a different permanent rejection, "insufficient funds".
start_proxy insufficient proxy-controlB-insufficient.log
since=$(date -u +%Y-%m-%dT%H:%M:%SZ); tick 10
observe CONTROL-B "$since" proxy-controlB-insufficient.log
grep -m1 'failed to submit joinTournament' "$OUT/sling-CONTROL-B.log" | tee -a "$OUT/summary.txt"

# 3. TRACE: back to underpriced, sling recreated at provider=trace to show the swallow line.
start_proxy underpriced proxy-trace-underpriced.log
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
RUST_LOG=info,cartesi_rollups_prt_node::provider=trace docker compose -f sling/compose.yaml \
  -f harness/stack/sling-proxy.yaml -f harness/stack/sling-trace.yaml up -d >/dev/null 2>&1
tick 10
observe TRACE "$since" proxy-trace-underpriced.log
grep -m1 'waits: the pending transaction' "$OUT/sling-TRACE.log" | tee -a "$OUT/summary.txt"

# 4. CONTROL A: pass-through, same sling at info -> the join lands.
start_proxy "" proxy-controlA-passthrough-OK.log
docker compose -f sling/compose.yaml -f harness/stack/sling-proxy.yaml up -d >/dev/null 2>&1
since=$(date -u +%Y-%m-%dT%H:%M:%SZ); tick 15
observe CONTROL-A "$since" proxy-controlA-passthrough-OK.log

kill "$proxy_pid" 2>/dev/null
docker compose -f sling/compose.yaml up -d >/dev/null 2>&1      # sling back on direct Anvil
echo "results in $OUT"
