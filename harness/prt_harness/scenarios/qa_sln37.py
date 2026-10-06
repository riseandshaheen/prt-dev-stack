"""SLN-37: the sling stamps every tx with a fixed 15M gas. With a block gas limit below that,
or a balance below 15M * maxFee, every submit fails. Tested on the stack's own sling, which is
the app's only sentry; settings are restored afterwards."""

import time

from .. import stack
from ..chain import events
from ..core import MUST_SETTLE_HONEST

ID = "SLN-37"
TITLE = "Fixed 15M gas: low block gas limit or low balance wedges the sling"
OUTCOME = MUST_SETTLE_HONEST
DEFAULT_LIMIT = 30_000_000


def _epoch_under(h, label: str) -> dict:
    since = time.time()
    h.next_epoch()
    n = h.sealed()["epoch"]
    start = h.chain.head() + 1
    period = h.chain.view(stack.CONSENSUS, "getClaimStagingPeriod()", ["uint256"])[0]
    h.run_until(lambda _o: h.sealed()["epoch"] > n, period + 500, 10, what=f"epoch {n} to settle")
    txs = h.txs_since(start)
    sling = h.by(txs, "sling")
    accept = [x for x in h.by(txs, function="acceptStagedTournamentResult") if x["ok"]][0]
    staged_at = next(e["block"] for e in events(h.chain, stack.CONSENSUS, start, h.chain.head(), {"EpochStaged"}))
    logs = stack.logs_since("sling", since)
    errors = [l for l in logs.splitlines() if "gas" in l.lower() or "insufficient" in l.lower() or "fund" in l.lower()]
    (h.out_dir / ID).mkdir(parents=True, exist_ok=True)
    (h.out_dir / ID / f"sling-{label}.log").write_text(logs)
    out = {"epoch": n, "sling_txs": len(sling), "accept_delay": accept["block"] - staged_at,
           "accepted_by": stack.NODE_OF.get(accept["from"]), "errors": len(errors), "sample": errors[:3]}
    h.log(f"{label}: {out}")
    return out


def run(h):
    try:
        h.chain.call("anvil_setBlockGasLimit", [hex(10_000_000)])
        low = _epoch_under(h, "gas-limit-10M")
        h.check(low["sling_txs"] == 0, f"with a 10M block gas limit no sling tx lands ({low['sling_txs']})")
        h.check(low["accept_delay"] >= 1000, f"no sentry claim, so settlement takes the 1000-block path (+{low['accept_delay']})")
        h.check(low["errors"] > 0, f"sling logs its failing submits ({low['errors']} lines): {low['sample']}")
    finally:
        h.chain.call("anvil_setBlockGasLimit", [hex(DEFAULT_LIMIT)])
    balance = h.chain.call("eth_getBalance", [stack.SLING, "latest"])
    try:
        h.chain.call("anvil_setBalance", [stack.SLING, hex(10**15)])   # 0.001 ETH
        poor = _epoch_under(h, "balance-0.001")
        # At devnet fees (base fee ~7 wei) 15M gas costs far less than 0.001 ETH, so submits can still
        # land; what this variant shows is the rejections and the 15M reservation, not a full wedge.
        if poor["sling_txs"]:
            h.finding(f"With 0.001 ETH sling still landed {poor['sling_txs']} tx(s) after {poor['errors']} rejected submits: "
                      "at devnet fees the 15M reservation fits; the wedge needs balance < 15M * maxFee.")
        h.check(poor["errors"] > 0, f"sling logs its failing submits ({poor['errors']} lines): {poor['sample']}")
    finally:
        h.chain.call("anvil_setBalance", [stack.SLING, balance])
    ok = _epoch_under(h, "control")
    h.check(ok["sling_txs"] > 0 and ok["accept_delay"] < 1000, f"CONTROL: sling claims and settles fast again (+{ok['accept_delay']})")
