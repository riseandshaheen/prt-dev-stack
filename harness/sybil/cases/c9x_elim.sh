#!/bin/bash
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
cons() { awk -v n=$1 '$1==n{print $3}' $OUT/apps.txt | tail -1; }
until [ "$(cast block-number -r $RPC)" -ge 3010 ]; do sleep 15; done
echo "== C9x third-party elimination at $(date +%T) block $(cast block-number -r $RPC)"
echo "root standing before: $(standing 0xf0cb96E0088ad6D7FC6ccb0176C59Fd6B39Fcba0 | tr -d '\n' | cut -c1-120)"
echo "root balance before: $(cast balance 0xf0cb96E0088ad6D7FC6ccb0176C59Fd6B39Fcba0 -r $RPC -e); account 1 before: $(cast balance $AD1 -r $RPC -e)"
echo "sling (account 17) log tail: $(docker logs rp-c9x 2>&1 | grep -v DEBUG | tail -1 | cut -c1-160)"
echo "sling eliminate attempts so far: $(docker logs rp-c9x 2>&1 | grep -c 'eliminate')"
echo "eliminateMatchByTimeout by account 1:"
cast send --rpc-url $RPC --private-key $PK1 0xf0cb96E0088ad6D7FC6ccb0176C59Fd6B39Fcba0 'eliminateMatchByTimeout((bytes32,bytes32))' "(0x5ae62336a8e09b38a565fccd245179c3c699c6608668d918f2668b429752ccca,0x0fac00defdb88465e84fe847be4f01c07af73be79b88642bd50c7c897aa148ac)" --json 2>&1 | python3 -I -c 'import sys,json;t=sys.stdin.read()
try:
    r=json.loads(t);print(" status",r["status"],"block",int(r["blockNumber"],16),"gas",int(r["gasUsed"],16),"logs",len(r["logs"]))
except Exception: print(t[:600])'
echo "root balance after: $(cast balance 0xf0cb96E0088ad6D7FC6ccb0176C59Fd6B39Fcba0 -r $RPC -e); account 1 after: $(cast balance $AD1 -r $RPC -e)"
cast logs --from-block 1600 --address 0xf0cb96E0088ad6D7FC6ccb0176C59Fd6B39Fcba0 'MatchDeleted(bytes32,bytes32,bytes32,uint8,uint8)' -r $RPC --json | python3 -I -c 'import sys,json;[print("MatchDeleted block",int(x["blockNumber"],16),"reason",int(x["data"][2:66],16),"winner",int(x["data"][66:130],16),"tx",x["transactionHash"]) for x in json.load(sys.stdin)]'
echo "root standing after: $(standing 0xf0cb96E0088ad6D7FC6ccb0176C59Fd6B39Fcba0 | tr -d '\n' | cut -c1-120)"
for i in $(seq 1 60); do r=$(canstage $(cons c9x)); case "$r" in true*) break;; esac; sleep 15; done
echo "canstage at $(date +%T) block $(cast block-number -r $RPC): $(canstage $(cons c9x) | cut -c1-120)"
sleep 60; echo "staged: $(cast logs --from-block 1600 --address $(cons c9x) 'EpochStaged(uint256,bytes32,bytes32)' -r $RPC --json | python3 -I -c 'import sys,json;print([(int(x["blockNumber"],16),x["transactionHash"]) for x in json.load(sys.stdin)])')"
