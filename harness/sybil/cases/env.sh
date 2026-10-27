export PATH=$HOME/.foundry/bin:$PATH
S=${CASES_RUN:-/tmp/prt-cases}
P=$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")/../../.." && pwd)   # prt-dev-stack root
OUT=$S/run/out
IMG=ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.6
SYB=prt-harness/sybil-runner:3.0.0-alpha.6
RPC=http://127.0.0.1:8545
RPC_IN=http://host.docker.internal:8545
FACTORY=0xF8f6dFA3A3e40C10926839FFd8a221C87C8b2ccD
INPUTBOX=0xEbE9f4Dfc04ae10bBeE663859c3dc5A23f94eA3C
TPL_HASH=0xf345a724da144e7acb8897b73dcb972f08d7f7baae33922e721e70115b44cc98
TPL=$P/tester/machine-image
Z=0x0000000000000000000000000000000000000000
WCFG="(0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266,0,17,560,0xB4D253c7a110241561B3eD6d632846dF7d4e9Af7)"
PK0=0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80; AD0=0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266
PK1=0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d; AD1=0x70997970C51812dc3A010C7d01b50e0d17dc79C8
PK8=0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97; AD8=0x23618e81E3f5cdF7f54C3d65f7FBc0aBf5B21E8f
PK9=0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6; AD9=0xa0Ee7A142d267C1f36714E4a8F75612F20a79720
mine() { cast rpc anvil_mine "$1" --rpc-url $RPC >/dev/null; }
# deploy_app SALT_HEX [stagingPeriod] -> "app consensus block"
deploy_app() { local salt=$1 sp=${2:-50}
  local r; r=$(cast send --rpc-url $RPC --private-key $PK0 $FACTORY "newDaveApp(bytes32,uint256,address,address[],(address,uint8,uint8,uint64,address),bytes32)" $TPL_HASH $sp $Z "[]" "$WCFG" "$salt" --json)
  python3 -I -c 'import sys,json;r=json.load(sys.stdin);l=r["logs"][-1]["data"];print("0x"+l[26:66],"0x"+l[90:130],int(r["blockNumber"],16))' <<<"$r"; }
# start_node NAME APP PK [extra node args]
start_node() { local name=$1 app=$2 pk=$3; shift 3; local dd=$S/run/dd/$name; rm -rf "$dd"; mkdir -p "$dd"; docker rm -f rp-$name >/dev/null 2>&1
  docker run -d --name rp-$name --add-host host.docker.internal:host-gateway -e CARTESI_SLING_AUTH_PRIVATE_KEY=$pk -e RUST_LOG=info,cartesi_sling_node=debug \
    -v "$TPL":/tpl:ro -v "$dd":/data $IMG --template-path /tpl --data-dir /data --blockchain-id 31337 --polling-interval 1 --snapshot-gap-inputs 1 --app-address $app --blockchain-http-endpoint ${RPC_NODE:-$RPC_IN} "$@" pk >/dev/null; }
# sybil NAME APP CONS EPOCH PLAYER MODE [extra -e ...]
sybil() { local name=$1 app=$2 cons=$3 epoch=$4 player=$5 mode=$6; shift 6; docker rm -f syb-$name >/dev/null 2>&1
  docker run -d --name syb-$name --tmpfs /tmp:rw,size=6g --add-host host.docker.internal:host-gateway -v $P/harness/sybil:/harness:ro -v $TPL:/machine:ro \
    -e ENDPOINT=$RPC_IN -e CONSENSUS=$cons -e INPUT_BOX=$INPUTBOX -e APP=$app -e EPOCH=$epoch -e PLAYER=$player -e MODE=$mode "$@" $SYB /harness/sybil.lua >/dev/null; }
addinput() { cast send --rpc-url $RPC --private-key $PK1 $INPUTBOX 'addInput(address,bytes)' $1 $2 --json | python3 -I -c 'import sys,json;r=json.load(sys.stdin);print("input block",int(r["blockNumber"],16),"status",r["status"])'; }
sealed() { cast call $1 'getCurrentSealedEpoch()(uint256,uint256,uint256,bytes32,bytes32,address)' --rpc-url $RPC | tr '\n' ' '; echo; }
canstage() { cast call $1 'canStageTournamentResult()(bool,bool,bool,uint256,bytes32,bytes32)' --rpc-url $RPC | tr '\n' ' '; echo; }
standing() { cast call $1 'tournamentStanding()((uint8,bool,bool,bytes32,bytes32,bytes32,uint64,uint64))' --rpc-url $RPC; }
PK10=0xf214f2b2cd398c806f84e317254e0f0b801d0643303237d97a22a48e01628897; PK11=0x701b615bbdfb9de65240bc28bd21bbc0d996645a3dd57e7b12bc2bdf6f192c82
PK12=0xa267530f49f8280200edf313ee7af6b827f2a8bce2897751d06a843f644967b1; PK13=0x47c99abed3324a2707c28affff1267e45918ec8c3f20b8aa892e8b065d2942dd
PK16=0xea6c44ac03bff858b476bba40716402b03e41b8e97e276d1baec7c37d42484a0; AD16=0x2546BcD3c84621e976D8185a91A922aE77ECEc30
