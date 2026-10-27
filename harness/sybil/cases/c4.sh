#!/bin/bash
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
read APP CONS BLK <<<"$(deploy_app 0x000000000000000000000000000000000000000000000000000000000000002d)"; echo "c4x app=$APP cons=$CONS block=$BLK" | tee $OUT/c4x-app.txt; echo "c4x $APP $CONS" >> $OUT/apps.txt
touch $S/run/ticker.stop; sleep 2; cast rpc anvil_setAutomine false -r $RPC >/dev/null; echo "automine off, ticker stopped at $(date +%T) block $(cast block-number -r $RPC)"
VICTIM=0xFABB0ac9d68B0B445fB7357272Ff202C5651694a; nohup env RPC=$RPC VICTIM=$VICTIM ADVERSARY_PK=$PK16 STOP=$S/run/copier.stop LOG=$OUT/c4x-copier.jsonl python3 -I $S/run/copier.py > $OUT/c4x-copier.log 2>&1 &
rm -f $S/run/copier.stop; start_node c4x $APP $PK12; echo "c4x sling (account 12) started $(date +%T)"
for i in $(seq 1 200); do docker logs rp-c4x 2>&1 | grep -q 'submit Hero action: join' && break; sleep 2; done; sleep 6
T=$(docker logs rp-c4x 2>&1 | grep -o 'join tournament 0x[0-9a-fA-F]*' | head -1 | awk '{print $3}'); echo "c4x root $T; joins: $(cast logs --from-block $BLK --address $T 'CommitmentJoined(bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print([(l["topics"][2][-6:],int(l["blockNumber"],16)) for l in json.load(sys.stdin)])')"
sybil c4x $APP $CONS 0 20 fight; echo "c4x sybil (account 20) started $(date +%T)"
for i in $(seq 1 180); do r=$(canstage $CONS); case "$r" in true*) break;; esac; docker logs syb-c4x 2>&1 | grep -q '^RESULT' && [ $i -gt 30 ] && break; sleep 10; done
echo "c4x dispute phase over at $(date +%T) block $(cast block-number -r $RPC): canstage=$(canstage $CONS | cut -c1-40)"
