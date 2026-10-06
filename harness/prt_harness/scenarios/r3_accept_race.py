"""R3 accept race with same-block inputs: inputs ordered before and after the accept tx."""

from ..core import MUST_SETTLE_HONEST
from .. import stack

ID = "R3"
TITLE = "Accept race with same-block inputs"
OUTCOME = MUST_SETTLE_HONEST
HOLD_SECONDS = 90


def run(h):
    for variant in ("before", "after"):
        h.next_epoch()
        h.wait_finished()
        if not h.chain.pending():   # don't mine a node tx that should be held
            h.mine(2)
        pending = h.hold_for(lambda p: h.by(p, function="acceptStagedTournamentResult"), HOLD_SECONDS * 2,
                             "acceptStagedTournamentResult")
        # Mine staging/claims until an accept is waiting.
        guard = 0
        while not h.by(pending, function="acceptStagedTournamentResult") and guard < 60:
            h.mine(1)
            pending = h.hold_for(lambda p: h.by(p, function="acceptStagedTournamentResult"), 20, "accept")
            guard += 1
        accept = h.by(pending, function="acceptStagedTournamentResult")
        if not accept:
            h.check(False, f"[{variant}] no accept tx appeared")
            continue
        acc = accept[0]
        node = stack.NODE_OF.get(acc["from"])
        # Anvil orders by fee: tip above the node's to go first, below to go after.
        tip = 1000 if variant == "before" else 0
        before = h.sealed()
        hashes = [h.add_input(f"R3 {variant} {i}".encode(), priority_gwei=tip) for i in range(2)]
        block = h.chain.head() + 1
        h.mine(1)
        txs = h.chain.txs(block, block)
        order = [(x["position"], x["function"], stack.NODE_OF.get(x["from"], "harness")) for x in txs]
        h.log(f"[{variant}] block {block}, accept sent by {node}: {order}")
        after = h.sealed()
        pos_accept = next((x["position"] for x in txs if x["function"] == "acceptStagedTournamentResult" and x["ok"]), None)
        pos_inputs = [x["position"] for x in txs if x["hash"] in hashes]
        if pos_accept is None:
            h.check(False, f"[{variant}] accept did not land in block {block}")
            continue
        inputs_before = sum(p < pos_accept for p in pos_inputs)
        h.check((inputs_before == 2) if variant == "before" else (inputs_before == 0),
                f"[{variant}] ordering achieved: {inputs_before}/2 inputs ahead of the accept")
        h.log(f"[{variant}] new epoch {after['epoch']} bounds {after['lower']}-{after['upper']}")
        # Settle this epoch so both nodes report their boundaries; I2 is checked on every tick.
        h.next_epoch()
        obs = h.observe()
        e = next(x for x in obs["epochs"] if x["number"] == after["epoch"])
        rng = next(c for c in e["checks"] if c["name"] == "Input range")
        h.check(rng["verdict"] == "agree", f"[{variant}] I2 input range agrees for epoch {after['epoch']}: {rng['values']}")
