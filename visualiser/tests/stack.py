"""Boot a full test stack in-process: Anvil, a driven dispute, fake nodes, the visualiser.

    python3 tests/stack.py            # boot, run API checks, exit
    python3 tests/stack.py --hold 60  # boot and keep serving on :8799 for N seconds

Needs anvil on PATH (or ANVIL=...), and DAVE_DIR pointing at a dave checkout for sling's schema.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))

import drive  # noqa: E402
import fixtures  # noqa: E402
from vis.eth import Eth  # noqa: E402

ANVIL = os.environ.get("ANVIL", "anvil")  # Foundry 1.4.3, same as the stack
STATE = os.environ.get("STATE", str(HERE.parent.parent / "anvil" / "state.json"))
CHAIN, REF, VIS = 8645, 8646, 8799


def wait_http(url: str, data: bytes | None = None, timeout: float = 20):
    end = time.time() + timeout
    while time.time() < end:
        try:
            req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=2) as resp:
                return resp.read()
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    raise TimeoutError(url)


def get(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{VIS}{path}", timeout=5) as resp:
        return json.loads(resp.read())


@contextmanager
def stack():
    procs = []
    tmp = Path(tempfile.mkdtemp(prefix="vis-stack-"))
    try:
        procs.append(subprocess.Popen(
            [ANVIL, "--host", "127.0.0.1", "--port", str(CHAIN), "--slots-in-an-epoch", "1", "-a", "40",
             "--load-state", STATE, "--preserve-historical-states"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        if not fixtures.SCHEMA.exists():
            raise SystemExit(f"sling schema not found at {fixtures.SCHEMA}; set DAVE_DIR to a dave checkout")
        rpc = f"http://127.0.0.1:{CHAIN}"
        wait_http(rpc, b'{"jsonrpc":"2.0","id":1,"method":"eth_blockNumber","params":[]}')
        eth = Eth(rpc)
        result = drive.scenario(eth)
        from vis.keccak import keccak256
        state_a = "0x" + keccak256(b"sybil a final state").hex()

        # Sling agrees with sybil A's final state; the reference node computed something else.
        raw_inputs = []
        for index in range(3):
            logs = eth.call("eth_getLogs", [{"address": drive.INPUT_BOX, "fromBlock": "0x0", "toBlock": "latest"}])
        for log in logs:
            from vis import abi
            decoded = abi.decode_log(log)
            raw_inputs.append(bytes.fromhex(decoded["args"]["input"][2:]))
        fixtures.build_sling_db(
            tmp / "db.sqlite3", drive.APP,
            "0xc8217d7fa39a7a4ba65e5efacb1cfca9996dd76ceea945299fea9f4f2786f3b2",
            result["tournament"], state_a, result["roots"][0], raw_inputs, eth.block_number() - 2)
        ref = fixtures.FakeReference(
            REF, drive.APP,
            epochs=[{"index": "0x0", "status": "CLAIM_COMPUTED", "input_index_lower_bound": "0x0",
                     "input_index_upper_bound": "0x0", "first_block": "0x19", "last_block": "0x19",
                     "machine_hash": "0x" + "ab" * 32, "commitment": result["roots"][0],
                     "tournament_address": result["tournament"], "staged_at_block": None},
                    {"index": "0x1", "status": "OPEN", "input_index_lower_bound": "0x0",
                     "input_index_upper_bound": "0x0", "first_block": "0x1a", "last_block": "0x1a",
                     "machine_hash": None, "commitment": None, "tournament_address": None}],
            inputs=[{"index": hex(i), "epoch_index": "0x1", "status": s, "block_number": hex(26 + i),
                     "decoded_data": {"sender": drive.ACCOUNTS[1]}} for i, s in enumerate(["ACCEPTED", "ACCEPTED", "NONE"])],
            outputs=[{"index": "0x0", "input_index": "0x0"}, {"index": "0x1", "input_index": "0x0"}])
        ref_server = ref.serve()

        cfg = {
            "listen": {"host": "127.0.0.1", "port": VIS},
            "chain": {"rpc": rpc, "poll_seconds": 1, "dave_app_factory": "0xd34BEC37Fa5816ABA2f87BdaD2E13dd1B161370f"},
            "nodes": [
                {"id": "sling", "kind": "sling", "label": "Sling", "db": str(tmp / "db.sqlite3"),
                 "accounts": ["0x14dc79964da2c08b23698b3d3cc7ca32193d9955"]},
                {"id": "reference", "kind": "reference", "label": "Reference node", "rpc": f"http://127.0.0.1:{REF}/rpc",
                 "accounts": ["0xf39fd6e51aad88f6f4ce6ab8827279cfffb92266", "0x976ea74026e726554db657fa54763abd0c3a0aa9"]},
            ],
            "labels": {"0x3c44cdddb6a900fa2b585dd299e03d12fa4293bc": "Sybil A (account 2)",
                       "0x90f79bf6eb2c4f870365e785982e1f101e93b906": "Sybil B (account 3)",
                       "0x15d34aaf54267db7d7c367839aaf71a00a2c6a65": "Sybil C (account 4)"},
        }
        (tmp / "visualiser.json").write_text(json.dumps(cfg))
        procs.append(subprocess.Popen([sys.executable, str(HERE.parent / "server.py"), str(tmp / "visualiser.json")],
                                      stdout=sys.stdout, stderr=sys.stderr))
        wait_http(f"http://127.0.0.1:{VIS}/healthz")
        time.sleep(2.5)
        yield {"eth": eth, "result": result, "ref": ref, "tmp": tmp}
        ref_server.shutdown()
    finally:
        for p in reversed(procs):
            p.terminate()
            p.wait(timeout=5)


def checks(ctx) -> None:
    ov = get("/api/v1/overview")
    assert ov["chain"]["id"] == 31337, ov["chain"]
    assert [n["status"] for n in ov["nodes"]] == ["live", "live"], ov["nodes"]
    print("apps discovered:", [(a["address"], a["name"]) for a in ov["apps"]])
    app = next(a for a in ov["apps"] if a["address"] == drive.APP)
    assert app["name"] == "echo" and app["current_epoch"] == 0, app
    assert [e["status"] for e in app["epochs"]] == ["disputed", "open"], app["epochs"]
    assert any("disagree on final state" in a["text"] for a in ov["alerts"]), ov["alerts"]
    epoch = get(f"/api/v1/apps/{drive.APP}/epochs/0")
    sides = {c["submitter"]["address"]: c["side"] for c in epoch["dispute"]["commitments"]}
    assert sides[drive.ACCOUNTS[2]] == "honest", sides   # joined by a sybil account, matches sling
    assert sides[drive.ACCOUNTS[3]] == "rival", sides
    assert epoch["dispute"]["matches"][0]["phase"] == "bisecting"
    reverted = [t for t in epoch["transactions"] if t["status"] == "reverted"]
    assert reverted and reverted[0]["revert"] == "ClockAlreadyInitialized", reverted
    open_epoch = get(f"/api/v1/apps/{drive.APP}/epochs/1")
    rows = open_epoch["inputs"]
    assert [r["nodes"]["sling"]["status"] for r in rows] == ["stored"] * 3, rows
    assert rows[0]["nodes"]["reference"]["outputs"] == 2
    assert rows[1]["text"] == "<img src=x onerror=alert(1)>"  # raw in JSON; the UI must escape it
    ledger = get(f"/api/v1/apps/{drive.APP}/transactions")
    # 6 direct calls, plus the factory's newDaveApp: sent to the factory, recorded through the
    # EpochSealed its consensus emitted (the path a bot's multicall or helper contract takes).
    assert len(ledger) == 7, len(ledger)
    via = [t for t in ledger if t["via"]]
    assert [t["function"] for t in via] == ["newDaveApp"] and via[0]["touched"], via

    # A node going down must show as down, not as stale data.
    ctx["ref"].fail = True
    time.sleep(2.5)
    ov = get("/api/v1/overview")
    ref_node = next(n for n in ov["nodes"] if n["id"] == "reference")
    assert ref_node["status"] == "down" and "locked" in ref_node["error"], ref_node
    ctx["ref"].fail = False

    # Mining changes the version; the stream announces it.
    before = ov["version"]
    ctx["eth"].call("anvil_mine", ["0x5"])
    time.sleep(2.5)
    assert get("/api/v1/overview")["version"] > before

    # Chain reset: rewind below the indexed head and check it is detected.
    ctx["eth"].call("anvil_rollback", [8])
    time.sleep(2.5)
    ov = get("/api/v1/overview")
    assert any("rewound" in a["text"].lower() or "reorg" in a["text"].lower() for a in ov["alerts"]), ov["alerts"]
    print("all API checks passed")


if __name__ == "__main__":
    hold = float(sys.argv[sys.argv.index("--hold") + 1]) if "--hold" in sys.argv else 0
    with stack() as ctx:
        if hold:
            print(f"serving on http://127.0.0.1:{VIS} for {hold}s", flush=True)
            time.sleep(hold)
        else:
            checks(ctx)
