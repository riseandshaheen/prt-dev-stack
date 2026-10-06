"""Sling killed (SIGKILL) and restarted at bad moments. Run with HARNESS_NODES=sling.

K1 epoch roll: kill right after EpochSealed, while the sling computes the epoch, at a few delays.
   Expect: after restart it joins the new tournament and the epoch settles.
K2 tx in flight: hold the clock until the sling's join is in the mempool, kill it, then
   a) mine it (the tx lands while the node is down), or
   b) drop it from the mempool (the tx is lost while the node is down).
   Expect: no duplicate or reverted tx in (a); a resend in (b).
K3 dispute: dave's Lua sybil disputes an epoch; the sling is killed every few of its moves, stays
   down a few blocks, and restarts. Expect: the honest sling still wins.
Kills are `docker kill` (no graceful shutdown); the state volume is kept.
"""

import os
import time

from .. import stack
from ..chain import events
from ..core import MUST_SETTLE_HONEST, ScenarioFailed
from ..sybil import Sybil, available

ID = "CRASH"
TITLE = "Sling killed and restarted at epoch roll, with a tx in flight, and mid-dispute"
OUTCOME = MUST_SETTLE_HONEST
SETUP = ("joinTournament", "submitSentryClaim", "stageTournamentResult", "acceptStagedTournamentResult",
         "tryRecoveringBond")


def _kill(h, down_blocks: int = 0) -> None:
    stack.docker("kill", stack.CONTAINERS["sling"])
    if down_blocks:
        h.chain.mine(down_blocks)
    time.sleep(1)
    stack.docker("start", stack.CONTAINERS["sling"])


def _sling_joins(h, t, since):
    return [j for j in events(h.chain, t, since, h.chain.head(), {"CommitmentJoined"})
            if j["args"]["submitter"].lower() == stack.SLING]


def _seal_block(h, n):
    return next(e["block"] for e in events(h.chain, stack.CONSENSUS, 0, h.chain.head(), {"EpochSealed"})
                if e["args"]["epochNumber"] == n)


def _crashes(h, since: float) -> list[str]:
    return [l for l in stack.logs_since("sling", since).splitlines() if "panicked" in l or " ERROR " in l]


def k1_epoch_roll(h, delay: float) -> dict:
    sealed = h.next_epoch()
    n, t = sealed["epoch"], sealed["tournament"]
    since = time.time()
    time.sleep(delay)
    _kill(h, down_blocks=2)
    h.run_until(lambda _o: _sling_joins(h, t, _seal_block(h, n)), 300, 1, settle=2, what=f"sling join after kill ({delay}s)")
    joins = _sling_joins(h, t, _seal_block(h, n))
    h.wait_finished()
    out = {"epoch": n, "delay_s": delay, "joins": len(joins), "commitment": joins[0]["args"]["commitment"][:12],
           "errors": len(_crashes(h, since))}
    h.log(f"K1 delay={delay}s: {out}")
    return out


def k2_inflight(h, drop: bool) -> dict:
    """Kill with the sling's join pending; mine it (drop=False) or drop it (drop=True)."""
    start = h.sealed()["epoch"]
    while h.sealed()["epoch"] == start:      # seal without letting the sling's join be mined
        h.chain.mine(1)
        time.sleep(0.3)
    sealed = h.sealed()
    n, t = sealed["epoch"], sealed["tournament"]
    seal = _seal_block(h, n)
    mine = []
    for _ in range(40):      # the sling reads finalized (head - 2): mine one block at a time until its join is pending
        pend = h.chain.wait_pending(lambda txs: any(x["from"] == stack.SLING for x in txs), 8)
        mine = [x for x in pend if x["from"] == stack.SLING]
        if mine:
            break
        h.chain.mine(1)
    h.log(f"held with sling join in mempool: {[(x['function'], x['nonce']) for x in mine]}")
    if not mine:
        raise ScenarioFailed("sling never submitted a join")
    since = time.time()
    stack.docker("kill", stack.CONTAINERS["sling"])
    if drop:
        for x in mine:
            h.chain.call("anvil_dropTransaction", [x["hash"]])
    h.chain.mine(1)
    stack.docker("start", stack.CONTAINERS["sling"])
    label = "K2b dropped" if drop else "K2a mined"
    try:
        h.run_until(lambda _o: _sling_joins(h, t, seal), 400, 1, settle=2, what=f"{label}: sling join")
        joined = True
    except ScenarioFailed:
        joined = False
    txs = h.by(h.txs_since(seal), "sling")
    out = {"epoch": n, "joined": joined, "sling_txs": [(x["function"], x["ok"], x.get("revert")) for x in txs],
           "reverts": sum(not x["ok"] for x in txs), "nonce_errors": sum("nonce" in l.lower() for l in _crashes(h, since))}
    h.log(f"{label}: {out}")
    if joined:
        h.wait_finished()
    return out


def k3_dispute(h, folder, every: int, down_blocks: int) -> dict:
    sealed = h.next_epoch()
    n, t = sealed["epoch"], sealed["tournament"]
    seal = _seal_block(h, n)
    h.run_until(lambda _o: _sling_joins(h, t, seal), 120, 1, settle=2, what="the sling's join")
    root = _sling_joins(h, t, seal)[0]["args"]["commitment"].lower()
    since, kills, seen = time.time(), 0, 0
    sybil = Sybil(n, player=3, mode="fight")
    try:
        deadline = time.time() + 900
        while not sybil.saw("HONEST") and sybil.alive and time.time() < deadline:
            time.sleep(2)
        h.run_until(lambda _o: sybil.saw("JOINED") or not sybil.alive, 200, 1, settle=2, what="sybil join")

        def step(_o):
            nonlocal kills, seen
            moves = [x for x in h.by(h.txs_since(seal), "sling") if x["function"] not in SETUP]
            if len(moves) >= seen + every:
                seen = len(moves)
                kills += 1
                _kill(h, down_blocks)
            return h.can_stage()["finished"]
        h.run_until(step, 8000, 1, settle=1.5, what="the dispute to finish")
    finally:
        sybil.stop()
        (folder / f"K3-{every}-sybil.log").write_text("\n".join(sybil.lines))
    winner = h.can_stage()["winner"].lower()
    txs = h.by(h.txs_since(seal), "sling")
    out = {"epoch": n, "won": winner == root, "kills": kills, "sling_moves": sum(x["function"] not in SETUP for x in txs),
           "reverts": sum(not x["ok"] for x in txs), "minutes": round((time.time() - since) / 60, 1),
           "errors": len(_crashes(h, since))}
    h.log(f"K3 every={every} down={down_blocks}: {out}")
    return out


def run(h):
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    h.next_epoch()
    h.wait_finished()

    for d in ([] if os.environ.get("CRASH_SKIP_K1") else (0.5, 3.0, 8.0)):
        r = k1_epoch_roll(h, d)
        h.check(r["joins"] == 1, f"K1 {d}s: sling joined once after the kill ({r})")

    a = k2_inflight(h, drop=False)
    h.check(a["joined"] and a["reverts"] == 0, f"K2a: join mined while down, no duplicate/revert ({a})")
    b = k2_inflight(h, drop=True)
    h.check(b["joined"], f"K2b: join dropped while down is resent after restart ({b})")
    if not b["joined"]:
        h.finding(f"A join lost from the mempool while the sling was down is never resent: {b}")
        return

    if not available():
        raise ScenarioFailed("sybil runner image is not built")
    for every, down in ((3, 2), (1, 5)):
        r = k3_dispute(h, folder, every, down)
        h.check(r["won"], f"K3 kill every {every} moves, {down} blocks down: honest sling wins ({r})")
        if not r["won"]:
            h.finding(f"Killing the sling every {every} moves during a dispute let the sybil win: {r}")
            return
