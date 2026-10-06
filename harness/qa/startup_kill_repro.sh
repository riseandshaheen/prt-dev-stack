#!/usr/bin/env bash
# A sling killed during startup never starts again: the startup clone leaves /state/snapshots/.work-1-0
# (PID 1 in a container, so the same name every start) and Storage::initialize fails on it before
# sweep_stale_staging can run.
#
# Uses a throwaway sling container with its own new volume (the stack's sling is untouched), reading
# the stack's Anvil directly. A read-only sidecar watches the volume for .work-1-0 and the script kills
# the node the moment it appears.
#   CONTROL : boot, let startup finish, kill, start      -> runs
#   TRIGGER : start, kill while .work-1-0 exists, start  -> exits "could not create `storage` ... File exists",
#             and again on every further start
#   OUT=/tmp/startup-kill bash harness/qa/startup_kill_repro.sh
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT="${OUT:-$ROOT/harness/runs/startup-kill-$(date +%Y%m%d-%H%M%S)}"; mkdir -p "$OUT"
STOP="${STOP:-kill}"   # kill = SIGKILL; stop = docker stop (SIGTERM, then SIGKILL after 10 s)
IMAGE=ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5
NAME=prt-harness-startkill
VOL=$NAME-state-$(date +%s)
APP=0x6c2e2f9665b8f941aa8d94ea3f0287f7884a6146
KEY=0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6   # Anvil account 9, unused by the stack

say(){ echo "$*" | tee -a "$OUT/summary.txt"; }
status(){ docker inspect -f '{{.State.Status}} exit={{.State.ExitCode}}' $NAME; }
work_exists(){ docker run --rm -v $VOL:/state:ro alpine test -d /state/snapshots/.work-1-0; }

docker rm -f $NAME >/dev/null 2>&1
docker create --name $NAME --add-host host.docker.internal:host-gateway \
  -v "$ROOT/echo/machine-image-rootfs:/machine:ro" -v $VOL:/state $IMAGE \
  --app-address $APP --machine-path /machine --web3-rpc-url http://host.docker.internal:8545 \
  --web3-chain-id 31337 --state-dir /state --sleep-duration-seconds 5 pk --web3-private-key $KEY >/dev/null
say "image $IMAGE, volume $VOL, docker $(docker version -f '{{.Server.Version}}')"

# CONTROL: first boot runs to completion, then a kill well after startup, then start.
docker start $NAME >/dev/null
for _ in $(seq 1 120); do docker logs $NAME 2>&1 | grep -q "epoch received\|disk after roll" && break; sleep 1; done
sleep 5
docker kill $NAME >/dev/null; docker start $NAME >/dev/null; sleep 20
say "CONTROL (kill after startup, start): $(status)"
docker logs $NAME > "$OUT/control.log" 2>&1

# TRIGGER: kill the moment the startup clone directory exists.
docker kill $NAME >/dev/null
docker start $NAME >/dev/null
hit=0
for _ in $(seq 1 200); do
  if work_exists; then t0=$(python3 -c 'import time;print(time.time())'); docker $STOP $NAME >/dev/null; hit=1; break; fi
done
t1=$(python3 -c 'import time;print(time.time())')
say "TRIGGER ($STOP): .work-1-0 seen during startup and node stopped: $hit (docker $STOP took $(python3 -c "print(round($t1-$t0,1))") s)"
docker run --rm -v $VOL:/state:ro alpine sh -c 'ls -la /state/snapshots; du -sh /state/snapshots/.work-1-0' > "$OUT/state-after-kill.txt" 2>&1
for i in 1 2 3; do
  docker start $NAME >/dev/null; sleep 12
  say "  start $i after the kill: $(status)"
done
docker logs $NAME 2>&1 | tail -8 > "$OUT/trigger-tail.log"
grep -m1 "unable to create directory" "$OUT/trigger-tail.log" | tee -a "$OUT/summary.txt"
say "container $NAME and volume $VOL kept for inspection; results in $OUT"
