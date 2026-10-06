"""R5b defender absent: as R5, but sling stays down through the timeout, so the sybil's
commitment wins. Both nodes must detect the divergence and stop rather than build on it."""

from .. import stack
from ..core import MUST_HALT_SAFELY
from . import r5_twin_defenders

ID = "R5b"
TITLE = "Defender absent"
OUTCOME = MUST_HALT_SAFELY
WATCH_BLOCKS = 1200   # past the 1000-block staging period


def run(h):
    try:
        winner, honest, n = r5_twin_defenders.run(h, sling_down=True)
    finally:
        stack.unpause("sling")
    start = h.chain.head() + 1
    for _ in range(WATCH_BLOCKS // 10):
        h.mine(10, settle=1)
    txs = h.txs_since(start)
    node_txs = [x for x in txs if stack.NODE_OF.get(x["from"])]
    obs = h.observe()
    ref = obs.get("reference") or {}
    s = h.sealed()
    h.log(f"{WATCH_BLOCKS} blocks after the rival win: sealed epoch {s['epoch']}, staged={s['staged']}; node txs "
          f"{sorted({(stack.NODE_OF[x['from']], x['function'], x['ok']) for x in node_txs})}; "
          f"reference app {ref.get('state')}")
    h.check(not [x for x in node_txs if x["ok"] and x["function"] in
                 ("stageTournamentResult", "acceptStagedTournamentResult", "joinTournament")],
            "neither node staged, accepted or joined on top of the rival result")
    h.check((ref.get("state") or "").upper() == "DIVERGED", f"the Go node flags the app DIVERGED ({ref.get('state')})")
    if not s["staged"] and s["epoch"] == n:
        h.finding(f"Epoch {n} never settles: the rival won with sling down, and no honest node stages its result, "
                  "so the application is halted for good (safety kept, liveness lost).")
