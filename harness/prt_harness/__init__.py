"""Cross-implementation break-testing harness for prt-dev-stack.

See ../README.md and ../../test-design-claude.md. The harness is the only miner:
it turns Anvil's automine off, drives every block itself, and checks invariants
on a three-way view (chain, sling, reference node) after every tick.
"""

import os
import sys
from pathlib import Path

# The three-way observer is the visualiser's `vis` package, so the invariants are
# computed on the same model the UI shows. Point VIS_DIR at the directory that
# contains `vis/` when the visualiser lives elsewhere.
_DEFAULTS = [
    Path(__file__).resolve().parents[2] / "visualiser",
    Path(__file__).resolve().parents[3] / "visualiser" / "visualiser-by-claude",
]
for _candidate in ([Path(os.environ["VIS_DIR"])] if os.environ.get("VIS_DIR") else []) + _DEFAULTS:
    if (_candidate / "vis" / "__init__.py").exists():
        sys.path.insert(0, str(_candidate))
        break
else:  # pragma: no cover
    raise SystemExit("cannot find the visualiser's vis package; set VIS_DIR to the directory containing vis/")
