#!/bin/bash
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
source ${CASES_RUN:-/tmp/prt-cases}/env2.sh
case=$1; log() { echo "[$case $(date +%T) block $(cast block-number -r $RPC)] $*"; }
dep() { until mkdir $S/run/deploy.lock 2>/dev/null; do sleep 1; done; read APP CONS BLK <<<"$(deploy_app2 $1 ${2:-50} "${3:-[]}")"; mine 2; sleep 2; rmdir $S/run/deploy.lock; echo "$case $APP $CONS" >> $OUT/apps.txt; log "deployed app=$APP cons=$CONS at $BLK"; }
root_of() { cast logs --from-block 25 --address $1 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print("0x"+l[int(sys.argv[1])]["data"][-40:])' $2; }
deleted() { cast logs --from-block 25 --address $1 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),int(x["data"][2:66],16),int(x["data"][66:130],16)) for x in json.load(sys.stdin)])'; }
txstat() { python3 -I - "$1" "$2" "$3" <<'PY'
import json,urllib.request,sys
RPC="http://127.0.0.1:8545"; A=sys.argv[1].lower(); start=int(sys.argv[2]); sel=sys.argv[3]
def rpc(m,p): return json.loads(urllib.request.urlopen(urllib.request.Request(RPC,json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode(),{"content-type":"application/json"})).read())["result"]
head=int(rpc("eth_blockNumber",[]),16); out=[]
for b in range(start,head+1):
    for tx in rpc("eth_getBlockByNumber",[hex(b),True])["transactions"]:
        if tx["from"].lower()==A and (sel=="any" or tx["input"][:10]==sel):
            r=rpc("eth_getTransactionReceipt",[tx["hash"]]); out.append((b,tx["input"][:10],int(r["status"],16)))
print(out)
PY
}
case $case in
d3race)
  dep 0x0000000000000000000000000000000000000000000000000000000000000040
  touch $S/run/ticker.stop; sleep 2; cast rpc anvil_setAutomine false -r $RPC >/dev/null
  SLING=$(addr 35); nohup env RPC=$RPC VICTIM=$SLING ADVERSARY_PK=$(pk 36) STOP=$S/run/copier2.stop LOG=$OUT/d3race-copier.jsonl ONLY_SELECTOR=0x1d5bf796 MAX_COPIES=1 python3 -I $S/run/copier.py > $OUT/d3race-copier.log 2>&1 &
  rm -f $S/run/copier2.stop; start_node2 d3race $APP 35; log "automine off, copier (account 36) copies only the first join of the sling (account 35)"
  for i in $(seq 1 200); do grep -q copied $OUT/d3race-copier.log && break; sleep 2; done; sleep 6; log "copier: $(grep copied $OUT/d3race-copier.log)"
  R0=$(root_of $CONS 0); log "joins on $R0: $(cast logs --from-block $BLK --address $R0 'CommitmentJoined(bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print([(l["topics"][2][-6:],int(l["blockNumber"],16)) for l in json.load(sys.stdin)])')"
  log "sling join txs (block, selector, status): $(txstat $SLING $BLK 0x1d5bf796)"
  log "sling log on the join: $(docker logs rp-d3race 2>&1 | grep -iE 'revert|already|join' | grep -v 'DEBUG.*polled' | grep -v 'prepared Join' | head -4 | sed -E 's/0x[0-9a-f]{64}/TX/g' | cut -c1-200 | tr '\n' ';')"
  touch $S/run/copier2.stop; sleep 3; cast rpc anvil_setAutomine true -r $RPC >/dev/null; rm -f $S/run/ticker.stop; nohup $S/run/ticker.sh $S/run/ticker.stop > $OUT/ticker.log 2>&1 & log "ticker back"
  wait_sealed_n $CONS 2 400 && log "epoch 1 sealed: $(sealed_list $CONS)"; log "staged by: $(cast logs --from-block $BLK --address $CONS 'EpochStaged(uint256,bytes32,bytes32)' -r $RPC --json | python3 -I -c 'import sys,json;print([x["transactionHash"] for x in json.load(sys.stdin)])')"
  log "root balance $(cast balance $R0 -r $RPC -e); sling warn/err: $(docker logs rp-d3race 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g' | cut -c1-160 | sort | uniq -c | tr '\n' ';')";;
c9tp)
  dep 0x0000000000000000000000000000000000000000000000000000000000000043
  sybil c9tp1 $APP $CONS 0 14 join -e TRANSITION=70368744177663 -e PATCH_SALT=c9c; sybil c9tp2 $APP $CONS 0 15 join -e TRANSITION=140737488355327 -e PATCH_SALT=c9d
  wait_marker syb-c9tp1 JOINED 300; wait_marker syb-c9tp2 JOINED 300; log "silent pair joined"; start_node2 c9tp $APP 16
  R0=$(root_of $CONS 0); read MID ONE TWO ELIM <<<"$(cast logs --from-block $BLK --address $R0 'MatchCreated(bytes32,bytes32,bytes32,bytes32,uint64,uint64)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin)[0];print(l["topics"][1],l["topics"][2],l["topics"][3],int(l["data"][130:194],16))')"
  log "match $MID eliminable at $ELIM"; for i in $(seq 1 300); do docker logs rp-c9tp 2>&1 | grep -q 'submit Hero action: join' && break; sleep 2; done; sleep 5
  docker pause rp-c9tp; log "sling paused (account 16) until after the eliminable block"
  until [ "$(cast block-number -r $RPC)" -ge $((ELIM+3)) ]; do sleep 10; done
  B0=$(cast balance $AD1 -r $RPC); log "root balance before $(cast balance $R0 -r $RPC -e); eliminateMatchByTimeout by account 1: $(cast send --rpc-url $RPC --private-key $PK1 $R0 'eliminateMatchByTimeout((bytes32,bytes32))' "($ONE,$TWO)" --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try: r=json.loads(t);print("status",r["status"],"block",int(r["blockNumber"],16),"gas",int(r["gasUsed"],16),"gasPrice",int(r["effectiveGasPrice"],16),"logs",[l["topics"][0][:10] for l in r["logs"]])
except Exception: print(t[:300])')"
  B1=$(cast balance $AD1 -r $RPC); log "account 1 balance delta (wei): $((B1-B0)); root balance after $(cast balance $R0 -r $RPC -e); deleted $(deleted $R0); standing $(standing $R0 | tr -d '\n' | cut -c1-30)"
  cast logs --from-block $BLK --address $R0 'PartialBondRefund(bytes32,address,uint256)' -r $RPC --json 2>/dev/null | python3 -I -c 'import sys,json;[print("PartialBondRefund",l["topics"][1:],l["data"]) for l in json.load(sys.stdin)]'
  docker unpause rp-c9tp; log "sling unpaused"; wait_sealed_n $CONS 2 400 && log "epoch 1 sealed: $(sealed_list $CONS)"; log "root balance end $(cast balance $R0 -r $RPC -e); sling bond lines: $(docker logs rp-c9tp 2>&1 | grep -iE 'bond|eliminat' | grep -v DEBUG | cut -c1-140 | head -4 | tr '\n' ';')";;
seam)
  dep 0x0000000000000000000000000000000000000000000000000000000000000044
  start_node2 seam $APP 10
  addinput $APP $(cast calldata 'corruptOutputsRoot(uint8)' 1); addinput $APP $(cast calldata 'reject(bytes)' 0x5e); addinput $APP $(cast calldata 'corruptOutputsRoot(uint8)' 0); addinput $APP $(cast calldata 'emitNotices(uint32,uint32,uint8)' 2 2097056 0)
  wait_sealed_n $CONS 2 600 || exit 1; log "epoch 1 sealed: $(sealed_list $CONS)"
  for i in $(seq 1 300); do [ "$(docker logs rp-seam 2>&1 | grep -c 'submit Hero action: join')" -ge 2 ] && break; sleep 2; done; sleep 3
  R1=$(root_of $CONS 1); log "sling joined epoch 1 root $R1 with $(cast logs --from-block 25 --address $R1 'CommitmentJoined(bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print([l["topics"][1] for l in json.load(sys.stdin)])')"
  log "node per-input lines: $(docker logs rp-seam 2>&1 | grep -iE 'processing input 1:|rejected|reverted|outputs|exception|halt|notice' | grep -v DEBUG | cut -c1-150 | head -8 | tr '\n' ';')"
  sybil seam $APP $CONS 1 11 fight -e TRANSITION=295147905179352825855
  wait_marker syb-seam HONEST 600; log "oracle: $(docker logs syb-seam 2>&1 | grep -E '^HONEST|consumed' | tr '\n' ' ')"
  wait_marker syb-seam JOINED 600 || log "sybil did not join: $(docker logs syb-seam 2>&1 | grep -iE 'error|revert' | head -2 | cut -c1-200)"
  for i in $(seq 1 240); do docker logs syb-seam 2>&1 | grep -q '^RESULT' && break; sleep 10; done; log "sybil $(docker logs syb-seam 2>&1 | grep '^RESULT')"
  CH=$(cast logs --from-block 25 --address $R1 'NewInnerTournament(bytes32,address,bytes32,bytes32,bytes32,bytes32,(bytes32,uint256,uint64,uint64,uint64,uint8,uint64,uint64),uint256)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print(("0x"+l[0]["topics"][2][-40:]) if l else "none")')
  [ "$CH" != none ] && { log "child $CH deleted $(deleted $CH)"; cast logs --from-block 25 --address $CH 'LeafMatchSealed(bytes32,bytes32,bytes32,bytes32,uint256,bytes32,bytes32,uint64,uint64,uint64)' -r $RPC --json | python3 -I -c 'import sys,json;[print("LeafMatchSealed divergencePosition",int(l["data"][2+64*3:2+64*4],16),"block",int(l["blockNumber"],16)) for l in json.load(sys.stdin)]'; }
  docker logs rp-seam 2>&1 | grep -E 'ProveLeaf|winLeafMatch|WARN|ERROR' | grep -v DEBUG | tail -4 | cut -c1-220
  wait_sealed_n $CONS 3 400 && log "epoch 2 sealed: $(sealed_list $CONS)";;
h1d2)
  dep 0x0000000000000000000000000000000000000000000000000000000000000045
  start_node2 h1d2a $APP 12; start_node2 h1d2b $APP 12; for i in $(seq 1 300); do docker logs rp-h1d2a 2>&1 | grep -q 'submit Hero action: join' && break; sleep 2; done; sleep 3
  sybil h1d2 $APP $CONS 0 13 fight; wait_marker syb-h1d2 JOINED 600; log "sybil (account 13) joined; slings share account 12 = $(addr 12)"
  for i in $(seq 1 240); do docker logs syb-h1d2 2>&1 | grep -q '^RESULT' && break; sleep 10; done; log "sybil $(docker logs syb-h1d2 2>&1 | grep '^RESULT')"
  for n in h1d2a h1d2b; do log "$n: actions $(docker logs rp-$n 2>&1 | grep -c 'submit Hero action') submitted $(docker logs rp-$n 2>&1 | grep -c 'submitted .* transaction') warn/err: $(docker logs rp-$n 2>&1 | grep -E 'WARN|ERROR' | grep -v DEBUG | sed -E 's/0x[0-9a-f]{64}/TX/g; s/nonce [0-9]+/nonce N/' | cut -c1-120 | sort | uniq -c | sort -rn | head -3 | tr '\n' ';')"; done
  log "shared-key txs (block, selector, status): $(txstat $(addr 12) $BLK any | python3 -I -c 'import sys,ast;l=ast.literal_eval(sys.stdin.read());print("total",len(l),"ok",sum(1 for x in l if x[2]),"reverted",sum(1 for x in l if not x[2]))')"
  wait_sealed_n $CONS 2 400 && log "epoch 1 sealed: $(sealed_list $CONS)";;
esac
log done
