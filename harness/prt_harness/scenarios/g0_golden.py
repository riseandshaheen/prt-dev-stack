"""Golden run: one epoch with an input, no faults. The honest oracle and a clock check."""

from ..core import MUST_SETTLE_HONEST

ID = "G0"
TITLE = "Golden epoch, harness as the only miner"
OUTCOME = MUST_SETTLE_HONEST


def run(h):
    start = h.sealed()
    h.add_input(b"harness golden input")
    h.mine(1)
    # Settle the sealed epoch, which seals the next one carrying our input.
    nxt = h.next_epoch()
    h.check(nxt["epoch"] == start["epoch"] + 1, f"epoch {start['epoch']} settled and epoch {nxt['epoch']} sealed")
    h.check(nxt["upper"] > nxt["lower"], f"epoch {nxt['epoch']} carries inputs {nxt['lower']}-{nxt['upper']}")
    after = h.next_epoch()
    obs = h.observe()
    e = next(x for x in obs["epochs"] if x["number"] == nxt["epoch"])
    h.check(e["status"] == "accepted", f"epoch {e['number']} accepted")
    h.check(e["agreement"] == "agree", f"chain, sling and reference agree on epoch {e['number']}: {[c['verdict'] for c in e['checks']]}")
