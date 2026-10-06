"""Seeded monkey testing of the sling and PRT, without the reference node.

The sling is the only validator: it joins, defends, stages, claims as the sentry and accepts.
It reads the chain through the fault proxy. A read-only oracle sling (unfunded key, direct to
Anvil, no faults) computes every epoch independently. Each epoch gets a random plan: inputs
(count, size, some in the sealing block), a sling fault (restart, kill, pause shorter than the
join window), an RPC fault (latency, random -32005 or -32603), a reorg of depth 1-2 within
finality, and a base-fee spike. After each epoch settles, chain, sling and oracle are compared.

Run with HARNESS_NODES=sling. MONKEY_EPOCHS (default 12) and MONKEY_SEED (default 1) control it.
Stops the reference node; rebuild the stack afterwards to bring it back.
"""

import os
import random
import subprocess
import time
from pathlib import Path

from .. import stack
from ..core import MUST_SETTLE_HONEST, ScenarioFailed
from ..probe import Proxy, ProbeSling

ID = "MONKEY"
TITLE = "Seeded monkey testing: sling and PRT only"
OUTCOME = MUST_SETTLE_HONEST
PORT = 18620
ORACLE_KEY = "0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97"   # Anvil 8, unfunded here
ORACLE_ADDR = "0x23618e81e3f5cdf7f54c3d65f7fbc0abf5b21e8f"
ROOT = Path(__file__).resolve().parents[3]
EPOCH_BUDGET = 3000
RPC_MODES = {
    "pass": [],
    "latency": ["--delay-ms", "400"],
    "err-32005": ["--error-rate=-32005:0.2"],
    "err-32603": ["--error-rate=-32603:0.1", "--error-message", "internal error"],
    "send-lost": ["--send-lost", "0.3"],
}


def _compose_sling(*extra):
    files = ["-f", "compose.yaml"] + (["-f", str(ROOT / "harness/stack/sling-proxy.yaml")] if extra else [])
    subprocess.run(["docker", "compose", *files, "up", "-d"], cwd=ROOT / "sling", capture_output=True, check=True)


def _plan(rng: random.Random) -> dict:
    n = rng.choice([0, rng.randint(1, 10), rng.randint(60, 70)])
    return {
        "inputs": n,
        "size": rng.choice([1, 32, 1000, 4000, 4000, 20000]),
        "straddle": rng.randint(2, 6) if rng.random() < 0.3 else 0,
        "sling": rng.choice([None, None, "restart", "kill", "pause"]),
        "sling_phase": rng.choice(["after-seal", "mid-window", "at-stage"]),
        "pause_blocks": rng.randint(20, 200),
        "rpc": rng.choice(list(RPC_MODES)),
        "reorg": rng.choice([1, 2]) if rng.random() < 0.4 else 0,
        "reorg_phase": rng.choice(["after-seal", "mid-window", "at-stage"]),
        "basefee": rng.random() < 0.2,
        "forge": rng.random() < 0.1,     # wrong sentry claim with the sentry's public key
    }


def _hex32(b):
    return "0x" + b.hex() if isinstance(b, bytes) else (b if str(b).startswith("0x") else "0x" + str(b).lower())


def run(h):
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    epochs = int(os.environ.get("MONKEY_EPOCHS", "12"))
    deadline = time.time() + float(os.environ.get("MONKEY_HOURS", "100")) * 3600
    seed = int(os.environ.get("MONKEY_SEED", "1"))
    report = (folder / "epochs.txt").open("w")
    crashes = []

    stack.docker("stop", stack.CONTAINERS["reference"], check=False)
    h.chain.call("anvil_setBalance", [ORACLE_ADDR, "0x0"])
    proxy = Proxy(PORT, log_path=folder / "proxy-0.log")
    _compose_sling("proxy")
    oracle = ProbeSling("oracle", 8545, key=ORACLE_KEY)
    h.baseline_restarts["sling"] = stack.state("sling")["restarts"]
    h.log(f"monkey: seed {seed}, {epochs} epochs; reference node stopped; sling behind the proxy; oracle sling direct")
    try:
        h.next_epoch()
        for i in range(epochs):
            if time.time() > deadline:
                h.log(f"monkey: time budget reached after {i} epochs")
                break
            rng = random.Random(seed * 1000 + i)
            plan = _plan(rng)
            s0 = h.sealed()
            e = s0["epoch"]
            started, start_block = time.time(), h.chain.head() + 1
            proxy.stop()
            proxy = Proxy(PORT, *RPC_MODES[plan["rpc"]], log_path=folder / f"proxy-{e}.log")
            if plan["basefee"]:
                h.chain.call("anvil_setNextBlockBaseFeePerGas", [hex(50 * 10**9)])
            for k in range(plan["inputs"]):
                h.add_input(bytes([65 + k % 26]) * plan["size"])
            if plan["forge"]:
                from vis import abi as _abi
                h.chain.send(stack.SLING, stack.CONSENSUS,
                             _abi.encode_call("submitSentryClaim(uint256,bytes32)", e, "0x" + "de" * 32))
            done = set()

            def phase_now():
                if h.can_stage()["finished"]:
                    return "at-stage"
                detail = (h.observer.epoch_detail(e) or {}).get("epoch") or {}
                return "mid-window" if detail.get("commitment_count", 0) else "after-seal"

            def act(phase) -> bool:
                """Apply any fault due in this phase; True if something was done (re-observe before deciding)."""
                before = set(done)
                _act(phase)
                return done != before

            def _act(phase):
                if plan["sling"] and plan["sling_phase"] == phase and "sling" not in done:
                    done.add("sling")
                    if plan["sling"] == "restart":
                        stack.restart("sling")
                    elif plan["sling"] == "kill":
                        stack.docker("kill", stack.CONTAINERS["sling"])
                        stack.docker("start", stack.CONTAINERS["sling"])
                    else:
                        stack.pause("sling")
                        h.chain.mine(plan["pause_blocks"])
                        stack.unpause("sling")
                if plan["reorg"] and plan["reorg_phase"] == phase and "reorg" not in done:
                    done.add("reorg")
                    h.chain.call("anvil_reorg", [plan["reorg"], []])

            mined = 0
            while h.sealed()["epoch"] <= e:    # a reorg can move the chain back an epoch: wait until past e
                if mined > EPOCH_BUDGET:
                    raise ScenarioFailed(f"epoch {e} did not settle within {EPOCH_BUDGET} blocks (plan {plan})")
                st = stack.state("sling")
                if not st["running"] and not st["paused"]:
                    # Supervisor emulation: record the crash with its last log lines, then start it again.
                    tail = stack.docker("logs", "--tail", "6", stack.CONTAINERS["sling"], check=False)
                    crashes.append({"epoch": e, "exit": st["exit_code"], "log": tail[-600:]})
                    h.finding(f"sling exited (code {st['exit_code']}) in epoch {e}; restarted. Last lines: {tail[-300:]}")
                    stack.docker("start", stack.CONTAINERS["sling"])
                    continue
                h.observe()           # refresh the three-way view (and run the invariants) every step
                if act(phase_now()):
                    continue          # a fault changed the chain or the node: decide from a fresh view
                pending = h.chain.pending()
                if plan["straddle"] and "straddle" not in done and h.by(pending, function="acceptStagedTournamentResult"):
                    done.add("straddle")
                    for k in range(plan["straddle"]):
                        h.add_input(b"late" + bytes([k]), priority_gwei=1000)
                blocks, expect = h._next_move()
                h.chain.mine(blocks)
                mined += blocks
                if expect:
                    h.chain.wait_pending(bool, 6.0)
                else:
                    time.sleep(0.3)
            settled_in = h.chain.head() - start_block

            # Assess epoch e: chain vs sling vs oracle.
            h.chain.mine(3)
            time.sleep(4)
            obs = h.observe()
            view = next(x for x in obs["epochs"] if x["number"] == e)
            chain_state = (view.get("staged") or {}).get("state")
            chain_commit = next((c["values"].get("chain") for c in view["checks"] if c["name"] == "Commitment"), None)
            sling = view["nodes"].get("sling") or {}
            for _ in range(15):
                row = oracle.query(f"select hex(final_state), hex(computation_hash) from settlement_info where epoch_number = {e}")
                if row:
                    break
                h.chain.mine(1)
                time.sleep(2)
            o_state, o_commit = (("0x" + row[0][0].lower(), "0x" + row[0][1].lower()) if row else (None, None))
            o_inputs = (oracle.query(f"select count(*) from inputs where epoch_number = {e}") or [[None]])[0][0]
            values = {"chain": (chain_state, chain_commit), "sling": (sling.get("final_state"), sling.get("commitment")),
                      "oracle": (o_state, o_commit)}
            complete = all(v[0] and v[1] for v in values.values()) and view["status"] == "accepted"
            agree = complete and len(set(values.values())) == 1
            txs = h.by(h.txs_since(start_block), "sling")
            reverts = [f"{x['function']}:{x['revert']}" for x in txs if not x["ok"]]
            logs = stack.logs_since("sling", started - 2)
            panics = [l for l in logs.splitlines() if "panicked" in l]
            restarts = sum(1 for c in crashes if c["epoch"] == e)
            injected = sum(proxy.stats()["drops"].values())
            chaos = [k for k in ("sling", "reorg") if plan[k]]
            line = (f"EPOCH {e} | inputs in {view['input_count']} (oracle {o_inputs}) | next epoch +{plan['inputs']}"
                    f"{' +' + str(plan['straddle']) + ' in sealing block' if plan['straddle'] else ''} | "
                    f"rpc {plan['rpc']} ({injected} injected) | sling {plan['sling'] or '-'}"
                    f"{'@' + plan['sling_phase'] if plan['sling'] else ''} | reorg {plan['reorg'] or '-'}"
                    f"{'@' + plan['reorg_phase'] if plan['reorg'] else ''} | basefee {'spike' if plan['basefee'] else '-'} | "
                    f"forged sentry {'yes' if plan['forge'] else '-'} | "
                    f"settled in {settled_in} blocks | chain/sling/oracle {'AGREE' if agree else 'DIVERGE ' + str(values)} | "
                    f"sling txs {len(txs)}, reverts {len(reverts)} {sorted(set(reverts)) if reverts else ''} | "
                    f"panics {len(panics)}, crashes {restarts}")
            h.log(line)
            report.write(line + "\n")
            report.flush()
            h.check(agree, f"epoch {e}: chain, sling and oracle {'agree' if agree else 'DIVERGE' if complete else 'incomplete'}")
            h.check(not panics, f"epoch {e}: no sling panic ({panics[:1]})")
            if o_inputs is not None:
                h.check(o_inputs == view["input_count"], f"epoch {e}: oracle saw {o_inputs} of {view['input_count']} inputs")
    finally:
        report.close()
        (folder / "oracle.log").write_text(oracle.logs())
        oracle.remove()
        proxy.stop()
        _compose_sling()               # back to direct Anvil
        stack.unpause("sling")
