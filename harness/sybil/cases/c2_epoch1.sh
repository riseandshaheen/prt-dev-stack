#!/bin/bash
source ${CASES_RUN:-/tmp/prt-cases}/env.sh
app() { awk -v n=$1 '$1==n{print $2}' $OUT/apps.txt; }; cons() { awk -v n=$1 '$1==n{print $3}' $OUT/apps.txt; }
C=$(cons c2)
for i in $(seq 1 400); do n=$(cast logs --from-block 25 --address $C 'EpochSealed(uint256,uint256,uint256,bytes32,bytes32,address)' -r $RPC --json | python3 -I -c 'import sys,json;print(len(json.load(sys.stdin)))'); [ "$n" -ge 2 ] && break; sleep 5; done
echo "c2 epoch 1 sealed at $(date +%T) block $(cast block-number -r $RPC)"
# let the sling join first
for i in $(seq 1 120); do [ "$(docker logs rp-c2 2>&1 | grep -c 'submit Hero action: join')" -ge 2 ] && break; sleep 2; done
sybil c2a1 $(app c2) $(cons c2) 1 5 fight
for i in $(seq 1 200); do docker logs syb-c2a1 2>&1 | grep -q '^JOINED' && break; sleep 2; done
HON=$(docker logs syb-c2a1 2>&1 | grep '^HONEST' | cut -d' ' -f2); A=$(docker logs syb-c2a1 2>&1 | grep '^SYBIL' | cut -d' ' -f2); T=$(docker logs syb-c2a1 2>&1 | grep '^TOURNAMENT' | cut -d' ' -f2)
echo "c2 epoch1 root $T honest $HON A $A joined at $(date +%T) block $(cast block-number -r $RPC)"
for i in $(seq 1 300); do CHILD=$(cast logs --from-block 25 --address $T 'NewInnerTournament(bytes32,address,bytes32,bytes32,bytes32,bytes32,(bytes32,uint256,uint64,uint64,uint64,uint8,uint64,uint64),uint256)' -r $RPC --json | python3 -I -c 'import sys,json;l=json.load(sys.stdin);print(("0x"+l[0]["topics"][2][-40:])+" "+str(int(l[0]["blockNumber"],16)) if l else "")'); [ -n "$CHILD" ] && break; sleep 1; done
read CA CB <<<"$CHILD"; echo "c2 child $CA at block $CB, starting B at $(date +%T) block $(cast block-number -r $RPC)"
sybil c2b1 $(app c2) $(cons c2) 1 6 fight -e TOURNAMENT=$CA -e CREATION_BLOCK=$CB -e ROOT_LEVEL=1 -e TRANSITION=17592186044414 -e PATCH_SALT=c2b -e PARENT_TOURNAMENT=$T -e PARENT_ONE=$HON -e PARENT_TWO=$A -e PARENT_COMMITMENT=$HON
echo "B started $(date +%T)"
