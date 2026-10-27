"""Run dave v3.0.0-alpha.6's Lua sybil player against the stack's Anvil, in its own container.

The image is built from harness/sybil/Dockerfile (see harness/README.md): the alpha.6 Lua PRT
client on cartesi/machine-emulator:0.22.0 with cast and jq. The player script is harness/sybil/sybil.lua.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from pathlib import Path

from . import stack

IMAGE = "prt-harness/sybil-runner:3.0.0-alpha.6"
HERE = Path(__file__).resolve().parent.parent
MACHINE = HERE.parent / "tester" / "machine-image"

# dave's Lua client stores a full machine snapshot per input under /tmp and fails to clean them,
# so a long replay can fill the disk (it did, twice). Cap its scratch and refuse to start when the
# host is low on space.
TMP_CAP = os.environ.get("SYBIL_TMP_CAP", "6g")
MIN_FREE_GB = 20
DOCKER_SAFETY = ["--tmpfs", f"/tmp:rw,size={TMP_CAP}"]


def check_disk() -> None:
    free = shutil.disk_usage("/").free / 2**30
    if free < MIN_FREE_GB:
        raise RuntimeError(f"only {free:.0f} GB free; the sybil/oracle needs {MIN_FREE_GB} GB headroom")


class Sybil:
    """One Lua player on one sealed epoch.

    mode: fight | join | honest | honest-fight (see sybil.lua). transition steers the dispute onto that
    transition (decimal string); None is dave's smoke divergence at meta-cycle 2^44. wrong_final also
    patches the last leaf so the claimed post-epoch state is the epoch's initial state.
    """

    def __init__(self, epoch: int, player: int = 2, mode: str = "fight", transition: str | None = None,
                 wrong_final: bool = False, app: str = stack.APP, consensus: str = stack.CONSENSUS,
                 machine: Path | str = MACHINE, tick: float = 1, name: str | None = None):
        check_disk()
        self.name = name or f"prt-harness-sybil-{player}"
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        self.lines: list[str] = []
        env = {
            "ENDPOINT": "http://host.docker.internal:8545", "CONSENSUS": consensus, "INPUT_BOX": stack.INPUT_BOX,
            "APP": app, "EPOCH": str(epoch), "PLAYER": str(player), "MODE": mode, "MACHINE": "/machine",
            "TICK": str(tick),
        }
        if transition is not None:
            env["TRANSITION"] = str(transition)
        if wrong_final:
            env["WRONG_FINAL"] = "1"
        env_args = [a for k, v in env.items() for a in ("-e", f"{k}={v}")]
        self.proc = subprocess.Popen([
            "docker", "run", "--rm", "--name", self.name, *DOCKER_SAFETY,
            "--add-host", "host.docker.internal:host-gateway",
            "-v", f"{HERE / 'sybil'}:/harness:ro", "-v", f"{Path(machine)}:/machine:ro",
            *env_args, IMAGE, "/harness/sybil.lua",
        ], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self):
        for line in self.proc.stdout:
            self.lines.append(line.rstrip())

    def saw(self, marker: str) -> bool:
        return any(marker in line for line in self.lines)

    def value(self, prefix: str) -> str | None:
        return next((l.split(" ", 1)[1] for l in self.lines if l.startswith(prefix + " ")), None)

    @property
    def alive(self) -> bool:
        return self.proc.poll() is None

    def stop(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)


def available() -> bool:
    return subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True).returncode == 0
