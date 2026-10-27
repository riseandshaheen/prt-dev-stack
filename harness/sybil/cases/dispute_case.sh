#!/bin/bash
# dispute_case.sh NAME SALT_OR_- PORT PK PLAYER "PROXY ENV..." : deploy (if salt), proxy, sling behind proxy, wait for its join, fight sybil
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
n=$1; salt=$2; port=$3; pk=$4; player=$5; penv=$6
app() { awk -v n=$1 '$1==n{print $2}' $OUT/apps.txt | tail -1; }; cons() { awk -v n=$1 '$1==n{print $3}' $OUT/apps.txt | tail -1; }
if [ "$salt" != "-" ]; then read APP CONS BLK <<<"$(deploy_app 0x00000000000000000000000000000000000000000000000000000000000000$salt)"; echo "$n app=$APP cons=$CONS block=$BLK" | tee $OUT/$n-app.txt; echo "$n $APP $CONS" >> $OUT/apps.txt; mine 2; fi
nohup env BIND=0.0.0.0 PROXY_PORT=$port UPSTREAM=$RPC LOGFILE=$OUT/$n-proxy.jsonl TAG=$n $penv python3 -I $P/harness/qa/rpc_fault_proxy.py >> $OUT/$n-proxy.log 2>&1 &
sleep 1; RPC_NODE=http://host.docker.internal:$port start_node $n $(app $n) $pk; echo "$n sling started $(date +%T) block $(cast block-number -r $RPC) proxy env: $penv" | tee $OUT/$n-start.txt
for i in $(seq 1 200); do docker logs rp-$n 2>&1 | grep -q 'submit Hero action: join' && break; sleep 2; done
T=$(docker logs rp-$n 2>&1 | grep -o 'join tournament 0x[0-9a-fA-F]*' | head -1 | awk '{print $3}'); echo "$n sling join submitted at $(date +%T) block $(cast block-number -r $RPC) tournament $T" | tee -a $OUT/$n-start.txt
sleep 5; sybil $n $(app $n) $(cons $n) 0 $player fight; echo "$n sybil started $(date +%T)" | tee -a $OUT/$n-start.txt
