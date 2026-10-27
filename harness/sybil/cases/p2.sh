#!/bin/bash
# p2.sh CASE : phase-2 dispute cases (K, L, P, M3, M5). bash.
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
source ${CASES_RUN:-/tmp/prt-cases}/env2.sh
case=$1; log() { echo "[$case $(date +%T) block $(cast block-number -r $RPC)] $*"; }
dep() { until mkdir $S/run/deploy.lock 2>/dev/null; do sleep 1; done; read APP CONS BLK <<<"$(deploy_app3 $1 ${2:-50} "${3:-[]}" ${4:-$FACTORY})"; sleep 2; rmdir $S/run/deploy.lock; echo "$case $APP $CONS" >> $OUT/apps.txt; log "deployed app=$APP cons=$CONS at $BLK factory ${4:-$FACTORY}"; }
root_of() { cast logs --from-block 25 --address $1 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print("0x"+l[int(sys.argv[1])]["data"][-40:])' $2; }
deleted() { cast logs --from-block 25 --address $1 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),int(x["data"][2:66],16),int(x["data"][66:130],16)) for x in json.load(sys.stdin)])'; }
child_of() { cast logs --from-block 25 --address $1 'NewInnerTournament(bytes32,address,bytes32,bytes32,bytes32,bytes32,(bytes32,uint256,uint64,uint64,uint64,uint8,uint64,uint64),uint256)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print(" ".join("0x"+x["topics"][2][-40:]+":"+str(int(x["blockNumber"],16)) for x in l))'; }
wait_join() { for i in $(seq 1 ${2:-300}); do [ "$(docker logs rp-$1 2>&1 | grep -c 'submit Hero action: join')" -ge ${3:-1} ] && return 0; sleep 2; done; return 1; }
wait_result() { for i in $(seq 1 ${2:-240}); do docker logs syb-$1 2>&1 | grep -q '^RESULT' && return 0; sleep 10; done; return 1; }
held() { python3 -I -c '
import json,sys
for l in open(sys.argv[1]):
    r=json.loads(l)
    if r.get("note","").startswith("holding") or r.get("note","")=="released sendRaw": print(r["ts"][11:], r["note"], r.get("selector"), r.get("from_block") or r.get("at_block"), (r.get("error") or {}).get("message","")[:60])' $OUT/$1-proxy.jsonl; }
summarize_dispute() { # ROOT NAME
  log "root deleted $(deleted $1); children $(child_of $1)"; for c in $(child_of $1 | tr ' ' '\n' | cut -d: -f1); do log "child $c deleted $(deleted $c)"; done
  log "sybil: $(docker logs syb-$2 2>&1 | grep -E '^RESULT' | tr '\n' ' '); sling actions: $(docker logs rp-$2 2>&1 | grep -oE 'submit Hero action: [a-z ]+' | sort | uniq -c | tr -s ' ' | tr '\n' ';')"
  log "sling warn/err: $(docker logs rp-$2 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g; s/nonce [0-9]+/nonce N/; s/[0-9]+ min/N min/' | cut -c1-150 | sort | uniq -c | sort -rn | head -4 | tr '\n' ';')"; }
case $case in
k12)  # K1 + K2: the sling's 1st-4th advances held exactly 25, 26, 35, 125 blocks
  dep 0x0000000000000000000000000000000000000000000000000000000000000050 50 "[]" $FACTORY_MAINNET
  proxy k12 18611 HOLD_SELECTOR=$ADV HOLD_SCHEDULE=25,26,35,125; RPC_NODE=http://host.docker.internal:18611 start_node3 k12 $APP 10; wait_join k12; sleep 3
  R0=$(root_of $CONS 0); sybil k12 $APP $CONS 0 11 fight; wait_marker syb-k12 JOINED 600; log "sybil joined root $R0"
  wait_result k12 300; held k12 | head -8; log "clocks:"; python3 -I $S/run/clocks.py $R0 $BLK; summarize_dispute $R0 k12;;
k6)   # honest node offline G+5 = 30 blocks mid-dispute
  dep 0x0000000000000000000000000000000000000000000000000000000000000051 50 "[]" $FACTORY_MAINNET
  start_node3 k6 $APP 12; wait_join k6; sleep 3; R0=$(root_of $CONS 0); sybil k6 $APP $CONS 0 13 fight; wait_marker syb-k6 JOINED 600
  for i in $(seq 1 120); do [ "$(docker logs syb-k6 2>&1 | grep -c 'ACT advance')" -ge 6 ] && break; sleep 2; done
  B0=$(cast block-number -r $RPC); docker pause rp-k6; log "sling paused at $B0"; until [ "$(cast block-number -r $RPC)" -ge $((B0+30)) ]; do sleep 2; done; docker unpause rp-k6; log "sling unpaused after $(( $(cast block-number -r $RPC) - B0 )) blocks"
  wait_result k6 300; log "clocks:"; python3 -I $S/run/clocks.py $R0 $BLK; summarize_dispute $R0 k6; log "overdue/offline log lines: $(docker logs rp-k6 2>&1 | grep -iE 'overdue|late|behind|deadline' | grep -v DEBUG | head -3 | cut -c1-200 | tr '\n' ';')";;
k8)   # root join held G-1 = 24 blocks, child join held T+G-1 = 624 blocks
  dep 0x0000000000000000000000000000000000000000000000000000000000000052 50 "[]" $FACTORY_MAINNET
  proxy k8 18612 HOLD_SELECTOR=$JOIN HOLD_SCHEDULE=24,624; RPC_NODE=http://host.docker.internal:18612 start_node3 k8 $APP 14
  R0=$(root_of $CONS 0); sybil k8 $APP $CONS 0 15 fight; wait_marker syb-k8 JOINED 900; log "sybil joined root $R0 (sling join held)"
  wait_result k8 400; held k8 | head -6; log "clocks:"; python3 -I $S/run/clocks.py $R0 $BLK; for c in $(child_of $R0 | tr ' ' '\n' | cut -d: -f1); do log "child clocks:"; python3 -I $S/run/clocks.py $c $BLK; done; summarize_dispute $R0 k8;;
k4)   # two children in sequence: the honest clock after each return
  dep 0x0000000000000000000000000000000000000000000000000000000000000053 50 "[]" $FACTORY_MAINNET
  start_node3 k4 $APP 16; wait_join k4; sleep 3; R0=$(root_of $CONS 0); sybil k4a $APP $CONS 0 17 fight; wait_result k4a 300; log "sybil A $(docker logs syb-k4a 2>&1 | grep '^RESULT'); waiting for the child's return"
  for i in $(seq 1 400); do [ "$(docker logs rp-k4 2>&1 | grep -c 'submit Hero action: propagate child')" -ge 1 ] && break; sleep 10; done; log "propagated A's child; root deleted $(deleted $R0)"
  sybil k4b $APP $CONS 0 18 fight -e TRANSITION=35184372088831 -e PATCH_SALT=k4b; wait_result k4b 300; log "sybil B $(docker logs syb-k4b 2>&1 | grep '^RESULT')"
  for i in $(seq 1 400); do [ "$(docker logs rp-k4 2>&1 | grep -c 'submit Hero action: propagate child')" -ge 2 ] && break; sleep 10; done; log "root clocks:"; python3 -I $S/run/clocks.py $R0 $BLK; summarize_dispute $R0 k4;;
l1)   # 8 silent sybils, 4 per class, + the sling through a counting proxy (L4)
  dep 0x0000000000000000000000000000000000000000000000000000000000000054
  for n in 0 1 2 3; do sybil l1a$n $APP $CONS 0 $((20+n)) join -e TRANSITION=$(python3 -c "print((1<<(44+$n))-1)") -e PATCH_SALT=l1a$n; sybil l1b$n $APP $CONS 0 $((24+n)) join -e TRANSITION=$(python3 -c "print((1<<(44+$n))-1)") -e PATCH_SALT=l1b$n -e WRONG_FINAL=1; done
  for n in 0 1 2 3; do wait_marker syb-l1a$n JOINED 600; wait_marker syb-l1b$n JOINED 600; done; log "8 sybils joined"
  proxy l1 18614 TAG=l1; RPC_NODE=http://host.docker.internal:18614 start_node3 l1 $APP 19; T0=$(date +%s); wait_join l1; log "sling joined"
  R0=$(root_of $CONS 0); for round in $(seq 1 40); do
    sleep 60; M=$(cast logs --from-block $BLK --address $R0 'MatchCreated(bytes32,bytes32,bytes32,bytes32,uint64,uint64)' -r $RPC --json | python3 -I -c 'import sys,json;[print(l["topics"][2],l["topics"][3],int(l["data"][130:194],16)) for l in json.load(sys.stdin)]')
    H=$(cast block-number -r $RPC); while read ONE TWO EL; do [ -z "$ONE" ] && continue; [ "$H" -gt "$EL" ] || continue
      cast send --rpc-url $RPC --private-key $PK1 $R0 'eliminateMatchByTimeout((bytes32,bytes32))' "($ONE,$TWO)" >/dev/null 2>&1 && log "eliminated match $ONE/$TWO (eliminable at $EL)"; done <<<"$M"
    docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' rp-l1 | sed "s/^/[l1 stats round $round] /"; st=$(standing $R0 | tr -d '\n' | cut -c2-2); [ "$st" = 2 ] && break; done
  log "root standing $(standing $R0 | tr -d '\n' | cut -c1-40); deleted $(deleted $R0); children $(child_of $R0)"
  log "sling actions: $(docker logs rp-l1 2>&1 | grep -oE 'submit Hero action: [a-z ]+' | sort | uniq -c | tr -s ' ' | tr '\n' ';') in $(( $(date +%s) - T0 )) s"
  log "RPC requests to the sling's proxy: total $(grep -c '"dir": "req"' $OUT/l1-proxy.jsonl); by method: $(grep '"dir": "req"' $OUT/l1-proxy.jsonl | grep -oE '"methods": \[[^]]*\]' | sort | uniq -c | sort -rn | head -6 | tr -s ' ' | tr '\n' ';')"
  log "data volume: $(docker system df -v --format '{{json .Volumes}}' | python3 -I -c 'import sys,json;[print(v["Name"],v["Size"]) for v in json.load(sys.stdin) if v["Name"]=="vol-l1"]')"
  log "sling warn/err: $(docker logs rp-l1 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g' | cut -c1-140 | sort | uniq -c | sort -rn | head -3 | tr '\n' ';')";;
l3)   # one sybil joins G+1 blocks before root closure and delegates once
  dep 0x000000000000000000000000000000000000000000000000000000000000005a
  start_node3 l3 $APP 28; wait_join l3; R0=$(root_of $CONS 0); START=$BLK; TARGET=$((START+700-26)); log "root $R0 start $START; sybil will join at block $TARGET"
  until [ "$(cast block-number -r $RPC)" -ge $((TARGET-260)) ]; do sleep 10; done; sybil l3 $APP $CONS 0 29 fight -e TICK=1
  wait_marker syb-l3 JOINED 300; log "sybil joined at $(cast logs --from-block $BLK --address $R0 'CommitmentJoined(bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print([int(l["blockNumber"],16) for l in json.load(sys.stdin)])')"
  wait_result l3 300; summarize_dispute $R0 l3; wait_sealed_n $CONS 2 600 && log "epoch 1 sealed: $(sealed_list $CONS) (root start $START: settlement delay $(( $(sealed_list $CONS | python3 -I -c 'import sys,ast;print(ast.literal_eval(sys.stdin.read())[-1][1])') - START - 700 )) blocks beyond the plain allowance)";;
p1)   # corruptOutputsRoot(1) and (2) epochs: dispute, then the staging attempt
  dep 0x000000000000000000000000000000000000000000000000000000000000005c
  start_node3 p1 $APP 30; addinput $APP $(cast calldata 'corruptOutputsRoot(uint8)' 1); wait_sealed_n $CONS 2 600 || exit 1; log "epoch 1 sealed: $(sealed_list $CONS)"; wait_join p1 300 2; sleep 3
  R1=$(root_of $CONS 1); sybil p1 $APP $CONS 1 31 fight -e TICK=2; wait_marker syb-p1 JOINED 600; log "oracle $(docker logs syb-p1 2>&1 | grep -E '^HONEST|consumed' | tr '\n' ' ')"; wait_result p1 300; summarize_dispute $R1 p1
  for i in $(seq 1 120); do r=$(canstage $CONS); case "$r" in true*) break;; esac; sleep 10; done; log "canstage $(canstage $CONS | cut -c1-120)"
  log "node settlement lines: $(docker logs rp-p1 2>&1 | grep -iE 'ERROR|terminal|settle|stage' | grep -v DEBUG | tail -3 | cut -c1-220 | tr '\n' ';')"
  log "third-party stage with a proof of the node's post-epoch machine is not possible without its snapshot; the node's own verdict above is the result";;
p4)   # raiseException epoch through a dispute, then settlement attempt
  dep 0x0000000000000000000000000000000000000000000000000000000000000057
  start_node3 p4 $APP 32; addinput $APP $(cast calldata 'raiseException(bytes)' 0xdead); wait_sealed_n $CONS 2 600 || exit 1; log "epoch 1 sealed: $(sealed_list $CONS)"; wait_join p4 300 2; sleep 3
  R1=$(root_of $CONS 1); sybil p4 $APP $CONS 1 33 fight; wait_marker syb-p4 JOINED 600; log "oracle $(docker logs syb-p4 2>&1 | grep -E '^HONEST|consumed' | tr '\n' ' ')"; wait_result p4 300; summarize_dispute $R1 p4
  for i in $(seq 1 120); do r=$(canstage $CONS); case "$r" in true*) break;; esac; sleep 10; done; log "canstage $(canstage $CONS | cut -c1-120)"
  log "node settlement lines: $(docker logs rp-p4 2>&1 | grep -iE 'ERROR|terminal|settle|stage|exception' | grep -v DEBUG | tail -4 | cut -c1-220 | tr '\n' ';')";;
m3)   # claimer-slot capture payout
  dep 0x0000000000000000000000000000000000000000000000000000000000000058
  sybil m3 $APP $CONS 0 35 honest; wait_marker syb-m3 JOINED 300; start_node3 m3 $APP 34; R0=$(root_of $CONS 0); log "adversary (account 35) holds the slot on $R0; sling (account 34) started"
  S0=$(cast balance $(addr 34) -r $RPC); A0=$(cast balance $(addr 35) -r $RPC); wait_sealed_n $CONS 2 400 && log "epoch 1 sealed: $(sealed_list $CONS)"
  log "tryRecoveringBond by the sling's account: $(cast send --rpc-url $RPC --private-key $(pk 34) $R0 'tryRecoveringBond()(bool)' --json | python3 -I -c 'import sys,json;r=json.load(sys.stdin);print("status",r["status"],"logs",len(r["logs"]))')"
  log "balances (wei) sling delta $(( $(cast balance $(addr 34) -r $RPC) - S0 )) adversary delta $(( $(cast balance $(addr 35) -r $RPC) - A0 )) root $(cast balance $R0 -r $RPC)";;
m5)   # padded-calldata replay of the honest leaf proof: the proxy holds the sling's winLeafMatch one block,
      # first re-sends its calldata + 2048 zero bytes from account 37, then forwards the original
  dep 0x000000000000000000000000000000000000000000000000000000000000005b
  proxy m5 18615 HOLD_SELECTOR=$WINLEAF HOLD_BLOCKS=1 PAD_REPLAY_PK=$(pk 37) PAD_BYTES=2048; RPC_NODE=http://host.docker.internal:18615 start_node3 m5 $APP 36; wait_join m5; R0=$(root_of $CONS 0)
  sybil m5 $APP $CONS 0 38 fight; wait_marker syb-m5 JOINED 600; wait_result m5 400; summarize_dispute $R0 m5
  python3 -I -c '
import json,sys
for l in open(sys.argv[1]):
    r=json.loads(l)
    if r.get("note")=="padded replay": print("padded replay to",r["to"],"bytes",r["bytes"],"+",r["pad"],"stdout",r["stdout"][-300:],"stderr",r["stderr"][-300:])' $OUT/m5-proxy.jsonl
  TX=$(python3 -I -c '
import json,sys
for l in open(sys.argv[1]):
    r=json.loads(l)
    if r.get("note")=="padded replay":
        try: print(json.loads(r["stdout"])["transactionHash"])
        except Exception: pass' $OUT/m5-proxy.jsonl | head -1); [ -n "$TX" ] && log "padded copy $TX status $(cast receipt $TX status -r $RPC) gasUsed $(cast receipt $TX gasUsed -r $RPC) revert: $(cast run $TX -r $RPC 2>&1 | grep -oE 'custom error[^,]{0,40}|revert[^\n]{0,80}' | head -1)";;
esac
log done
