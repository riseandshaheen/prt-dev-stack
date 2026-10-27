#!/bin/bash
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
read APP CONS BLK <<<"$(deploy_app 0x000000000000000000000000000000000000000000000000000000000000002b)"; echo "c9x app=$APP cons=$CONS block=$BLK" | tee $OUT/c9x-app.txt; echo "c9x $APP $CONS" >> $OUT/apps.txt; mine 2
sybil c9x1 $APP $CONS 0 17 join -e TRANSITION=70368744177663 -e PATCH_SALT=c9c; sybil c9x2 $APP $CONS 0 18 join -e TRANSITION=140737488355327 -e PATCH_SALT=c9d
for i in $(seq 1 120); do [ "$(docker logs syb-c9x1 2>&1 | grep -c '^JOINED')" -ge 1 ] && [ "$(docker logs syb-c9x2 2>&1 | grep -c '^JOINED')" -ge 1 ] && break; sleep 2; done
echo "both silent sybils joined at $(date +%T) block $(cast block-number -r $RPC)"
start_node c9x $APP 0x689af8efa8c651a91ad287602527f3af2fe9f6501a7ac4b061667b5a93e037fd
echo "c9x sling (account 17) started $(date +%T)"
