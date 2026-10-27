#!/bin/bash
# mine one block per second until the stop file appears
export PATH=$HOME/.foundry/bin:$PATH
STOP=${1:-/tmp/ticker2.stop}
while [ ! -f "$STOP" ]; do cast rpc anvil_mine 1 --rpc-url http://127.0.0.1:8546 >/dev/null 2>&1; sleep 1; done
