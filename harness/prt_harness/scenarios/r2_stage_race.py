"""R2 stage race and proof differential: same-block stage, then each node stages alone."""

from .. import stack
from ..core import MUST_SETTLE_HONEST

ID = "R2"
TITLE = "Stage race and proof differential"
OUTCOME = MUST_SETTLE_HONEST
HOLD_SECONDS = 90


def _stage(h, alone: str | None):
    """Run one epoch to its stage. alone: pause the other node so this one must stage."""
    other = {"sling": "reference", "reference": "sling"}.get(alone)
    h.next_epoch()
    if other:
        h.log(f"pausing {other} so {alone} must stage alone")
        stack.pause(other)
    try:
        h.run_until(lambda _o: h.can_stage()["finished"], 800, 5, what="tournament to finish")
        start = h.chain.head() + 1
        if not h.chain.pending():   # don't mine a node tx that should be held
            h.mine(2)
        pending = h.hold_for(lambda p: len({x["from"] for x in h.by(p, function="stageTournamentResult")}) >= (1 if alone else 2),
                             HOLD_SECONDS, "stageTournamentResult")
        h.mine(1)
        h.run_until(lambda _o: h.sealed()["staged"] or h.sealed()["epoch"] > h.can_stage()["epoch"], 200, 1,
                    what="result staged")
        stages = h.by(h.txs_since(start), function="stageTournamentResult")
        return pending, stages
    finally:
        if other:
            stack.unpause(other)


def run(h):
    # 1. Both live: same-block stage.
    pending, stages = _stage(h, None)
    stagers = {stack.NODE_OF.get(x["from"]) for x in h.by(pending, function="stageTournamentResult")}
    h.log(f"stage txs: {[(stack.NODE_OF.get(x['from']), x['block'], x['position'], x['ok'], x['revert']) for x in stages]}")
    if stagers != {"sling", "reference"}:
        h.finding(f"No stage race: with the clock held {HOLD_SECONDS}s only {sorted(stagers) or 'nobody'} had a "
                  "stage pending. The race is not reachable with both nodes live.")
    else:
        losers = [x for x in stages if not x["ok"]]
        h.check(all("TournamentResultAlreadyStaged" in (x["revert"] or "") for x in losers),
                f"stage loser reverted with TournamentResultAlreadyStaged: {[x['revert'] for x in losers]}")
    h.check(sum(x["ok"] for x in stages) == 1, "exactly one successful stage")

    # 2. Each implementation's MachineValidityProof on its own.
    for node in ("sling", "reference"):
        _, stages = _stage(h, node)
        mine = [x for x in stages if stack.NODE_OF.get(x["from"]) == node]
        h.log(f"{node} alone: {[(x['block'], x['ok'], x['revert']) for x in stages]}")
        h.check(any(x["ok"] for x in mine), f"{node}'s stageTournamentResult (its MachineValidityProof) accepted on its own")
