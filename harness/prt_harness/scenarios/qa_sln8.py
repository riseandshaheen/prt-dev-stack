"""SLN-8: the sling pays mined reverts the Go node avoids by pre-simulation; a foreign first
claimer takes the bond. An outsider (Anvil account 2) joins the honest commitment first."""

import time

from .. import stack
from ..chain import events
from ..core import MUST_SETTLE_HONEST, ScenarioFailed
from ..sybil import Sybil, available

ID = "SLN-8"
TITLE = "Mined reverts and a foreign first claimer"
OUTCOME = MUST_SETTLE_HONEST
OUTSIDER = stack.ACCOUNTS[2]


def run(h):
    if not available():
        raise ScenarioFailed("sybil runner image is not built")
    sealed = h.next_epoch()
    n, t = sealed["epoch"], sealed["tournament"]
    stack.pause("sling")
    stack.pause("reference")
    for tx in h.chain.pending():
        if stack.NODE_OF.get(tx["from"]):
            h.chain.call("anvil_dropTransaction", [tx["hash"]])
    start = h.chain.head() + 1
    h.log(f"epoch {n} sealed; both nodes paused; an outsider joins the honest commitment first")
    outsider = Sybil(n, player=3, mode="honest")
    try:
        deadline = time.time() + 900
        while not outsider.saw("JOINED") and outsider.alive and time.time() < deadline:
            h.mine(1, settle=2)
        h.check(outsider.saw("JOINED"), "the outsider joined the honest commitment")
    finally:
        outsider.stop()
        stack.unpause("sling")
        stack.unpause("reference")
    h.run_until(lambda _o: h.can_stage()["finished"], 800, 2, settle=2, what="the tournament to finish")
    h.next_epoch()
    h.mine(200, settle=2)       # room for any node to call tryRecoveringBond on the outsider's behalf
    disposition, claimer, payment = h.chain.view(t, "bondRecovery()", ["uint8", "address", "uint256"])
    txs = h.txs_since(start)
    joins = events(h.chain, t, start, h.chain.head(), {"CommitmentJoined"})
    bonds = events(h.chain, t, start, h.chain.head(), {"BondRecovered"})
    reverted = {node: [x for x in h.by(txs, node) if not x["ok"]] for node in ("sling", "reference")}
    gas = {node: sum(int(h.chain.receipt(x["hash"])["gasUsed"], 16) for x in reverted[node]) for node in reverted}
    h.log(f"joins {[(j['args']['submitter'][:10]) for j in joins]}; bonds {[(b['args']['claimer'][:10], b['args']['payment']) for b in bonds]}")
    h.log(f"mined reverts: sling {[(x['function'], x['revert']) for x in reverted['sling']]} ({gas['sling']} gas); "
          f"reference {[(x['function'], x['revert']) for x in reverted['reference']]} ({gas['reference']} gas)")
    h.check(len(joins) == 1 and joins[0]["args"]["submitter"].lower() == OUTSIDER, "only the outsider's join landed")
    h.check(claimer.lower() == OUTSIDER, f"the bond the nodes defended belongs to the outsider ({claimer}, {payment} wei)")
    if not bonds:
        h.finding(f"No node recovers a foreign claimer's bond: {payment} wei stays in the tournament until the "
                  "outsider calls tryRecoveringBond itself.")
    if reverted["sling"] and not reverted["reference"]:
        h.finding(f"Mined reverts paid by sling only: {sorted({(x['function'], x['revert']) for x in reverted['sling']})}")
