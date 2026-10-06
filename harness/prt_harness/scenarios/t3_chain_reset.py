"""T3 chain reset under persisted state: restart Anvil; node volumes survive.
The same tournament addresses reappear with a different history; both nodes must halt safely."""

import time

from .. import stack
from ..core import MUST_HALT_SAFELY

ID = "T3"
TITLE = "Chain reset under persisted state"
OUTCOME = MUST_HALT_SAFELY


def run(h):
    before = h.chain.head()
    h.chain.automine(True)
    stack.restart("anvil")
    for _ in range(60):
        try:
            head = h.chain.head()
            break
        except Exception:  # noqa: BLE001
            time.sleep(1)
    h.chain.automine(False)
    h.log(f"Anvil restarted: head {before} -> {head}")
    h.start_epoch = 0
    # Drive the fresh chain through a few join windows and watch what the nodes send.
    start = h.chain.head()
    h.add_input(b"T3 input on the reset chain")
    for _ in range(80):
        h.mine(10, settle=1)
    txs = h.txs_since(start)
    node_txs = [x for x in txs if stack.NODE_OF.get(x["from"])]
    summary = sorted({(stack.NODE_OF[x["from"]], x["function"], x["ok"], x["revert"]) for x in node_txs})
    h.log(f"node txs on the reset chain: {summary}")
    ok_moves = [x for x in node_txs if x["ok"] and x["function"] in
                ("joinTournament", "stageTournamentResult", "acceptStagedTournamentResult", "submitSentryClaim")]
    obs = h.observe()
    ref = obs.get("reference") or {}
    h.log(f"reference app {ref.get('state')} {ref.get('reason')}; sling processed "
          f"{(obs.get('sling') or {}).get('latest_processed_block')}")
    for node in ("sling", "reference"):
        h.log(f"{node} container: {stack.state(node)}")
    h.check(not ok_moves, f"while behind their saved height, no node made a settlement move ({len(ok_moves)}: "
                          f"{sorted({(stack.NODE_OF[x['from']], x['function']) for x in ok_moves})})")
    h.check(len(node_txs) - len([x for x in node_txs if x["ok"]]) <= 10,
            f"no revert storm ({len(node_txs)} node txs)")
    ref_halted = (ref.get("state") or "").upper() not in ("OK", "ENABLED")
    if not ref_halted:
        h.finding("Neither node detects the reset: both log 'behind' warnings every tick and wait for the chain "
                  "to reach their saved height (the Go node's last check block, sling's event watermark), "
                  "with the app still reported OK.")
