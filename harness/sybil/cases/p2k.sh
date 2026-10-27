#!/bin/bash
# p2k.sh CASE : burst cases on the private Anvil (8546). bash.
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
source ${CASES_RUN:-/tmp/prt-cases}/env2.sh
source $S/p2/mainnet-factory-k.env; RPC=http://127.0.0.1:${CHAIN_PORT:-8546}; RPC_IN=http://host.docker.internal:${CHAIN_PORT:-8546}; FAC=$FACTORY_MAINNET_K
case=$1; log() { echo "[$case $(date +%T) block $(cast block-number -r $RPC)] $*"; }
burst() { local n=$1; while [ $n -gt 0 ]; do local st=$(( n > 5000 ? 5000 : n )); cast rpc anvil_mine $(printf '0x%x' $st) -r $RPC >/dev/null; n=$((n-st)); done; }
dep() { until mkdir $S/run/deployk.lock 2>/dev/null; do sleep 1; done; read APP CONS BLK <<<"$(deploy_app3 $1 50 "[]" $FAC)"; sleep 1; rmdir $S/run/deployk.lock; echo "$case $APP $CONS chain${CHAIN_PORT:-8546}" >> $OUT/apps.txt; log "deployed app=$APP cons=$CONS at $BLK (chain ${CHAIN_PORT:-8546}, mainnet factory)"; }
root_of() { cast logs --from-block 25 --address $1 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print("0x"+l[int(sys.argv[1])]["data"][-40:])' $2; }
deleted() { cast logs --from-block 25 --address $1 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),int(x["data"][2:66],16),int(x["data"][66:130],16)) for x in json.load(sys.stdin)])'; }
child_of() { cast logs --from-block 25 --address $1 'NewInnerTournament(bytes32,address,bytes32,bytes32,bytes32,bytes32,(bytes32,uint256,uint64,uint64,uint64,uint8,uint64,uint64),uint256)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print(" ".join("0x"+x["topics"][2][-40:]+":"+str(int(x["blockNumber"],16)) for x in l))'; }
wait_join() { for i in $(seq 1 ${2:-300}); do [ "$(docker logs rp-$1 2>&1 | grep -c 'submit Hero action: join')" -ge ${3:-1} ] && return 0; sleep 2; done; return 1; }
wait_result() { for i in $(seq 1 ${2:-240}); do docker logs syb-$1 2>&1 | grep -q '^RESULT' && return 0; sleep 10; done; return 1; }
clocks() { RPC=$RPC python3 -I - "$1" "$2" <<'PY'
import json,sys,urllib.request,subprocess,os
RPC="http://127.0.0.1:"+__import__("os").environ.get("CHAIN_PORT","8546"); T=sys.argv[1]; FROM=int(sys.argv[2]); cast=os.path.expanduser("~/.foundry/bin/cast")
def rpc(m,p): return json.loads(urllib.request.urlopen(urllib.request.Request(RPC,json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),{"content-type":"application/json"})).read())["result"]
MC=subprocess.run([cast,"keccak","MatchCreated(bytes32,bytes32,bytes32,bytes32,uint64,uint64)"],capture_output=True,text=True).stdout.strip()
MA=subprocess.run([cast,"keccak","MatchAdvanced(bytes32,bytes32,bytes32,bytes32,bytes32,uint256,uint64,uint64,uint64)"],capture_output=True,text=True).stdout.strip()
rows=[]
for l in rpc("eth_getLogs",[{"fromBlock":hex(FROM),"toBlock":"latest","address":T,"topics":[[MC,MA]]}]):
    d=l["data"][2:]; w=[d[i:i+64] for i in range(0,len(d),64)]; b=int(l["blockNumber"],16)
    rows.append((b,int(w[1],16),None) if l["topics"][0]==MC else (b,int(w[6],16),int(w[5],16)))
st={0:[],1:[]}
for i,(b,dl,h) in enumerate(rows): st[i%2].append((b,dl-b,h))
for sid,s in st.items():
    prev=None; out=[]
    for b,rem,h in s: out.append(f"blk {b} h{h if h is not None else '-'} rem {rem}"+(f" ({rem-prev:+d})" if prev is not None else "")); prev=rem
    print(f"responder stream {sid}: "+" | ".join(out))
PY
}
summarize() { log "root deleted $(deleted $1); children $(child_of $1)"; for c in $(child_of $1 | tr ' ' '\n' | cut -d: -f1); do log "child $c deleted $(deleted $c)"; done; log "sybil: $(docker logs syb-$2 2>&1 | grep -E '^RESULT' | tr '\n' ' '); sling actions: $(docker logs rp-$2 2>&1 | grep -oE 'submit Hero action: [a-z ]+' | sort | uniq -c | tr -s ' ' | tr '\n' ';')"; log "sling warn/err: $(docker logs rp-$2 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g; s/nonce [0-9]+/nonce N/; s/[0-9]+ min/N min/' | cut -c1-160 | sort | uniq -c | sort -rn | head -4 | tr '\n' ';')"; }
case $case in
k3a|k3b)  # cumulative lateness: one root advance held G+20,000, one child advance held G+30,399 (a) or G+30,401 (b)
  [ $case = k3a ] && SALT=0x0000000000000000000000000000000000000000000000000000000000000060 && H2=30424 && ACC=10 || { SALT=0x0000000000000000000000000000000000000000000000000000000000000061; H2=30426; ACC=12; }
  dep $SALT; SCHED="20025$(printf ',0%.0s' $(seq 1 29)),$H2"; proxy $case $((18630+ACC)) HOLD_SELECTOR=$ADV HOLD_SCHEDULE=$SCHED HOLD_MINE=1 UPSTREAM=$RPC; RPC_NODE=http://host.docker.internal:$((18630+ACC)) start_node3 $case $APP $ACC; wait_join $case; sleep 3
  R0=$(root_of $CONS 0); sybil $case $APP $CONS 0 $((ACC+1)) fight -e TICK=2; wait_marker syb-$case JOINED 600; log "sybil joined root $R0; schedule: 1st sling advance held 20,025 blocks, 31st held $H2 (total lateness $(( 20000 + H2 - 25 )) vs C = 50,400)"
  wait_result $case 400; python3 -I -c '
import json,sys
for l in open(sys.argv[1]):
    r=json.loads(l)
    if r.get("note","").startswith("holding") or r.get("note","")=="released sendRaw": print(r["ts"][11:], r["note"], r.get("from_block") or r.get("at_block"), (r.get("error") or {}).get("message","")[:80])' $OUT/$case-proxy.jsonl
  log "root clocks:"; clocks $R0 $BLK; for c in $(child_of $R0 | tr ' ' '\n' | cut -d: -f1); do log "child clocks:"; clocks $c $BLK; done; summarize $R0 $case;;
k7)   # offline past the whole balance on the mainnet table; divergent state settles; node exits 30
  dep 0x0000000000000000000000000000000000000000000000000000000000000062
  start_node3 k7 $APP 14; cast send --rpc-url $RPC --private-key $PK1 $INPUTBOX 'addInput(address,bytes)' $APP $(cast calldata 'emitNotice(bytes)' 0xc7) >/dev/null; wait_join k7; sleep 3; log "sling joined epoch 0; bursting 51,200 blocks"; burst 51200
  for i in $(seq 1 60); do r=$(canstage $CONS); case "$r" in true*) break;; k4)   # child-return refill: two children in sequence, bursting each child's allowance so the parent gets the return
  dep 0x0000000000000000000000000000000000000000000000000000000000000063
  start_node3 k4 $APP 16; wait_join k4; sleep 3; R0=$(root_of $CONS 0); sybil k4a $APP $CONS 0 17 fight -e TICK=2; wait_result k4a 300; log "sybil A $(docker logs syb-k4a 2>&1 | grep '^RESULT')"
  log "before the burst, root clocks:"; clocks $R0 $BLK; burst 51200; for i in $(seq 1 60); do [ "$(docker logs rp-k4 2>&1 | grep -c 'submit Hero action: propagate child')" -ge 1 ] && break; sleep 5; done; sleep 10; log "propagated A's child; root deleted $(deleted $R0)"
  sybil k4b $APP $CONS 0 18 fight -e TRANSITION=35184372088831 -e PATCH_SALT=k4b -e TICK=2; wait_marker syb-k4b JOINED 600 || log "B could not join: $(docker logs syb-k4b 2>&1 | grep -iE 'revert|error' | head -1 | cut -c1-200)"; wait_result k4b 300; log "sybil B $(docker logs syb-k4b 2>&1 | grep '^RESULT')"
  log "root clocks (stream of the sling shows the refilled clock at the second match):"; clocks $R0 $BLK; for c in $(child_of $R0 | tr ' ' '\n' | cut -d: -f1); do log "child $c clocks:"; clocks $c $BLK; done; summarize $R0 k4;;
esac; sleep 5; done; for i in $(seq 1 60); do [ "$(cast logs --from-block $BLK --address $CONS 'EpochStaged(uint256,bytes32,bytes32)' -r $RPC --json | python3 -I -c 'import sys,json;print(len(json.load(sys.stdin)))')" -ge 1 ] && break; sleep 5; done; burst 60; sleep 15; log "sealed: $(sealed_list $CONS)"
  wait_join k7 300 2; sleep 3; R1=$(root_of $CONS 1); sybil k7 $APP $CONS 1 15 fight -e WRONG_FINAL=1 -e TICK=2; wait_marker syb-k7 JOINED 600; log "wrong-final sybil joined epoch 1: $(docker logs syb-k7 2>&1 | grep -E '^(HONEST|SYBIL)' | tr '\n' ' ')"
  for i in $(seq 1 120); do [ "$(docker logs syb-k7 2>&1 | grep -c 'ACT advance')" -ge 6 ] && break; sleep 2; done; docker pause rp-k7; B0=$(cast block-number -r $RPC); log "sling paused at $B0; bursting 51,200 blocks"; burst 51200
  wait_result k7 120; log "sybil $(docker logs syb-k7 2>&1 | grep '^RESULT'); deleted $(deleted $R1); standing $(standing $R1 | tr -d '\n' | cut -c1-40)"
  PROOF=$(docker run --rm -v $P/harness/sybil:/harness:ro -v $TPL:/machine:ro $SYB /harness/validity_proof.lua /machine 2>/dev/null); log "stage: $(cast send --rpc-url $RPC --private-key $(pk 15) $CONS 'stageTournamentResult(uint256,((bytes32,bytes32[]),(bytes32,bytes32[]),(bytes32,bytes32[])))' 1 "$PROOF" --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:200])')"; burst 60; log "accept: $(cast send --rpc-url $RPC --private-key $(pk 15) $CONS 'acceptStagedTournamentResult(uint256)' 1 --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:200])')"; log "sealed: $(sealed_list $CONS)"
  docker unpause rp-k7; log "sling unpaused $(( $(cast block-number -r $RPC) - B0 )) blocks after the pause"; for i in $(seq 1 40); do st=$(docker inspect -f '{{.State.Status}}' rp-k7); [ "$st" = exited ] && break; sleep 10; done
  log "sling container: $(docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' rp-k7)"; docker logs rp-k7 2>&1 | grep -E 'ERROR|WARN' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g' | cut -c1-260 | sort | uniq -c | sort -rn | head -5;;
esac
log done
