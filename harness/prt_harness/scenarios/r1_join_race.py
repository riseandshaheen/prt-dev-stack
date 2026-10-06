"""R1 join race: hold the clock after a seal so both nodes' joins can land in one block."""

from ..core import MUST_SETTLE_HONEST

ID = "R1"
TITLE = "Join race"
OUTCOME = MUST_SETTLE_HONEST
HOLD_SECONDS = 90


def run(h):
    sealed = h.next_epoch()
    n, t = sealed["epoch"], sealed["tournament"]
    h.log(f"epoch {n} sealed, tournament {t}; holding the clock for both joins")
    # Nodes read finalized (head - 2): give them two blocks, then stop mining.
    if not h.chain.pending():   # don't mine a node tx that should be held
        h.mine(2)
    txs = h.hold_for(lambda p: {x["from"] for x in h.by(p, function="joinTournament")} >= {"0x976ea74026e726554db657fa54763abd0c3a0aa9", "0x14dc79964da2c08b23698b3d3cc7ca32193d9955"},
                     HOLD_SECONDS, "joinTournament from both nodes")
    joiners = sorted({x["from"] for x in h.by(txs, function="joinTournament")})
    race = len(joiners) == 2
    start = h.chain.head() + 1
    h.mine(1)
    if not race:
        h.finding(f"No join race: after {HOLD_SECONDS}s with the clock stopped, only "
                  f"{[h_ for h_ in joiners] or 'nobody'} had a join pending. Sling does not submit its own join "
                  f"while the Go node's join is unconfirmed, so the first-joiner race never forms on this stack.")
    block_txs = h.by(h.chain.txs(start, start), function="joinTournament")
    order = [(x["position"], "reference" if x["from"].endswith("0aa9") else "sling", x["ok"], x["revert"]) for x in block_txs]
    h.log(f"joins in block {start}: {order}")
    h.wait_finished()
    joins = h.by(h.txs_since(start), function="joinTournament")
    ok_joins = [x for x in joins if x["ok"]]
    h.check(len(ok_joins) == 1, f"exactly one successful join for epoch {n} ({len(ok_joins)})")
    if race:
        loser = [x for x in joins if not x["ok"]]
        h.check(loser and all(x["revert"] and "ClockAlreadyInitialized" in x["revert"] for x in loser),
                f"loser reverted with ClockAlreadyInitialized: {[x['revert'] for x in loser]}")
        h.check(len(loser) <= 1, f"loser did not retry its join ({len(loser)} reverted joins)")
    # Let the epoch settle and bonds be recovered.
    h.next_epoch()
    h.mine(30, settle=2)
    rec = h.by(h.txs_since(start), function="tryRecoveringBond")
    claimer = ok_joins[0]["from"] if ok_joins else None
    others = [x for x in rec if x["from"] != claimer]
    h.log(f"tryRecoveringBond calls: {[(x['from'][:10], x['ok'], x['revert']) for x in rec]}")
    h.check(any(x["from"] == claimer and x["ok"] for x in rec), "the claimer recovered its bond")
    if others:
        h.finding(f"Non-claimer bond recovery attempts: {[(x['from'], x['ok'], x['revert']) for x in others]}")
