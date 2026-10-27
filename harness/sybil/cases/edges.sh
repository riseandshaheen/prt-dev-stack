#!/bin/bash
# edges.sh CASE : the 2026-10-22 edge cases. bash only (array indexing).
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
source ${CASES_RUN:-/tmp/prt-cases}/env2.sh
case=$1; log() { echo "[$case $(date +%T) block $(cast block-number -r $RPC)] $*"; }
dep() { until mkdir $S/run/deploy.lock 2>/dev/null; do sleep 1; done; read APP CONS BLK <<<"$(deploy_app2 $1 ${2:-50} "${3:-[]}")"; mine 2; sleep 2; rmdir $S/run/deploy.lock; echo "$case $APP $CONS" >> $OUT/apps.txt; log "deployed app=$APP cons=$CONS at $BLK"; }
root_of() { cast logs --from-block 25 --address $1 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print("0x"+l[int(sys.argv[1])]["data"][-40:])' $2; }
evcount() { cast logs --from-block 25 --address $1 "$2" -r $RPC --json | python3 -I -c 'import sys,json;print(len(json.load(sys.stdin)))'; }
case $case in
c1long)
  dep 0x0000000000000000000000000000000000000000000000000000000000000030
  start_node2 c1long $APP 21; addinput $APP $(cast calldata 'burnCycles(uint64,uint8)' 1200000 0)
  wait_sealed_n $CONS 2 600 || { log "epoch 1 never sealed"; exit 1; }; log "epoch 1 sealed: $(sealed_list $CONS)"
  for i in $(seq 1 300); do [ "$(docker logs rp-c1long 2>&1 | grep -c 'submit Hero action: join')" -ge 2 ] && break; sleep 2; done
  R1=$(root_of $CONS 1); JOINS=$(cast logs --from-block 25 --address $R1 'CommitmentJoined(bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print([l["topics"][1] for l in json.load(sys.stdin)])'); log "sling joined epoch 1 root $R1 with $JOINS"
  T0=$(date +%s); sybil c1long $APP $CONS 1 22 fight -e TRANSITION=4398046511103
  wait_marker syb-c1long HONEST 900; log "oracle HONEST $(docker logs syb-c1long 2>&1 | grep '^HONEST') after $(( $(date +%s) - T0 )) s; inputs: $(docker logs syb-c1long 2>&1 | grep 'consumed')"
  wait_marker syb-c1long JOINED 900; log "sybil joined after $(( $(date +%s) - T0 )) s"
  for i in $(seq 1 720); do docker logs syb-c1long 2>&1 | grep -q '^RESULT' && break; sleep 10; done
  log "sybil $(docker logs syb-c1long 2>&1 | grep '^RESULT') after $(( $(date +%s) - T0 )) s"
  CH=$(cast logs --from-block 25 --address $R1 'NewInnerTournament(bytes32,address,bytes32,bytes32,bytes32,bytes32,(bytes32,uint256,uint64,uint64,uint64,uint8,uint64,uint64),uint256)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print(("0x"+l[0]["topics"][2][-40:]+" "+str(int(l[0]["blockNumber"],16))) if l else "none")')
  log "child $CH"; read CA CB <<<"$CH"; [ "$CA" != none ] && cast logs --from-block 25 --address $CA 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;[print("child MatchDeleted block",int(x["blockNumber"],16),"reason",int(x["data"][2:66],16),"winner",int(x["data"][66:130],16)) for x in json.load(sys.stdin)]'
  docker logs rp-c1long 2>&1 | grep -E 'computing quartet|prepared (SealLeaf|ProveLeaf|Advance)' | grep -E 'stride 2\^0|SealLeaf|ProveLeaf' | tail -6 | cut -c1-200
  wait_sealed_n $CONS 3 400 && log "epoch 2 sealed: $(sealed_list $CONS)";;
c6)
  dep 0x0000000000000000000000000000000000000000000000000000000000000031
  start_node2 c6 $APP 23; addinput $APP $(cast calldata 'emitNotice(bytes)' 0xc6)
  wait_sealed_n $CONS 2 600 || exit 1; log "epoch 1 sealed: $(sealed_list $CONS)"
  for i in $(seq 1 300); do [ "$(docker logs rp-c6 2>&1 | grep -c 'submit Hero action: join')" -ge 2 ] && break; sleep 2; done; sleep 3
  sybil c6 $APP $CONS 1 24 fight -e WRONG_FINAL=1; wait_marker syb-c6 JOINED 600; log "sybil joined: $(docker logs syb-c6 2>&1 | grep -E '^(HONEST|SYBIL)' | tr '\n' ' ')"
  for i in $(seq 1 120); do [ "$(docker logs syb-c6 2>&1 | grep -c 'ACT advance')" -ge 10 ] && break; sleep 2; done
  docker pause rp-c6; log "sling PAUSED after $(docker logs syb-c6 2>&1 | grep -c 'ACT advance') sybil advances"
  R1=$(root_of $CONS 1); for i in $(seq 1 120); do docker logs syb-c6 2>&1 | grep -q 'ACT claim_timeout' && break; sleep 10; done
  log "sybil claimed timeout: $(docker logs syb-c6 2>&1 | grep -c 'ACT claim_timeout'); MatchDeleted: $(cast logs --from-block 25 --address $R1 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),int(x["data"][2:66],16),int(x["data"][66:130],16)) for x in json.load(sys.stdin)])')"
  for i in $(seq 1 120); do docker logs syb-c6 2>&1 | grep -q '^RESULT' && break; sleep 10; done; log "sybil $(docker logs syb-c6 2>&1 | grep '^RESULT'); root standing $(standing $R1 | tr -d '\n' | cut -c1-80)"
  docker unpause rp-c6; log "sling UNPAUSED"; sleep 30; docker logs rp-c6 2>&1 | grep -E 'ERROR|WARN' | grep -v DEBUG | tail -3 | cut -c1-250
  for i in $(seq 1 100); do r=$(canstage $CONS); case "$r" in true*) break;; esac; sleep 10; done; log "canstage: $(canstage $CONS | cut -c1-100)"
  PROOF=$(docker run --rm -v $P/harness/sybil:/harness:ro -v $TPL:/machine:ro $SYB /harness/validity_proof.lua /machine 2>/dev/null)
  log "third party stages the sybil's result: $(cast send --rpc-url $RPC --private-key $(pk 24) $CONS 'stageTournamentResult(uint256,((bytes32,bytes32[]),(bytes32,bytes32[]),(bytes32,bytes32[])))' 1 "$PROOF" --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:300])')"
  sleep 60; log "accept: $(cast send --rpc-url $RPC --private-key $(pk 24) $CONS 'acceptStagedTournamentResult(uint256)' 1 --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:300])')"; log "sealed: $(sealed_list $CONS)"
  for i in $(seq 1 30); do st=$(docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' rp-c6); case "$st" in exited*) break;; esac; sleep 10; done
  log "sling container: $(docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' rp-c6)"; docker logs rp-c6 2>&1 | grep -E 'ERROR' | grep -v DEBUG | tail -4 | cut -c1-300;;
multi)
  dep 0x0000000000000000000000000000000000000000000000000000000000000032
  start_node2 multi $APP 25; for i in $(seq 1 300); do docker logs rp-multi 2>&1 | grep -q 'submit Hero action: join' && break; sleep 2; done; sleep 3
  T0=$(date +%s); sybil multi1 $APP $CONS 0 26 fight; sybil multi2 $APP $CONS 0 27 fight -e TRANSITION=35184372088831 -e PATCH_SALT=m2; sybil multi3 $APP $CONS 0 28 fight -e TRANSITION=70368744177663 -e PATCH_SALT=m3
  for n in 1 2 3; do wait_marker syb-multi$n JOINED 600; done; log "all three joined after $(( $(date +%s) - T0 )) s"
  R0=$(root_of $CONS 0); for i in $(seq 1 240); do a=0; for n in 1 2 3; do docker logs syb-multi$n 2>&1 | grep -q '^RESULT' && a=$((a+1)); done; [ $a -ge 3 ] && break; sleep 10; done
  log "sybils: $(for n in 1 2 3; do docker logs syb-multi$n 2>&1 | grep '^RESULT' | tr '\n' ' '; done) after $(( $(date +%s) - T0 )) s"
  log "root matches $(evcount $R0 'MatchCreated(bytes32,bytes32,bytes32,bytes32,uint64,uint64)') deleted $(cast logs --from-block 25 --address $R0 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),int(x["data"][2:66],16),int(x["data"][66:130],16)) for x in json.load(sys.stdin)])') children $(evcount $R0 'NewInnerTournament(bytes32,address,bytes32,bytes32,bytes32,bytes32,(bytes32,uint256,uint64,uint64,uint64,uint8,uint64,uint64),uint256)')"
  log "sling actions: $(docker logs rp-multi 2>&1 | grep -oE 'submit Hero action: [a-z ]+' | sort | uniq -c | tr '\n' ';')"; docker logs rp-multi 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g' | sort | uniq -c | sort -rn | head -3 | cut -c1-200
  wait_sealed_n $CONS 2 400 && log "epoch 1 sealed: $(sealed_list $CONS) after $(( $(date +%s) - T0 )) s";;
h1d)
  dep 0x0000000000000000000000000000000000000000000000000000000000000034
  start_node2 h1da $APP 31; start_node2 h1db $APP 31; for i in $(seq 1 300); do docker logs rp-h1da 2>&1 | grep -q 'submit Hero action: join' && break; sleep 2; done; sleep 3
  sybil h1d $APP $CONS 0 32 fight; wait_marker syb-h1d JOINED 600; log "sybil joined"
  for i in $(seq 1 240); do docker logs syb-h1d 2>&1 | grep -q '^RESULT' && break; sleep 10; done; log "sybil $(docker logs syb-h1d 2>&1 | grep '^RESULT')"
  for n in h1da h1db; do log "$n: actions $(docker logs rp-$n 2>&1 | grep -c 'submit Hero action') warn/err: $(docker logs rp-$n 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g; s/nonce [0-9]+/nonce N/' | cut -c1-160 | sort | uniq -c | sort -rn | head -3 | tr '\n' ';')"; done
  python3 -I - <<PY
import json,urllib.request
RPC="$RPC"; A="$(addr 31)".lower()
def rpc(m,p): return json.loads(urllib.request.urlopen(urllib.request.Request(RPC,json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),{"content-type":"application/json"})).read())["result"]
head=int(rpc("eth_blockNumber",[]),16); ok=fail=0
for b in range($BLK,head+1):
    for tx in rpc("eth_getBlockByNumber",[hex(b),True])["transactions"]:
        if tx["from"].lower()==A:
            s=int(rpc("eth_getTransactionReceipt",[tx["hash"]])["status"],16); ok+=s; fail+=1-s
print("shared-key txs ok",ok,"reverted",fail)
PY
  wait_sealed_n $CONS 2 400 && log "epoch 1 sealed: $(sealed_list $CONS)";;
c7s)
  SENTRY=$(addr 33); dep 0x0000000000000000000000000000000000000000000000000000000000000035 50 "[$SENTRY]"; log "sentry $SENTRY (account 33), no node"
  addinput $APP $(cast calldata 'emitNotice(bytes)' 0xc7)
  sybil c7s0 $APP $CONS 0 34 honest; wait_marker syb-c7s0 JOINED 600; R0=$(root_of $CONS 0)
  for i in $(seq 1 120); do r=$(canstage $CONS); case "$r" in true*) break;; esac; sleep 10; done
  PROOF=$(docker run --rm -v $P/harness/sybil:/harness:ro -v $TPL:/machine:ro $SYB /harness/validity_proof.lua /machine 2>/dev/null)
  cast send --rpc-url $RPC --private-key $(pk 34) $CONS 'stageTournamentResult(uint256,((bytes32,bytes32[]),(bytes32,bytes32[]),(bytes32,bytes32[])))' 0 "$PROOF" >/dev/null 2>&1; log "epoch 0 staged by account 34; canaccept $(cast call $CONS 'canAcceptStagedTournamentResult()(bool,bool,bool,uint256,bytes32,bytes32)' -r $RPC | tr '\n' ' ' | cut -c1-40)"
  sleep 60; cast send --rpc-url $RPC --private-key $(pk 34) $CONS 'acceptStagedTournamentResult(uint256)' 0 >/dev/null 2>&1; log "accepted: $(sealed_list $CONS)"
  sybil c7s1 $APP $CONS 1 34 fight -e WRONG_FINAL=1; wait_marker syb-c7s1 JOINED 600; log "wrong-final sybil joined epoch 1: $(docker logs syb-c7s1 2>&1 | grep -E '^(HONEST|SYBIL)' | tr '\n' ' ')"
  for i in $(seq 1 120); do r=$(canstage $CONS); case "$r" in true*) break;; esac; sleep 10; done
  log "stage wrong result: $(cast send --rpc-url $RPC --private-key $(pk 34) $CONS 'stageTournamentResult(uint256,((bytes32,bytes32[]),(bytes32,bytes32[]),(bytes32,bytes32[])))' 1 "$PROOF" --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:300])')"
  start_node2 c7s $APP 33; log "sentry node started late"; sleep 90
  docker logs rp-c7s 2>&1 | grep -v DEBUG | grep -iE 'sentry|claim|dissent|stage|ERROR|WARN' | head -8 | cut -c1-250
  log "SentryClaim events: $(cast logs --from-block 25 --address $CONS 'SentryClaim(uint256,uint256,address,bytes32)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),x["data"][-64:][:12]) for x in json.load(sys.stdin)])'); canaccept $(cast call $CONS 'canAcceptStagedTournamentResult()(bool,bool,bool,uint256,bytes32,bytes32)' -r $RPC | tr '\n' ' ' | cut -c1-30)"
  log "accept by account 34 after staging period: $(cast send --rpc-url $RPC --private-key $(pk 34) $CONS 'acceptStagedTournamentResult(uint256)' 1 --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:200])')"; log "sealed: $(sealed_list $CONS)"; sleep 60; docker logs rp-c7s 2>&1 | grep -v DEBUG | tail -3 | cut -c1-250; log "sentry container: $(docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' rp-c7s)";;
esac
log done
