"""Addresses, accounts and container control for prt-dev-stack."""

from __future__ import annotations

import json
import subprocess
import time

RPC = "http://127.0.0.1:8545"
REFERENCE_RPC = "http://127.0.0.1:10011/rpc"

APP = "0x6c2e2f9665b8f941aa8d94ea3f0287f7884a6146"          # echo
CONSENSUS = "0x570c32cb7eac495d59e30c78d775facb4d027f68"
INPUT_BOX = "0xebe9f4dfc04ae10bbee663859c3dc5a23f94ea3c"

ACCOUNTS = [
    "0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266",  # 0 reference claims
    "0x70997970c51812dc3a010c7d01b50e0d17dc79c8",  # 1 inputs
    "0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc",  # 2 sybil (dave player 3)
    "0x90f79bf6eb2c4f870365e785982e1f101e93b906",  # 3 harness adversary
    "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65",  # 4
    "0x9965507d1a55bcc2695c58ba16fb37d819b0a4dc",  # 5
    "0x976ea74026e726554db657fa54763abd0c3a0aa9",  # 6 reference PRT signer
    "0x14dc79964da2c08b23698b3d3cc7ca32193d9955",  # 7 sling, and the app's only sentry
]
REFERENCE_PRT = ACCOUNTS[6]
SLING = ACCOUNTS[7]
NODE_OF = {ACCOUNTS[0]: "reference", REFERENCE_PRT: "reference", SLING: "sling"}

CONTAINERS = {
    "anvil": "prt-anvil-anvil-1",
    "sling": "prt-sling-node-1",
    "reference": "prt-reference-node-1",
    "database": "prt-reference-database-1",
}


def docker(*args: str, check: bool = True, timeout: float = 120) -> str:
    result = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def pause(node: str) -> None:
    docker("pause", CONTAINERS[node])


def unpause(node: str) -> None:
    docker("unpause", CONTAINERS[node], check=False)


def restart(node: str) -> None:
    docker("restart", CONTAINERS[node])


def state(node: str) -> dict:
    info = json.loads(docker("inspect", CONTAINERS[node]))[0]
    s = info["State"]
    return {"status": s["Status"], "running": s["Running"], "paused": s["Paused"],
            "restarts": info.get("RestartCount", 0), "started_at": s["StartedAt"],
            "exit_code": s["ExitCode"]}


def logs_since(node: str, since: float) -> str:
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(since))
    result = subprocess.run(["docker", "logs", "--since", stamp, CONTAINERS[node]],
                            capture_output=True, text=True, timeout=60)
    return result.stdout + result.stderr


def unpause_all() -> None:
    for node in ("sling", "reference"):
        unpause(node)
