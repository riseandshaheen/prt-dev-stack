"""R4b sentry path: pause sling before its sentry claim; the Go node must accept at stage + 1000."""

from .. import stack
from ..core import MUST_SETTLE_HONEST

ID = "R4"
TITLE = "Sentry path: sling paused after staging"
OUTCOME = MUST_SETTLE_HONEST


def run(h):
    h.next_epoch()
    n = h.sealed()["epoch"]
    # Pause sling at the seal, before it computes the epoch, so no claim of its can be queued.
    # Drop anything it already had pending.
    stack.pause("sling")
    for tx in h.by(h.chain.pending(), "sling"):
        h.chain.call("anvil_dropTransaction", [tx["hash"]])
    h.log(f"sling paused at the seal of epoch {n}")
    h.run_until(lambda _o: h.can_stage()["finished"], 800, 5, what="tournament to finish")
    try:
        h.run_until(lambda _o: h.sealed()["staged"], 200, 1, what="Go node to stage")
        staged_at = h.sealed()["staging_block"]
        period = h.chain.view(stack.CONSENSUS, "getClaimStagingPeriod()", ["uint256"])[0]
        h.log(f"staged at block {staged_at}; staging period {period}")
        claims = [x for x in h.txs_since(staged_at - 5) if x["function"] == "submitSentryClaim"]
        h.check(not claims, f"no sentry claim landed while sling was paused ({len(claims)})")
        h.run_until(lambda _o: h.sealed()["epoch"] > n, period + 100, 10, what="Go node to accept after the staging period")
        txs = h.txs_since(staged_at)
        accept = [x for x in h.by(txs, function="acceptStagedTournamentResult") if x["ok"]][0]
        delay = accept["block"] - staged_at
        early = [x for x in h.by(txs, "reference", "acceptStagedTournamentResult") if not x["ok"]]
        h.log(f"accepted by {stack.NODE_OF.get(accept['from'])} at block {accept['block']} (+{delay}); "
              f"early reverted accepts: {len(early)}")
        h.check(stack.NODE_OF.get(accept["from"]) == "reference", "the Go node accepted with sling down")
        h.check(period <= delay <= period + 15, f"accepted within 15 blocks of the staging period ending (+{delay})")
        h.check(len(early) == 0, f"no ClaimStagingPeriodNotOverYet spam ({len(early)} reverted accepts: "
                                  f"{sorted({x['revert'] for x in early})})")
    finally:
        stack.unpause("sling")
    start = h.chain.head()
    h.mine(20, settle=3)
    h.mine(20, settle=3)
    stale = [x for x in h.by(h.txs_since(start), "sling") if not x["ok"]]
    h.check(not stale, f"sling sent no stale txs after resuming: {[(x['function'], x['revert']) for x in stale]}")
