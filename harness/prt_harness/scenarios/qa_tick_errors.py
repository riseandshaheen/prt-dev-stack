"""Sling under per-call RPC errors, and wedged behind a dRPC-style range limit, during real disputes.

Sling is the only validator (run with HARNESS_NODES=sling, reference node stopped) and reads the
chain through the fault proxy.

A: per-call error rate -> failed ticks. 10% / 20% / 30% of eth_getLogs fail with -32603; count
   the sling's "retrying next tick" warnings against its tick count over the same window.
B: dave's Lua sybil disputes an epoch while 20% (then 30%) of the sling's eth_getLogs fail.
   Does the honest sling still win?
C: the sling falls 150 blocks behind a dRPC-style limit (code 35, 100 blocks) right after it
   joins, then a sybil disputes. Leaves a rival winner on chain: rebuild afterwards.
"""

import subprocess
import time
from pathlib import Path

from .. import stack
from ..chain import events
from ..core import MUST_SETTLE_HONEST, ScenarioFailed
from ..probe import Proxy
from ..sybil import Sybil, available

ID = "TICKERR"
TITLE = "Sling under per-call RPC errors and a range-limit wedge, during disputes"
OUTCOME = MUST_SETTLE_HONEST
PORT = 18620
ROOT = Path(__file__).resolve().parents[3]
TICK = 5
DRPC = ["--error-range=35:100", "--error-message", "ranges over 100 blocks are not supported on free plan"]


def _compose_sling(proxy: bool):
    files = ["-f", "compose.yaml"] + (["-f", str(ROOT / "harness/stack/sling-proxy.yaml")] if proxy else [])
    subprocess.run(["docker", "compose", *files, "up", "-d"], cwd=ROOT / "sling", capture_output=True, check=True)


def _failed_ticks(since: float) -> int:
    return sum("retrying next tick" in l for l in stack.logs_since("sling", since).splitlines())


def _sling_root(h, t, seal_block):
    joins = events(h.chain, t, seal_block, h.chain.head(), {"CommitmentJoined"})
    mine = [j for j in joins if j["args"]["submitter"].lower() == stack.SLING]
    return mine[0]["args"]["commitment"].lower() if mine else None


def _dispute(h, folder, label, proxy_args, wedge=False) -> dict:
    """One sybil dispute with the sling behind the given proxy mode."""
    proxy = Proxy(PORT, log_path=folder / f"{label}-proxy-pass.log")
    try:
        sealed = h.next_epoch()
        n, t = sealed["epoch"], sealed["tournament"]
        seal_block = next(e["block"] for e in events(h.chain, stack.CONSENSUS, 0, h.chain.head(), {"EpochSealed"})
                          if e["args"]["epochNumber"] == n)
        h.run_until(lambda _o: _sling_root(h, t, seal_block), 120, 1, settle=2, what="the sling's join")
        root = _sling_root(h, t, seal_block)
        proxy.stop()
        proxy = Proxy(PORT, *proxy_args, log_path=folder / f"{label}-proxy.log")
        if wedge:     # fall behind further than the provider's range limit
            stack.pause("sling")
            h.chain.mine(150)
            stack.unpause("sling")
        since = time.time()
        sybil = Sybil(n, player=3, mode="fight")
        try:
            deadline = time.time() + 900
            while not sybil.saw("HONEST") and sybil.alive and time.time() < deadline:
                time.sleep(2)
            h.run_until(lambda _o: sybil.saw("JOINED") or not sybil.alive, 200, 1, settle=2, what="sybil join")
            h.run_until(lambda _o: h.can_stage()["finished"], 6000, 1, settle=1.5, what="the dispute to finish")
        finally:
            sybil.stop()
            (folder / f"{label}-sybil.log").write_text("\n".join(sybil.lines))
        winner = h.can_stage()["winner"].lower()
        txs = h.by(h.txs_since(seal_block), "sling")
        moves = [x for x in txs if x["function"] not in ("joinTournament", "submitSentryClaim", "stageTournamentResult",
                                                          "acceptStagedTournamentResult", "tryRecoveringBond")]
        out = {"epoch": n, "won": winner == root, "sling_moves": len(moves),
               "sling_reverts": sum(not x["ok"] for x in txs), "failed_ticks": _failed_ticks(since),
               "minutes": round((time.time() - since) / 60, 1), "injected": sum(proxy.stats()["drops"].values())}
        h.log(f"{label}: {out}")
        return out
    finally:
        proxy.stop()


def run(h):
    if not available():
        raise ScenarioFailed("sybil runner image is not built")
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    proxy = Proxy(PORT, log_path=folder / "A-pass.log")
    _compose_sling(proxy=True)
    try:
        h.next_epoch()
        # A. Per-call error rate -> failed ticks, measured over 120 s of normal epoch activity.
        rows = []
        for p in (0.1, 0.2, 0.3):
            proxy.stop()
            proxy = Proxy(PORT, f"--error-rate=-32603:{p}", "--error-message", "internal error",
                          log_path=folder / f"A-{p}.log")
            since, start = time.time(), proxy.stats()["counts"]
            for _ in range(24):
                h.chain.mine(1)
                time.sleep(TICK)
            stats = proxy.stats()
            ticks = round((time.time() - since) / TICK)
            failed = _failed_ticks(since)
            calls = sum(stats["counts"].values())
            rows.append((p, ticks, failed, calls, stats["drops"].get("error-rate", 0)))
            h.log(f"A p={p}: ~{ticks} ticks, {failed} failed ({failed / max(1, ticks):.0%}); "
                  f"{calls} RPC calls, {stats['drops'].get('error-rate', 0)} errors injected")
        (folder / "A-ticks.txt").write_text("\n".join(f"p={p} ticks={t} failed={f} calls={c} injected={i}"
                                                      for p, t, f, c, i in rows))
        proxy.stop()

        # B. Honest sling defending under per-call errors.
        for p in (0.2, 0.3):
            r = _dispute(h, folder, f"B-{p}", [f"--error-rate=-32603:{p}", "--error-message", "internal error"])
            h.check(r["won"], f"B p={p}: the honest sling still wins the dispute ({r})")
            if not r["won"]:
                h.finding(f"With {p:.0%} of eth_getLogs failing, the honest sling lost a dispute: {r}")
                return     # a rival winner halts the app; C would add nothing

        # C. Sling wedged behind a dRPC-style limit during a dispute.
        r = _dispute(h, folder, "C-drpc", DRPC, wedge=True)
        h.log(f"C: honest sling {'won' if r['won'] else 'LOST'} while wedged behind code 35")
        if not r["won"]:
            h.finding(f"A sling wedged behind dRPC's range-limit code could not defend: the sybil won ({r})")
    finally:
        proxy.stop()
        _compose_sling(proxy=False)
