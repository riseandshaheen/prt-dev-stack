"""#3 Log-range error codes. The sling splits an oversized eth_getLogs only when the error matches
one of --long-block-range-error-codes (default -32005, -32600, -32602, -32616). Real providers
answer with other codes too (dRPC free tier: 35; Cloudflare: -32047), and some reuse -32005 for
rate limiting (Merkle). A probe sling reads through a proxy that imitates them, with the range
limit scaled from 10,000 blocks to 100 so it binds on this short chain.

A: fresh start behind each provider. B: a running probe falls behind and must catch up through
dRPC. C: throttling, random -32005 vs an unlisted code, measuring request amplification.
"""

import time

from .. import stack
from ..core import MUST_SETTLE_HONEST
from ..probe import Proxy, ProbeSling

ID = "LOGRANGE"
TITLE = "Range-limit error codes outside the default list"
OUTCOME = MUST_SETTLE_HONEST
PORT = 18610
LIMIT = 100
PROVIDERS = [
    ("control-32602", -32602, "query exceeds max block range 100"),
    ("drpc-35", 35, "ranges over 100 blocks are not supported on free plan"),
    ("cloudflare-32047", -32047, "Invalid eth_getLogs request. 'fromBlock'-'toBlock' range too large. Max range: 100"),
]


def _processed(probe) -> int:
    rows = probe.query("select block from latest_processed")
    return rows[0][0] if rows else 0


def _tick(h, seconds: int):
    for _ in range(seconds // 2):
        h.chain.mine(1)
        time.sleep(2)


def run(h):
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    h.mine(LIMIT + 50)            # make sure startup and catch-up ranges exceed the limit

    # A. Fresh start behind each provider.
    for label, code, msg in PROVIDERS:
        proxy = Proxy(PORT, f"--error-range={code}:{LIMIT}", "--error-message", msg,
                      log_path=folder / f"A-{label}-proxy.log")
        probe = ProbeSling(f"logrange-{label}", PORT)
        try:
            _tick(h, 60)
            st, stats = probe.state(), proxy.stats()
            done = _processed(probe)
            lag = h.chain.head() - 2 - done
            logs = probe.logs()
            (folder / f"A-{label}-probe.log").write_text(logs)
            err = next((l for l in logs.splitlines() if "panicked" in l or "ERROR" in l), None)
            h.log(f"A {label}: running={st['running']} exit={st['exit_code']} processed={done} lag={lag} "
                  f"getLogs={stats['counts'].get('eth_getLogs', 0)} first error: {err}")
            if code == -32602:
                h.check(st["running"] and lag <= 5, f"A control: probe splits ranges and syncs (lag {lag})")
            else:
                h.check(not (st["running"] and lag <= 5),
                        f"A {label}: probe cannot sync through an unlisted range code "
                        f"(running={st['running']}, exit {st['exit_code']}, lag {lag})")
        finally:
            probe.remove()
            proxy.stop()

    # B. Running probe falls behind, then must catch up through dRPC's limit.
    proxy = Proxy(PORT, log_path=folder / "B-proxy-pass.log")
    probe = ProbeSling("logrange-catchup", PORT)
    try:
        _tick(h, 40)
        h.check(h.chain.head() - 2 - _processed(probe) <= 5, "B: probe synced through a pass-through proxy")
        proxy.stop()
        proxy = Proxy(PORT, f"--error-range=35:{LIMIT}", "--error-message", PROVIDERS[1][2],
                      log_path=folder / "B-proxy-drpc.log")
        stack.docker("pause", probe.name)
        h.chain.mine(LIMIT + 50)
        stack.docker("unpause", probe.name)
        before = _processed(probe)
        _tick(h, 60)
        after, st = _processed(probe), probe.state()
        lag = h.chain.head() - 2 - after
        h.log(f"B behind dRPC: processed {before} -> {after}, lag {lag}, running={st['running']} exit={st['exit_code']}, "
              f"injected {proxy.stats()['counts'].get('eth_getLogs', 0)} getLogs")
        h.check(lag > LIMIT, f"B: after a {LIMIT + 50}-block gap the probe stays stuck behind dRPC (lag {lag})")
        proxy.stop()
        proxy = Proxy(PORT, log_path=folder / "B-proxy-pass2.log")
        _tick(h, 40)
        lag2 = h.chain.head() - 2 - _processed(probe)
        h.log(f"B after switching back to pass-through: lag {lag2}")
        h.check(lag2 <= 5, f"B: the probe recovers as soon as the provider stops refusing (lag {lag2})")

        # C. Throttling: request load under random errors, while the probe follows the head.
        results = {}
        for label, args in [("baseline", []), ("rate-limit -32005", ["--error-rate=-32005:0.3"]),
                            ("rate-limit unlisted -32099", ["--error-rate=-32099:0.3"])]:
            proxy.stop()
            proxy = Proxy(PORT, *args, log_path=folder / f"C-{label.split()[0]}-{len(results)}.log")
            start_block = _processed(probe)
            _tick(h, 60)
            stats = proxy.stats()
            results[label] = {"getLogs_per_min": stats["counts"].get("eth_getLogs", 0),
                              "injected": stats["drops"].get("error-rate", 0),
                              "blocks_ingested": _processed(probe) - start_block,
                              "lag": h.chain.head() - 2 - _processed(probe)}
            h.log(f"C {label}: {results[label]}")
        (folder / "C-results.txt").write_text("\n".join(f"{k}: {v}" for k, v in results.items()))
        base = max(1, results["baseline"]["getLogs_per_min"])
        amp = results["rate-limit -32005"]["getLogs_per_min"] / base
        amp_unlisted = results["rate-limit unlisted -32099"]["getLogs_per_min"] / base
        h.log(f"C request amplification vs baseline: -32005 x{amp:.1f}, unlisted x{amp_unlisted:.1f}")
        if amp > 1.5 * max(1.0, amp_unlisted):
            h.finding(f"Rate-limit errors coded -32005 multiply the sling's eth_getLogs load x{amp:.1f} "
                      f"(unlisted code: x{amp_unlisted:.1f}): it splits ranges in response to throttling.")
    finally:
        (folder / "B-probe.log").write_text(probe.logs())
        probe.remove()
        proxy.stop()
