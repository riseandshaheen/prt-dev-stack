"""T3b: after a chain reset, drive the new chain past the nodes' saved height.
The new history reuses the same contract and tournament addresses: nodes must not act on the old one."""

from .. import stack
from ..core import MUST_HALT_SAFELY

ID = "T3b"
TITLE = "Nodes resume past their saved height on a reset chain"
OUTCOME = MUST_HALT_SAFELY


def run(h):
    h.start_epoch = 0
    obs = h.observe()
    ref = obs.get("reference") or {}
    # Past the saved height the nodes resume. The new chain has a different history under the
    # same contract and tournament addresses: they must not act on the old one.
    saved = max((obs.get("sling") or {}).get("latest_processed_block") or 0, ref.get("last_input_check_block") or 0)
    h.log(f"mining past the nodes' saved height {saved}")
    h.chain.mine(saved - h.chain.head() + 5)
    resume = h.chain.head()
    for _ in range(40):
        h.mine(10, settle=1.5)
    txs = h.txs_since(resume)
    node_txs = [x for x in txs if stack.NODE_OF.get(x["from"])]
    summary = sorted({(stack.NODE_OF[x["from"]], x["function"], x["ok"], x["revert"]) for x in node_txs})
    obs = h.observe()
    ref = obs.get("reference") or {}
    h.log(f"after passing the saved height: node txs {summary}; reference app {ref.get('state')} {ref.get('reason')}")
    for node in ("sling", "reference"):
        st = stack.state(node)
        h.log(f"{node} container: {st}")
        h.check(st["running"] and st["restarts"] == 0, f"{node} still running without restarts")
    moves = [x for x in node_txs if x["ok"] and x["function"] in
             ("joinTournament", "stageTournamentResult", "acceptStagedTournamentResult", "submitSentryClaim", "advanceMatch")]
    h.check(not moves, f"no settlement move built on the old history ({sorted({(stack.NODE_OF[x['from']], x['function']) for x in moves})})")
    h.check((ref.get("state") or "").upper() not in ("OK", "ENABLED") or not node_txs,
            f"the Go node halts or stays silent (app {ref.get('state')}, {len(node_txs)} txs)")
