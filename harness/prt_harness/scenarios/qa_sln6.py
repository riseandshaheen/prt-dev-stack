"""SLN-6: bond recovery re-scans the whole tournament tree every tick. A read-only probe sling
(unfunded key) runs behind a counting proxy; reads per tick are measured as history grows."""

import time

from vis import abi

from .. import stack
from ..chain import events
from ..core import MUST_SETTLE_HONEST
from ..probe import Proxy, ProbeSling

ID = "SLN-6"
TITLE = "Per-tick reads grow with tournament history"
OUTCOME = MUST_SETTLE_HONEST
KEY = "0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97"     # Anvil 8
ADDRESS = "0x23618e81e3f5cdf7f54c3d65f7fbc0abf5b21e8f"
TICK = 5          # the probe's --sleep-duration-seconds
WINDOW = 60


def _measure(h, proxy) -> dict:
    before = proxy.stats()
    for _ in range(WINDOW // 5):
        h.mine(1, settle=5)
    after = proxy.stats()
    ticks = WINDOW / TICK
    calls = {k: after["calls"].get(k, 0) - before["calls"].get(k, 0) for k in after["calls"]}
    total = sum(after["counts"].values()) - sum(before["counts"].values())
    by_fn = {}
    for key, n in calls.items():
        if n:
            name = abi.FUNCTIONS.get(key.split(":")[1], {}).get("name", key.split(":")[1])
            by_fn[name] = by_fn.get(name, 0) + n
    return {"requests_per_tick": round(total / ticks, 1), "calls_per_tick": {k: round(v / ticks, 1) for k, v in
                                                                             sorted(by_fn.items(), key=lambda x: -x[1])}}


def _tournaments(h) -> int:
    roots = [e["args"]["tournament"].lower() for e in events(h.chain, stack.CONSENSUS, 0, h.chain.head(), {"EpochSealed"})]
    inner = events(h.chain, roots, 0, h.chain.head(), {"NewInnerTournament"}) if roots else []
    return len(roots) + len(inner)


def run(h):
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    balance = h.chain.call("eth_getBalance", [ADDRESS, "latest"])
    h.chain.call("anvil_setBalance", [ADDRESS, "0x0"])
    proxy = Proxy(18603, log_path=folder / "proxy.log")
    probe = ProbeSling("sln6", 18603, key=KEY)
    try:
        h.run_until(lambda o: (probe.query("select block from latest_processed") or [[0]])[0][0] >= o["finalized"] - 5,
                    300, 2, settle=3, what="the probe to catch up")
        samples = []
        for round_ in range(3):
            m = _measure(h, proxy)
            m["tournaments"] = _tournaments(h)
            m["epoch"] = h.sealed()["epoch"]
            samples.append(m)
            h.log(f"sample {round_}: {m}")
            h.next_epoch()
        (folder / "samples.txt").write_text("\n".join(map(str, samples)))
        first, last = samples[0], samples[-1]
        rec = lambda m: m["calls_per_tick"].get("bondRecovery", 0)
        h.log(f"bondRecovery calls per tick: {[rec(m) for m in samples]} for {[m['tournaments'] for m in samples]} tournaments")
        h.check(rec(last) > 0, "bondRecovery is called on idle ticks")
        if rec(last) > rec(first):
            h.finding(f"bondRecovery reads per idle tick grew from {rec(first)} to {rec(last)} as tournaments went "
                      f"from {first['tournaments']} to {last['tournaments']}")
    finally:
        (folder / "probe.log").write_text(probe.logs())
        probe.remove()
        proxy.stop()
        h.chain.call("anvil_setBalance", [ADDRESS, balance])
