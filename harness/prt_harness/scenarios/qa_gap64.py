"""Production snapshot gap (64): batch boundaries, restart mid-batch, and a seal that straddles
a batch. dave's e2e suite runs the sling at --snapshot-gap-inputs 2; the published image
defaults to 64, which is what this stack runs.

Every epoch is checked three ways (chain bounds, sling, reference node) once it is accepted.
The last step times dave's Lua oracle on the largest epoch, as a third commitment and as the
cost estimate for a full dispute at this size. Nothing here halts the stack.
"""

import subprocess
import time

from .. import stack
from ..core import MUST_SETTLE_HONEST
from ..sybil import DOCKER_SAFETY, HERE, IMAGE, MACHINE, check_disk

ID = "GAP64"
TITLE = "Sling at the production snapshot gap: batch boundaries, restart, straddling seal"
OUTCOME = MUST_SETTLE_HONEST
SIZES = [63, 64, 65, 129]


def _flood(h, n: int, tag: str, priority_gwei=None) -> list[str]:
    return [h.add_input(f"{tag} {i:04d} gap64 probe".encode(), priority_gwei=priority_gwei) for i in range(n)]


def _snapshot_mb() -> float | None:
    r = subprocess.run(["docker", "run", "--rm", "-v", "prt-sling_sling-state:/s", "alpine", "du", "-sm", "/s"],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.split()[0])
    except (IndexError, ValueError):
        return None


def run(h):
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    expected: dict[int, tuple[int, str]] = {}    # epoch -> (inputs, label)
    sizes_mb = []
    h.next_epoch()

    # 1. Batch-boundary sweep. Inputs land in the open epoch; the next accept seals it.
    for n in SIZES:
        _flood(h, n, f"size{n}")
        h.mine(2)
        s = h.next_epoch()
        expected[s["epoch"]] = (n, f"{n} inputs")
        sizes_mb.append((s["epoch"], n, _snapshot_mb()))
        h.log(f"epoch {s['epoch']} sealed with {s['upper'] - s['lower']} inputs (expected {n})")

    # 2. Restart mid-batch: 100 inputs = one full batch of 64 plus 36 pending at the restart.
    _flood(h, 100, "restart")
    h.mine(3)
    time.sleep(20)              # let the sling ingest and run the first batch
    processed = h.observe()["sling"].get("latest_processed_block")
    stack.restart("sling")
    h.log(f"sling restarted mid-batch (last processed block {processed})")
    s = h.next_epoch()
    expected[s["epoch"]] = (100, "100 inputs, sling restarted mid-batch")
    sizes_mb.append((s["epoch"], 100, _snapshot_mb()))

    # 3. Seal straddling a batch: 62 inputs in the open epoch, then 4 more in the same block as the
    #    accept that seals it, ordered ahead of the accept by fee.
    _flood(h, 62, "straddle")
    h.mine(2)
    h.wait_finished()
    h.mine(2)
    pending = []
    for _ in range(60):
        pending = h.hold_for(lambda p: h.by(p, function="acceptStagedTournamentResult"), 20, "accept")
        if h.by(pending, function="acceptStagedTournamentResult"):
            break
        h.mine(1)
    hashes = _flood(h, 4, "straddle-late", priority_gwei=1000)
    block = h.chain.head() + 1
    h.mine(1)
    order = [(x["position"], x["function"]) for x in h.chain.txs(block, block)]
    s = h.sealed()
    late_first = all(p < next(q for q, f in order if f == "acceptStagedTournamentResult")
                     for p, f in order if f == "addInput")
    h.check(late_first, f"the 4 late inputs landed ahead of the accept in block {block}")
    expected[s["epoch"]] = (66, "62 inputs + 4 in the sealing block")
    h.log(f"epoch {s['epoch']} sealed with {s['upper'] - s['lower']} inputs (expected 66)")

    # Accept the last epoch, then check every epoch three ways.
    h.next_epoch()
    h.mine(10, settle=3)
    obs = h.observe()
    for n, (count, label) in sorted(expected.items()):
        e = next((x for x in obs["epochs"] if x["number"] == n), None)
        if e is None:
            h.check(False, f"epoch {n} ({label}) not observed")
            continue
        verdicts = {c["name"]: c["verdict"] for c in e["checks"]}
        values = {c["name"]: c["values"] for c in e["checks"]}
        h.check(e["input_count"] == count, f"epoch {n} ({label}): chain holds {e['input_count']} inputs")
        h.check(e["status"] == "accepted" and all(v == "agree" for v in verdicts.values()),
                f"epoch {n} ({label}): {e['status']}, {verdicts}")
        if any(v == "diverge" for v in verdicts.values()):
            h.log(f"epoch {n} values: {values}")
    (folder / "snapshot_sizes.txt").write_text("\n".join(f"epoch {e}: {n} inputs, state dir {mb} MB"
                                                        for e, n, mb in sizes_mb))
    h.log(f"sling state dir: {sizes_mb}")

    # 4. Oracle timing on the largest epoch.
    big = next(n for n, (c, _) in expected.items() if c == max(SIZES))
    ref_commitment = next(x for x in obs["epochs"] if x["number"] == big)["nodes"]["reference"]["commitment"]
    check_disk()
    started = time.time()
    r = subprocess.run([
        "docker", "run", "--rm", *DOCKER_SAFETY, "--add-host", "host.docker.internal:host-gateway",
        "-v", f"{HERE / 'sybil'}:/harness:ro", "-v", f"{MACHINE}:/machine:ro", "-w", "/dave/test/e2e/rollups",
        "-e", "ENDPOINT=http://host.docker.internal:8545", "-e", f"CONSENSUS={stack.CONSENSUS}",
        "-e", f"INPUT_BOX={stack.INPUT_BOX}", "-e", f"APP={stack.APP}", "-e", f"EPOCH={big}", "-e", "DRYRUN=1",
        "-e", "MACHINE=/machine", "--entrypoint", "lua5.4", IMAGE, "/harness/sybil.lua",
    ], capture_output=True, text=True, timeout=3600)
    seconds = round(time.time() - started)
    honest = next((l.split()[1] for l in r.stdout.splitlines() if l.startswith("HONEST ")), None)
    (folder / "oracle.log").write_text(r.stdout + r.stderr)
    h.log(f"oracle on epoch {big} ({max(SIZES)} inputs, replaying all earlier epochs): {seconds}s, commitment {honest}")
    h.check(honest is not None and honest.lower() == (ref_commitment or "").lower(),
            "dave's Lua oracle agrees with the nodes on the largest epoch")
