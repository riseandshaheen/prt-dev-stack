"""Run dave v3.0.0-alpha.5's Lua sybil player against the stack's Anvil, in its own container."""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path

from . import stack

IMAGE = "prt-harness/sybil-runner:3.0.0-alpha.5"
HERE = Path(__file__).resolve().parent.parent
MACHINE = HERE.parent / "echo" / "machine-image-rootfs"

# dave's Lua client stores a full machine snapshot per input under /tmp and fails to clean them,
# so a long replay can fill the disk (it did, twice). Cap its scratch and refuse to start when the
# host is low on space.
TMP_CAP = "6g"
MIN_FREE_GB = 20
DOCKER_SAFETY = ["--tmpfs", f"/tmp:rw,size={TMP_CAP}"]


def check_disk() -> None:
    free = shutil.disk_usage("/").free / 2**30
    if free < MIN_FREE_GB:
        raise RuntimeError(f"only {free:.0f} GB free; the sybil/oracle needs {MIN_FREE_GB} GB headroom")


class Sybil:
    def __init__(self, epoch: int, player: int = 2, mode: str = "fight"):
        check_disk()
        self.name = f"prt-harness-sybil-{player}"
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        self.lines: list[str] = []
        self.proc = subprocess.Popen([
            "docker", "run", "--rm", "--name", self.name, *DOCKER_SAFETY,
            "--add-host", "host.docker.internal:host-gateway",
            "-v", f"{HERE / 'sybil'}:/harness:ro", "-v", f"{MACHINE}:/machine:ro",
            "-w", "/dave/test/e2e/rollups",
            "-e", "ENDPOINT=http://host.docker.internal:8545",
            "-e", f"CONSENSUS={stack.CONSENSUS}", "-e", f"INPUT_BOX={stack.INPUT_BOX}", "-e", f"APP={stack.APP}",
            "-e", f"EPOCH={epoch}", "-e", f"PLAYER={player}", "-e", f"MODE={mode}", "-e", "MACHINE=/machine",
            "--entrypoint", "lua5.4", IMAGE, "/harness/sybil.lua",
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
