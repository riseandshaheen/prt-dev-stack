"""A second, disposable sling behind the fault-injecting RPC proxy.

The stack's own sling keeps talking to Anvil directly. A probe runs the same image with its
own state volume and signing key, so a fault only reaches the probe and the stack is never
left with a broken sling.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import stack

IMAGE = "ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5"
PROXY = Path(__file__).resolve().parent.parent / "qa" / "rpc_proxy.py"
MACHINE = Path(__file__).resolve().parent.parent.parent / "echo" / "machine-image-rootfs"

# Anvil account 9: never used by the stack.
PROBE_KEY = "0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6"
PROBE_ADDRESS = "0xa0ee7a142d267c1f36714e4a8f75612f20a79720"


class Proxy:
    def __init__(self, port: int, *args: str, log_path: Path | None = None):
        self.port = port
        self.log_path = log_path or Path(tempfile.mkstemp(suffix=".proxy.log")[1])
        self._log = open(self.log_path, "w")
        self.proc = subprocess.Popen([sys.executable, str(PROXY), "--listen", str(port), "--count", *args],
                                     stdout=self._log, stderr=subprocess.STDOUT)
        for _ in range(50):
            if self.proc.poll() is not None:   # e.g. the port is still held by a stale proxy
                raise RuntimeError(f"proxy on {port} exited at startup; see {self.log_path}")
            try:
                self.stats()
                if self.proc.poll() is None:
                    return
            except OSError:
                pass
            time.sleep(0.1)
        raise RuntimeError("proxy did not start")

    def stats(self) -> dict:
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/", timeout=3) as resp:
            return json.loads(resp.read())

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=5)
        self._log.close()


class ProbeSling:
    def __init__(self, name: str, rpc_port: int, key: str = PROBE_KEY):
        self.name = f"prt-harness-{name}"
        self.volume = f"{self.name}-state"
        self.remove()
        stack.docker("run", "-d", "--name", self.name, "--add-host", "host.docker.internal:host-gateway",
                     "-v", f"{MACHINE}:/machine:ro", "-v", f"{self.volume}:/state", IMAGE,
                     "--app-address", stack.APP, "--machine-path", "/machine",
                     "--web3-rpc-url", f"http://host.docker.internal:{rpc_port}", "--web3-chain-id", "31337",
                     "--state-dir", "/state", "--sleep-duration-seconds", "5",
                     "pk", "--web3-private-key", key)

    def logs(self) -> str:
        r = subprocess.run(["docker", "logs", self.name], capture_output=True, text=True)
        return r.stdout + r.stderr

    def state(self) -> dict:
        info = json.loads(stack.docker("inspect", self.name))[0]["State"]
        return {"running": info["Running"], "status": info["Status"], "exit_code": info["ExitCode"]}

    def has_db(self) -> bool:
        r = subprocess.run(["docker", "run", "--rm", "-v", f"{self.volume}:/state", "--entrypoint", "sh", IMAGE,
                            "-c", "test -f /state/db.sqlite3 && echo yes || echo no"], capture_output=True, text=True)
        return r.stdout.strip() == "yes"

    def query(self, sql: str) -> list:
        tmp = Path(tempfile.mkdtemp(prefix="probe-db-"))
        try:
            for f in ("db.sqlite3", "db.sqlite3-wal", "db.sqlite3-shm"):
                subprocess.run(["docker", "cp", f"{self.name}:/state/{f}", str(tmp / f)], capture_output=True)
            if not (tmp / "db.sqlite3").exists():
                return []
            conn = sqlite3.connect(tmp / "db.sqlite3")
            try:
                return conn.execute(sql).fetchall()
            finally:
                conn.close()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def remove(self):
        subprocess.run(["docker", "rm", "-f", self.name], capture_output=True)
        subprocess.run(["docker", "volume", "rm", "-f", self.volume], capture_output=True)
