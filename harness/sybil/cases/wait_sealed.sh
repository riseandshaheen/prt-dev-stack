#!/bin/bash
# wait_sealed.sh CONS EPOCH MAXSECS -> prints when EpochSealed(EPOCH) appears
export PATH=$HOME/.foundry/bin:$PATH
for i in $(seq 1 $(( ${3:-1800} / 10 ))); do
  e=$(cast call $1 'getCurrentSealedEpoch()(uint256,uint256,uint256,bytes32,bytes32,address)' --rpc-url http://127.0.0.1:8545 | head -1)
  [ "$e" -ge "$2" ] && { echo "sealed epoch $e at block $(cast block-number -r http://127.0.0.1:8545) $(date +%T)"; exit 0; }
  sleep 10
done; echo TIMEOUT; exit 1
