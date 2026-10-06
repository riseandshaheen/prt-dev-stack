"""R5 twin defenders: sling is held until the Go node's join lands, then a sybil joins.
Sling must defend a commitment it did not submit, and the Go node must recover the bond."""

import time

from .. import stack
from ..core import MUST_SETTLE_HONEST, ScenarioFailed
from ..chain import events
from ..sybil import Sybil, available

ID = "R5"
TITLE = "Twin defenders"
OUTCOME = MUST_SETTLE_HONEST
SLING_DOWN = False   # R5b flips this


def run(h, sling_down: bool = SLING_DOWN):
    if not available():
        raise ScenarioFailed("sybil runner image is not built")
    sealed = h.next_epoch()
    n, t = sealed["epoch"], sealed["tournament"]
    stack.pause("sling")
    h.log(f"epoch {n} sealed; sling paused until the Go node joins")
    h.run_until(lambda _o: any(e["args"]["submitter"].lower() == stack.REFERENCE_PRT for e in
                               events(h.chain, t, sealed_block(h, n), h.chain.head(), {"CommitmentJoined"})),
                60, 1, what="the Go node's join")
    go_join = events(h.chain, t, sealed_block(h, n), h.chain.head(), {"CommitmentJoined"})[0]
    honest_root = go_join["args"]["commitment"].lower()
    h.log(f"Go node joined {honest_root[:14]}…; starting the sybil")
    sybil = Sybil(n, player=3, mode="fight")  # dave counts accounts from 1: player 3 is Anvil account 2
    try:
        # The sybil replays every past epoch from the template first; hold the clock meanwhile.
        deadline = time.time() + 1800
        while not sybil.saw("HONEST") and sybil.alive and time.time() < deadline:
            time.sleep(2)
        if not sybil.saw("HONEST"):
            raise ScenarioFailed(f"sybil did not build its commitment: {sybil.lines[-15:]}")
        h.check(sybil.value("HONEST") is not None, "sybil's oracle replay matched the chain for every sealed epoch")
        h.run_until(lambda _o: sybil.saw("JOINED") or not sybil.alive, 120, 1, settle=2, what="sybil join")
        h.check(sybil.saw("JOINED"), "sybil joined a rival commitment")
        if not sling_down:
            stack.unpause("sling")
            h.log("sling resumed")
        # Play the dispute at one block per ~1.5 s, dave's own cadence.
        h.run_until(lambda _o: h.can_stage()["finished"], 6000, 1, settle=1.5, what="the dispute to finish")
        result = h.can_stage()
        winner = result["winner"].lower()
        h.log(f"tournament finished; winner {winner[:14]}…, failed={result['failed']}; sybil: {sybil.lines[-3:]}")
        if sling_down:
            h.check(winner != honest_root, "with no active defender the sybil's commitment wins")
            return winner, honest_root, n
        h.check(winner == honest_root, "the honest commitment (joined by the Go node) won")
        h.check(any(x["function"] == "advanceMatch" and x["ok"]
                    for x in h.by(h.txs_since(sealed_block(h, n)), "sling")),
                "sling played the match for a commitment it did not submit")
        h.next_epoch(step=5)
        h.run_until(lambda _o: events(h.chain, t, sealed_block(h, n), h.chain.head(), {"BondRecovered"}),
                    300, 5, settle=2, what="bond recovery")
        bonds = events(h.chain, t, sealed_block(h, n), h.chain.head(), {"BondRecovered"})
        h.log(f"bonds: {[(b['args']['claimer'], b['args']['payment']) for b in bonds]}")
        h.check(any(b["args"]["claimer"].lower() == stack.REFERENCE_PRT for b in bonds),
                "the Go node (first joiner) recovered its bond")
    finally:
        sybil.stop()
        (h.out_dir / ID).mkdir(parents=True, exist_ok=True)
        (h.out_dir / ID / "sybil.log").write_text("\n".join(sybil.lines))
        if not sling_down:
            stack.unpause("sling")


def sealed_block(h, n):
    return next(e["block"] for e in events(h.chain, stack.CONSENSUS, 0, h.chain.head(), {"EpochSealed"})
                if e["args"]["epochNumber"] == n)
