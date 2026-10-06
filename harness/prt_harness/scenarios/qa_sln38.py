"""SLN-38: a provider that omits one InputAdded makes the sling commit the wrong epoch.
A funded probe sling reads through a proxy that drops one input; the stack's own sling and the
Go node read Anvil directly, so on this stack the probe's wrong commitment meets honest ones."""

from .. import stack
from ..chain import events
from ..core import MUST_SETTLE_HONEST
from ..probe import PROBE_ADDRESS, Proxy, ProbeSling

ID = "SLN-38"
TITLE = "Dropped InputAdded makes the sling commit the wrong epoch"
OUTCOME = MUST_SETTLE_HONEST


def run(h):
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    count = h.chain.view(stack.INPUT_BOX, "getNumberOfInputs(address)", ["uint256"], stack.APP)[0]
    drop = count + 1    # the second of the two inputs added below
    proxy = Proxy(18602, "--drop-input-index", str(drop), log_path=folder / "proxy.log")
    probe = ProbeSling("sln38", 18602)
    try:
        h.run_until(lambda o: (probe.query("select block from latest_processed") or [[0]])[0][0] >= o["finalized"] - 5,
                    200, 2, settle=3, what="the probe to catch up")
        h.add_input(b"SLN-38 input A")
        h.add_input(b"SLN-38 input B")
        h.mine(3)
        h.log(f"inputs {count} and {drop} added; the proxy drops InputAdded index {drop} for the probe")
        sealed = h.next_epoch()
        n = sealed["epoch"]
        h.log(f"epoch {n} sealed with inputs {sealed['lower']}-{sealed['upper']}")
        h.run_until(lambda _o: probe.query(f"select count(*) from settlement_info where epoch_number = {n}")[0][0] > 0,
                    120, 2, settle=4, what="the probe to settle its view of the epoch")
        stored = probe.query(f"select count(*) from inputs where epoch_number = {n}")[0][0]
        probe_commitment = "0x" + probe.query(f"select hex(computation_hash) from settlement_info where epoch_number = {n}")[0][0].lower()
        obs = h.observe()
        e = next(x for x in obs["epochs"] if x["number"] == n)
        honest = {k: v["commitment"] for k, v in e["nodes"].items()}
        h.log(f"chain: {sealed['upper'] - sealed['lower']} inputs; probe stored {stored}; "
              f"probe commitment {probe_commitment[:14]}…; stack nodes {honest}")
        h.check(stored == sealed["upper"] - sealed["lower"] - 1, f"probe stored one input fewer than the chain ({stored})")
        h.check(probe_commitment not in honest.values(), "probe committed a different epoch than both honest nodes")
        h.check(not any("error" in l.lower() and "input" in l.lower() for l in probe.logs().splitlines()[-400:]),
                "probe logged no input error: the divergence is silent")
        # What happens on chain: does the probe bond its wrong commitment, and who wins?
        h.run_until(lambda _o: h.can_stage()["finished"], 3000, 1, settle=1.5, what="the epoch's tournament to finish")
        joins = events(h.chain, sealed["tournament"], 0, h.chain.head(), {"CommitmentJoined"})
        probe_joins = [j for j in joins if j["args"]["submitter"].lower() == PROBE_ADDRESS]
        winner = h.can_stage()["winner"].lower()
        h.log(f"joins {[(j['args']['submitter'][:10], j['args']['commitment'][:14]) for j in joins]}; winner {winner[:14]}…")
        h.check(bool(probe_joins), "probe bonded its wrong commitment on chain")
        h.check(winner in honest.values(), "the honest commitment still won on this stack")
        h.next_epoch()
    finally:
        (folder / "probe.log").write_text(probe.logs())
        probe.remove()
        proxy.stop()
