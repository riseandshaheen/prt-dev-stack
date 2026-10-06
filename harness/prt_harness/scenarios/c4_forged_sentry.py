"""C4 compromised public sentry key: a wrong sentry claim from account 7 before sling sends its own."""

from .. import stack
from ..core import MUST_SETTLE_HONEST
from vis import abi

ID = "C4"
TITLE = "Compromised public sentry key"
OUTCOME = MUST_SETTLE_HONEST
WRONG = "0x" + "de" * 32


def run(h):
    h.next_epoch()
    n = h.sealed()["epoch"]
    tx = h.chain.send(stack.SLING, stack.CONSENSUS, abi.encode_call("submitSentryClaim(uint256,bytes32)", n, WRONG))
    h.mine(1)
    rc = h.chain.receipt(tx)
    h.check(rc and rc["status"] == "0x1", f"forged sentry claim for epoch {n} landed")
    start = h.chain.head() + 1  # after the forgery's block
    h.run_until(lambda _o: h.sealed()["staged"], 800, 5, what="stage")
    staged_at = h.sealed()["staging_block"]
    period = h.chain.view(stack.CONSENSUS, "getClaimStagingPeriod()", ["uint256"])[0]
    h.run_until(lambda _o: h.sealed()["epoch"] > n, period + 100, 10, what="accept after the staging period")
    txs = h.txs_since(start)
    claims = h.by(txs, function="submitSentryClaim")
    accept = [x for x in h.by(txs, function="acceptStagedTournamentResult") if x["ok"]][0]
    reverted = [x for x in txs if not x["ok"] and stack.NODE_OF.get(x["from"])]
    h.log(f"sentry claims after the forgery: {[(x['ok'], x['revert']) for x in claims]}; "
          f"accept at +{accept['block'] - staged_at} by {stack.NODE_OF.get(accept['from'])}")
    h.check(len(claims) <= 1 and all(not x["ok"] for x in claims),
            f"sling skipped its own claim or tried once ({len(claims)} claim txs)")
    h.check(accept["block"] - staged_at >= period, f"settlement fell back to the {period}-block path (+{accept['block'] - staged_at})")
    h.check(len(reverted) <= 3, f"node reverts during the fallback: {len(reverted)} "
                               f"{sorted({(stack.NODE_OF.get(x['from']), x['function'], x['revert']) for x in reverted})}")
    obs = h.observe()
    e = next(x for x in obs["epochs"] if x["number"] == n)
    h.check(e["agreement"] == "agree", f"accepted state is the honest one: {[c['verdict'] for c in e['checks']]}")
