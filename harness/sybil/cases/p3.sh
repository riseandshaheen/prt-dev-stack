#!/bin/bash
# p3.sh CASE : fourth pass, all on the private Anvil 8546, one case at a time, volumes removed at the end. bash.
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
source ${CASES_RUN:-/tmp/prt-cases}/env2.sh
source $S/p2/mainnet-factory-k.env; RPC=http://127.0.0.1:8546; RPC_IN=http://host.docker.internal:8546; FAC=$FACTORY_MAINNET_K
case=$1; log() { echo "[$case $(date +%T) block $(cast block-number -r $RPC)] $*"; }
step_mine() { # step_mine N [containers to pause]: advance N blocks in steps of 500 with the named containers paused
  local n=$1; shift; for c in "$@"; do docker pause $c >/dev/null 2>&1; done
  while [ $n -gt 0 ]; do local st=$(( n > 500 ? 500 : n )); cast rpc anvil_mine $(printf '0x%x' $st) -r $RPC >/dev/null; n=$((n-st)); sleep 0.2; done
  for c in "$@"; do docker unpause $c >/dev/null 2>&1; done; }
dep() { read APP CONS BLK <<<"$(deploy_app3 $1 50 "[]" ${2:-$FACTORY})"; echo "$case $APP $CONS chain8546" >> $OUT/apps.txt; log "deployed app=$APP cons=$CONS at $BLK ($([ "${2:-$FACTORY}" = "$FAC" ] && echo mainnet || echo devnet) factory, chain 8546)"; }
root_of() { cast logs --from-block 25 --address $1 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print("0x"+l[int(sys.argv[1])]["data"][-40:])' $2; }
deleted() { cast logs --from-block 25 --address $1 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),int(x["data"][2:66],16),int(x["data"][66:130],16),x["transactionHash"][:12]) for x in json.load(sys.stdin)])'; }
child_of() { cast logs --from-block 25 --address $1 'NewInnerTournament(bytes32,address,bytes32,bytes32,bytes32,bytes32,(bytes32,uint256,uint64,uint64,uint64,uint8,uint64,uint64),uint256)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print(" ".join("0x"+x["topics"][2][-40:]+":"+str(int(x["blockNumber"],16)) for x in l))'; }
wait_join() { for i in $(seq 1 ${2:-300}); do [ "$(docker logs rp-$1 2>&1 | grep -c 'submit Hero action: join')" -ge ${3:-1} ] && return 0; sleep 2; done; return 1; }
wait_result() { for i in $(seq 1 ${2:-240}); do docker logs syb-$1 2>&1 | grep -q '^RESULT' && return 0; sleep 10; done; return 1; }
clocks() { CHAIN_PORT=8546 python3 -I $S/run/clocks2.py "$1" "$2"; }
summarize() { log "root deleted $(deleted $1); children $(child_of $1)"; for c in $(child_of $1 | tr ' ' '\n' | cut -d: -f1); do log "child $c deleted $(deleted $c)"; done; log "sybil: $(for n in $2; do docker logs syb-$n 2>&1 | grep -E '^RESULT' | tr '\n' ' '; done); sling actions: $(docker logs rp-$3 2>&1 | grep -oE 'submit Hero action: [a-z ]+' | sort | uniq -c | tr -s ' ' | tr '\n' ';')"; log "sling warn/err: $(docker logs rp-$3 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g; s/nonce [0-9]+/nonce N/; s/[0-9]+ min/N min/' | cut -c1-170 | sort | uniq -c | sort -rn | head -4 | tr '\n' ';')"; }
cleanup() { docker ps -a --format '{{.Names}}' | grep -E '^(rp-|syb-)' | xargs -r docker rm -f >/dev/null 2>&1; docker volume ls -q | grep '^vol-' | xargs -r docker volume rm >/dev/null 2>&1; pkill -f rpc_fault_proxy.py; log "cleanup: containers $(docker ps -a --format '{{.Names}}' | grep -cE '^(rp-|syb-)') volumes $(docker volume ls -q | grep -c '^vol-')"; }
receipt() { cast receipt $1 -r $RPC --json | python3 -I -c '
import sys,json; r=json.load(sys.stdin); print("status",int(r["status"],16),"block",int(r["blockNumber"],16),"gasUsed",int(r["gasUsed"],16),"effectiveGasPrice",int(r["effectiveGasPrice"],16),"logs",[(l["address"][:10],l["topics"][0][:10],l["data"][:70]) for l in r["logs"]])'; }
case $case in
m5)
  dep 0x0000000000000000000000000000000000000000000000000000000000000070; proxy m5 18615 UPSTREAM=$RPC HOLD_SELECTOR=$WINLEAF HOLD_BLOCKS=1 PAD_REPLAY_PK=$(pk 37) PAD_BYTES=2048
  RPC_NODE=http://host.docker.internal:18615 start_node3 m5 $APP 36; wait_join m5; R0=$(root_of $CONS 0); A0=$(cast balance $(addr 37) -r $RPC); sybil m5 $APP $CONS 0 38 fight -e TICK=2; wait_marker syb-m5 JOINED 600; wait_result m5 400; summarize $R0 m5 m5
  TX=$(python3 -I -c '
import json,sys
for l in open(sys.argv[1]):
    r=json.loads(l)
    if r.get("note")=="padded replay":
        try: print(json.loads(r["stdout"])["transactionHash"])
        except Exception: print("ERR")' $OUT/m5-proxy.jsonl | tail -1); log "padded replay tx: $TX"
  case $TX in 0x*) log "padded receipt: $(receipt $TX)"; log "adversary (account 37) delta wei: $(python3 -c "print($(cast balance $(addr 37) -r $RPC) - $A0)")";; esac
  cleanup;;
p1)
  dep 0x0000000000000000000000000000000000000000000000000000000000000071; start_node3 p1 $APP 30; cast send --rpc-url $RPC --private-key $PK1 $INPUTBOX 'addInput(address,bytes)' $APP $(cast calldata 'corruptOutputsRoot(uint8)' 1) >/dev/null
  wait_join p1 300 1; step_mine 720 rp-p1; for i in $(seq 1 120); do [ "$(cast logs --from-block $BLK --address $CONS 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print(len(json.load(sys.stdin)))')" -ge 2 ] && break; [ $i -eq 40 ] && step_mine 60 rp-p1; sleep 5; done; log "sealed: $(sealed_list $CONS)"
  wait_join p1 300 2; sleep 3; R1=$(root_of $CONS 1); sybil p1 $APP $CONS 1 31 fight -e TICK=2; wait_marker syb-p1 JOINED 600; log "oracle $(docker logs syb-p1 2>&1 | grep -E '^HONEST|consumed' | tr '\n' ' ')"; wait_result p1 300; summarize $R1 p1 p1
  step_mine 720 rp-p1 syb-p1; sleep 20; log "canstage $(canstage $CONS | cut -c1-120)"; log "node settlement lines: $(docker logs rp-p1 2>&1 | grep -iE 'ERROR|terminal|settle|stage' | grep -v DEBUG | tail -3 | cut -c1-220 | tr '\n' ';')"; cleanup;;
l3)
  dep 0x0000000000000000000000000000000000000000000000000000000000000072; start_node3 l3 $APP 28; wait_join l3; R0=$(root_of $CONS 0); START=$BLK; TARGET=$((START+700-26)); log "root $R0 start $START; sybil join to land at block $TARGET (G+1 before closure)"
  proxy l3 18616 UPSTREAM=$RPC HOLD_SELECTOR=$JOIN HOLD_UNTIL_BLOCK=$TARGET; sybil l3 $APP $CONS 0 29 fight -e ENDPOINT=http://host.docker.internal:18616 -e TICK=2
  until [ "$(cast block-number -r $RPC)" -ge $((TARGET+2)) ]; do sleep 20; done; log "sybil join block: $(cast logs --from-block $BLK --address $R0 'CommitmentJoined(bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print([(l["topics"][2][-6:],int(l["blockNumber"],16)) for l in json.load(sys.stdin)])')"
  wait_result l3 400; summarize $R0 l3 l3; for i in $(seq 1 240); do [ "$(cast logs --from-block $BLK --address $CONS 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print(len(json.load(sys.stdin)))')" -ge 2 ] && break; sleep 10; done
  log "sealed: $(sealed_list $CONS); settlement at $(sealed_list $CONS | python3 -I -c 'import sys,ast;print(ast.literal_eval(sys.stdin.read())[-1][1])') vs root start $START (+700 plain allowance = $((START+700)))"; cleanup;;
k4)   # child-return refill: B joins the root while A's child is live, so the sling meets B after the return
  dep 0x0000000000000000000000000000000000000000000000000000000000000078 $FAC; start_node3 k4 $APP 16; wait_join k4; sleep 3; R0=$(root_of $CONS 0)
  sybil k4a $APP $CONS 0 17 fight -e TICK=2; wait_marker syb-k4a JOINED 600; sybil k4b $APP $CONS 0 18 fight -e TRANSITION=35184372088831 -e PATCH_SALT=k4b -e TICK=2; wait_marker syb-k4b JOINED 600; log "A and B joined root $R0: $(cast logs --from-block $BLK --address $R0 'CommitmentJoined(bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print([(l["topics"][2][-6:],int(l["blockNumber"],16)) for l in json.load(sys.stdin)])')"
  wait_result k4a 300; log "sybil A $(docker logs syb-k4a 2>&1 | grep '^RESULT'); matches: $(cast logs --from-block $BLK --address $R0 'MatchCreated(bytes32,bytes32,bytes32,bytes32,uint64,uint64)' -r $RPC --json | python3 -I -c 'import sys,json;print([(l["topics"][2][:10],l["topics"][3][:10],int(l["blockNumber"],16)) for l in json.load(sys.stdin)])')"
  log "root clocks before the child's return:"; clocks $R0 $BLK; step_mine 51200 rp-k4 syb-k4b; for i in $(seq 1 90); do [ "$(docker logs rp-k4 2>&1 | grep -c 'submit Hero action: propagate child')" -ge 1 ] && break; sleep 5; done; sleep 15; log "propagated; root deleted $(deleted $R0); matches now: $(cast logs --from-block $BLK --address $R0 'MatchCreated(bytes32,bytes32,bytes32,bytes32,uint64,uint64)' -r $RPC --json | python3 -I -c 'import sys,json;print([(l["topics"][2][:10],l["topics"][3][:10],int(l["blockNumber"],16),int(l["data"][66:130],16)) for l in json.load(sys.stdin)])')"
  wait_result k4b 400; log "sybil B $(docker logs syb-k4b 2>&1 | grep '^RESULT')"; log "root clocks (the sling's remaining at the second match is the refilled clock):"; clocks $R0 $BLK; for c in $(child_of $R0 | tr ' ' '\n' | cut -d: -f1); do log "child $c clocks:"; clocks $c $BLK; done; summarize $R0 "k4a k4b" k4; cleanup;;
k7)
  dep 0x0000000000000000000000000000000000000000000000000000000000000074 $FAC; start_node3 k7 $APP 14; cast send --rpc-url $RPC --private-key $PK1 $INPUTBOX 'addInput(address,bytes)' $APP $(cast calldata 'emitNotice(bytes)' 0xc7) >/dev/null; wait_join k7; sleep 3; log "sling joined epoch 0; advancing 51,200 blocks in steps with the sling paused"; step_mine 51200 rp-k7
  for i in $(seq 1 90); do [ "$(cast logs --from-block $BLK --address $CONS 'EpochStaged(uint256,bytes32,bytes32)' -r $RPC --json | python3 -I -c 'import sys,json;print(len(json.load(sys.stdin)))')" -ge 1 ] && break; sleep 5; done; step_mine 60 rp-k7; sleep 20; log "sealed: $(sealed_list $CONS)"
  wait_join k7 300 2; sleep 3; R1=$(root_of $CONS 1); sybil k7 $APP $CONS 1 15 fight -e WRONG_FINAL=1 -e TICK=2; wait_marker syb-k7 JOINED 600; log "wrong-final sybil joined epoch 1: $(docker logs syb-k7 2>&1 | grep -E '^(HONEST|SYBIL)' | tr '\n' ' ')"
  for i in $(seq 1 120); do [ "$(docker logs syb-k7 2>&1 | grep -c 'ACT advance')" -ge 6 ] && break; sleep 2; done; docker pause rp-k7; B0=$(cast block-number -r $RPC); log "sling paused at $B0; advancing 51,200 blocks with the sybil paused too"; step_mine 51200 syb-k7
  wait_result k7 120; log "sybil $(docker logs syb-k7 2>&1 | grep '^RESULT'); deleted $(deleted $R1); standing $(standing $R1 | tr -d '\n' | cut -c1-40)"
  PROOF=$(docker run --rm -v $P/harness/sybil:/harness:ro -v $TPL:/machine:ro $SYB /harness/validity_proof.lua /machine 2>/dev/null); log "stage: $(cast send --rpc-url $RPC --private-key $(pk 15) $CONS 'stageTournamentResult(uint256,((bytes32,bytes32[]),(bytes32,bytes32[]),(bytes32,bytes32[])))' 1 "$PROOF" --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:200])')"; step_mine 60; log "accept: $(cast send --rpc-url $RPC --private-key $(pk 15) $CONS 'acceptStagedTournamentResult(uint256)' 1 --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16))
except Exception: print(t[:200])')"; log "sealed: $(sealed_list $CONS)"
  docker unpause rp-k7; log "sling unpaused $(( $(cast block-number -r $RPC) - B0 )) blocks after the pause"; for i in $(seq 1 40); do [ "$(docker inspect -f '{{.State.Status}}' rp-k7)" = exited ] && break; sleep 10; done
  log "sling container: $(docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' rp-k7)"; docker logs rp-k7 2>&1 | grep -E 'ERROR|WARN' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g' | cut -c1-260 | sort | uniq -c | sort -rn | head -5; cleanup;;
k3a|k3b)
  [ $case = k3a ] && SALT=0x0000000000000000000000000000000000000000000000000000000000000075 && H2=30424 || { SALT=0x0000000000000000000000000000000000000000000000000000000000000076; H2=30426; }
  dep $SALT $FAC; SCHED="20025$(printf ',0%.0s' $(seq 1 29)),$H2"; proxy $case 18617 UPSTREAM=$RPC HOLD_SELECTOR=$ADV HOLD_SCHEDULE=$SCHED; RPC_NODE=http://host.docker.internal:18617 start_node3 $case $APP 10; wait_join $case; sleep 3
  R0=$(root_of $CONS 0); sybil $case $APP $CONS 0 11 fight -e TICK=2; wait_marker syb-$case JOINED 600; log "sybil joined root $R0; holds: 1st sling response 20,025 blocks, 31st $H2 (total lateness $(( 20000 + H2 - 25 )) vs C = 50,400); the driver advances held blocks in steps with the sybil paused"
  for round in 1 2; do for i in $(seq 1 600); do grep -q "holding sendRaw" $OUT/$case-proxy.jsonl 2>/dev/null && [ "$(grep -c 'holding sendRaw' $OUT/$case-proxy.jsonl)" -ge $round ] && break; docker logs syb-$case 2>&1 | grep -q '^RESULT' && break 2; sleep 2; done
    N=$(grep 'holding sendRaw' $OUT/$case-proxy.jsonl | sed -n "${round}p" | grep -oE 'holding sendRaw [0-9]+' | awk '{print $3}'); [ -z "$N" ] && break; log "hold $round of $N blocks begins; stepping"; step_mine $((N+1)) syb-$case; sleep 5; log "released: $(grep -c 'released' $OUT/$case-proxy.jsonl) (re-sends waited: $(grep -c 'waits for the same release' $OUT/$case-proxy.jsonl))"; done
  wait_result $case 400; log "root clocks:"; clocks $R0 $BLK; for c in $(child_of $R0 | tr ' ' '\n' | cut -d: -f1); do log "child clocks:"; clocks $c $BLK; done; summarize $R0 $case $case; cleanup;;
m5base)  # the same front-run copy of the honest leaf proof with no padding: the refund baseline for M5
  dep 0x0000000000000000000000000000000000000000000000000000000000000079; rm -f $OUT/m5base-proxy.jsonl; proxy m5base 18618 UPSTREAM=$RPC HOLD_SELECTOR=$WINLEAF HOLD_BLOCKS=1 PAD_REPLAY_PK=$(pk 37) PAD_BYTES=0
  RPC_NODE=http://host.docker.internal:18618 start_node3 m5base $APP 36; wait_join m5base; R0=$(root_of $CONS 0); A0=$(cast balance $(addr 37) -r $RPC); sybil m5base $APP $CONS 0 38 fight -e TICK=2; wait_marker syb-m5base JOINED 600; wait_result m5base 400; summarize $R0 m5base m5base
  TX=$(python3 -I -c '
import json,sys
for l in open(sys.argv[1]):
    r=json.loads(l)
    if r.get("note")=="padded replay":
        try: print(json.loads(r["stdout"])["transactionHash"])
        except Exception: print("ERR")' $OUT/m5base-proxy.jsonl | head -1); log "unpadded copy tx: $TX"
  case $TX in 0x*) log "receipt: $(receipt $TX)"; log "account 37 delta wei: $(python3 -c "print($(cast balance $(addr 37) -r $RPC) - $A0)")";; esac
  cleanup;;
esac
log done
