#!/bin/bash
# watch.sh NAME CONS : every 20 s log block, EpochSealed count, canStage, EpochStaged count, until epoch 2 is sealed or 3 h
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
n=$1; c=$2
for i in $(seq 1 540); do
  s=$(cast logs --from-block 25 --address $c 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print(len(l)-1, [ (int(x["topics"][1],16), int(x["blockNumber"],16)) for x in l][-1])')
  st=$(cast logs --from-block 25 --address $c 'EpochStaged(uint256,bytes32,bytes32)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print([(int(x["topics"][1],16) if len(x["topics"])>1 else -1, int(x["blockNumber"],16)) for x in l])')
  echo "$(date +%T) block $(cast block-number -r $RPC) sealed=$s staged=$st canstage=$(canstage $c)"
  [ "${s%% *}" -ge 2 ] && exit 0
  sleep 20
done
