#!/usr/bin/env python3
"""Run harness scenarios against the live prt-dev-stack.

    python3 harness/run.py                 # every scenario, in catalog order
    python3 harness/run.py g0_golden c4_forged_sentry
"""

import argparse
import atexit
import signal
import subprocess
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from prt_harness import core, scenarios  # noqa: E402


def _cleanup():
    # A killed run must not leave fault proxies holding their ports.
    subprocess.run(["pkill", "-f", "harness/qa/rpc_proxy.py"], capture_output=True)


def main():
    atexit.register(_cleanup)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))   # so atexit runs on kill
    parser = argparse.ArgumentParser()
    parser.add_argument("names", nargs="*")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parent / "runs"))
    parser.add_argument("--settle", type=float, default=1.5, help="seconds to let nodes react after each mine")
    args = parser.parse_args()
    out = Path(args.out) / time.strftime("%Y%m%d-%H%M%S")
    harness = core.Harness(out, settle=args.settle)
    results = [core.run(harness, s) for s in scenarios.load(args.names or None)]
    (out / "summary.json").write_text(json.dumps([r.__dict__ for r in results], indent=2, default=str))
    print(f"\nbundle: {out}")
    for r in results:
        print(f"  {r.id:5} {r.status:7} {r.title}")
    return 0 if all(r.status == "passed" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
