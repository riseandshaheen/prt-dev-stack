"""SLN-39: a generic RPC error (-32602) on the startup EpochSealed query panics the sling.
A probe sling starts behind a proxy that answers every eth_getLogs with -32602."""

import time

from .. import stack
from ..core import MUST_SETTLE_HONEST
from ..probe import Proxy, ProbeSling

ID = "SLN-39"
TITLE = "Generic -32602 on startup getLogs panics the sling"
OUTCOME = MUST_SETTLE_HONEST


def _boot(h, mode: str, args: list[str]) -> dict:
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    proxy = Proxy(18601, *args, log_path=folder / f"proxy-{mode}.log")
    probe = ProbeSling(f"sln39-{mode}", 18601)
    try:
        for _ in range(30):            # 60 s
            h.mine(1, settle=2)
            if not probe.state()["running"] or probe.has_db():
                break
        time.sleep(3)
        out = {"state": probe.state(), "db": probe.has_db(), "stats": proxy.stats()}
        logs = probe.logs()
        (folder / f"probe-{mode}.log").write_text(logs)
        out["panic"] = next((l for l in logs.splitlines() if "panicked" in l or "fail to get sealed epoch" in l), None)
        h.log(f"{mode}: running={out['state']['running']} exit={out['state']['exit_code']} db={out['db']} "
              f"getLogs={out['stats']['counts'].get('eth_getLogs', 0)} panic={out['panic']}")
        return out
    finally:
        probe.remove()
        proxy.stop()


def run(h):
    control = _boot(h, "control", [])
    h.check(control["state"]["running"] and control["db"], "CONTROL: probe starts and creates its database")
    trigger = _boot(h, "trigger", ["--error-range=-32602:0"])
    h.check(not trigger["state"]["running"], f"TRIGGER: probe exits (exit code {trigger['state']['exit_code']})")
    h.check(not trigger["db"], "TRIGGER: no database created")
    h.check(trigger["panic"] is not None, f"TRIGGER: startup panic in the log: {trigger['panic']}")
