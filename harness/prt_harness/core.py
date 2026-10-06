"""Scenario runtime: clock controller, per-tick invariant checks, and the artifact bundle."""

from __future__ import annotations

import json
import os
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from vis import abi

from . import invariants as inv
from . import stack
from .chain import Chain, events
from .observer import Observer

MUST_SETTLE_HONEST = "MUST_SETTLE_HONEST"
MAY_LOSE_CONSISTENTLY = "MAY_LOSE_CONSISTENTLY"
MUST_HALT_SAFELY = "MUST_HALT_SAFELY"

# Invariants each outcome class may violate without failing the scenario.
TOLERATED = {
    MUST_SETTLE_HONEST: set(),
    MAY_LOSE_CONSISTENTLY: {"I3"},
    MUST_HALT_SAFELY: {"I1", "I2", "I3", "I6", "I7"},  # I6 here is the node halting, which is the point
}


# Event-driven clock: coarse waits (step > 1) jump to the next deadline when nothing is pending
# and no match is live, and wait for a node's transaction instead of sleeping a fixed time.
# HARNESS_FAST=0 restores the fixed-step clock, for re-checking anything that looks suspicious.
FAST = os.environ.get("HARNESS_FAST", "1") != "0"
NODE_TICK = 6.0      # longest node poll on this stack (sling sleeps 5 s), plus slack
# Nodes whose containers the invariants watch. HARNESS_NODES=sling runs the stack without the reference node.
NODES = tuple(n for n in os.environ.get("HARNESS_NODES", "sling,reference").split(",") if n)


class ScenarioFailed(AssertionError):
    pass


@dataclass
class Result:
    id: str
    title: str
    outcome_class: str
    status: str = "running"          # passed | failed | error | skipped
    checks: list = field(default_factory=list)      # {"ok", "text"}
    findings: list = field(default_factory=list)    # free-form observations worth reporting
    violations: list = field(default_factory=list)  # invariant violations, deduplicated
    start_block: int = 0
    end_block: int = 0
    started_at: float = 0
    seconds: float = 0
    error: str | None = None


class Harness:
    def __init__(self, out_dir: Path, settle: float = 1.5):
        self.chain = Chain()
        self.observer = Observer()
        self.out_dir = out_dir
        self.settle = settle
        self.timeline: list[dict] = []
        self.honest: dict[int, str] = {}
        self._seen: set = set()

    # ------------------------------------------------------------ lifecycle
    def begin(self, result: Result) -> None:
        stack.unpause_all()
        self.result = result
        self.timeline = []
        self._seen = set()
        self.baseline_restarts = {n: stack.state(n)["restarts"] for n in NODES}
        self.chain.automine(False)
        result.start_block = self.chain.head() + 1  # txs from here on are the scenario's
        result.started_at = time.time()
        self.start_epoch = self.sealed()["epoch"]
        self.log(f"begin at block {result.start_block}, sealed epoch {self.start_epoch}")

    def end(self) -> None:
        r = self.result
        stack.unpause_all()
        # Let anything in flight land, then check the whole window once more.
        self.mine(3)
        r.end_block = self.chain.head()
        ledger = self.chain.txs(r.start_block, r.end_block)
        self.ledger = ledger
        seal_blocks = sorted((e["block"], e["args"]["epochNumber"])
                             for e in events(self.chain, stack.CONSENSUS, max(0, r.start_block - 2000), r.end_block,
                                             {"EpochSealed"}))

        def epoch_of(block: int) -> int | None:
            current = None
            for b, n in seal_blocks:
                if b <= block:
                    current = n
            return current
        self._violate(inv.i4_waste(ledger, epoch_of))
        tournaments = sorted({e["args"]["tournament"].lower() for e in events(
            self.chain, stack.CONSENSUS, max(0, r.start_block - 2000), r.end_block, {"EpochSealed"})})
        if tournaments:
            tev = events(self.chain, tournaments, max(0, r.start_block - 2000), r.end_block,
                         {"CommitmentJoined", "BondRecovered"})
            self._violate(inv.i5_bonds([e for e in tev if e["name"] == "CommitmentJoined"],
                                       [e for e in tev if e["name"] == "BondRecovered"], {}))
        r.seconds = round(time.time() - r.started_at, 1)
        if r.status == "running":
            fatal = [x for x in r.violations if x["id"] not in TOLERATED[r.outcome_class]]
            failed = [c for c in r.checks if not c["ok"]]
            r.status = "failed" if (fatal or failed) else "passed"
        self._write_bundle()
        self.chain.automine(True)

    # --------------------------------------------------------------- clock
    def mine(self, blocks: int = 1, settle: float | None = None) -> dict:
        self.chain.mine(blocks)
        time.sleep(self.settle if settle is None else settle)
        return self.observe()

    def observe(self) -> dict:
        obs = self.observer.poll()
        self.last = obs
        self._violate(inv.i1_i2_agreement(obs, self.start_epoch))
        self._violate(inv.i3_safety(obs, self.start_epoch, self.honest))
        states = {n: stack.state(n) for n in NODES}
        self._violate(inv.i6_terminal(obs, self.baseline_restarts, states))
        return obs

    def run_until(self, predicate, max_blocks: int, step: int = 1, settle: float | None = None,
                  what: str = "condition") -> dict:
        mined = 0
        obs = self.observe()
        while not predicate(obs):
            if mined >= max_blocks:
                raise ScenarioFailed(f"timed out after {max_blocks} blocks waiting for {what}")
            if FAST and step > 1:
                blocks, expect = self._next_move()
                blocks = min(blocks, max_blocks - mined)
                self.chain.mine(blocks)
                mined += blocks
                if expect:   # a node should act now: wait for its tx, not a fixed time
                    self.chain.wait_pending(bool, NODE_TICK)
                else:
                    time.sleep(0.3)
                obs = self.observe()
            else:
                obs = self.mine(step, settle)
                mined += step
        return obs

    def _next_move(self) -> tuple[int, bool]:
        """How far the clock can safely jump now, and whether a node is expected to act after it.

        Jumps happen only when the mempool is empty and no match is live; the jump stops short of
        the next deadline (join window close, staging period end) so nodes act on time.
        """
        if self.chain.pending():
            return 1, True
        s = self.sealed()
        head = self.chain.head()
        if s["staged"]:
            agree = self.chain.view(stack.CONSENSUS, "canAcceptStagedTournamentResult()",
                                    ["bool", "bool", "bool", "uint256", "bytes32", "bytes32"])[1]
            if agree:
                return 1, True
            period = self.chain.view(stack.CONSENSUS, "getClaimStagingPeriod()", ["uint256"])[0]
            left = s["staging_block"] + period - head
            return (left - 2, False) if left > 4 else (1, True)
        if self.can_stage()["finished"]:
            return 1, True
        detail = self.observer.epoch_detail(s["epoch"]) or {}
        epoch = detail.get("epoch") or {}
        live = [m for m in ((detail.get("dispute") or {}).get("matches") or []) if m.get("phase") != "ended"]
        if epoch.get("commitment_count", 0) == 0 or live:
            return 1, True           # waiting for a join, or a match is live: real time only
        # Never jump on a commitment a shallow reorg could still remove.
        commitments = (detail.get("dispute") or {}).get("commitments") or []
        if any(c.get("block", head) > head - 3 for c in commitments):
            return 1, True
        close = epoch.get("join_window_closes_at")
        if close and close - head > 4:
            return close - head - 2, False
        return 1, True

    def hold_for(self, want, timeout: float, what: str) -> list[dict]:
        """Stop the clock until want(pending txs) holds (or timeout), then return the mempool."""
        txs = self.chain.wait_pending(want, timeout)
        self.log(f"held for {what}: {[(stack.NODE_OF.get(t['from'], t['from'][:8]), t['function']) for t in txs]}")
        return txs

    # ------------------------------------------------------------ chain views
    def sealed(self) -> dict:
        n, lower, upper, tournament, staged, staging_block, state, _ = self.chain.view(
            stack.CONSENSUS, "getCurrentSealedEpoch()",
            ["uint256", "uint256", "uint256", "address", "bool", "uint256", "bytes32", "bytes32"])
        return {"epoch": n, "lower": lower, "upper": upper, "tournament": tournament.lower(),
                "staged": staged, "staging_block": staging_block, "state": state}

    def can_stage(self) -> dict:
        finished, failed, staged, n, winner, state = self.chain.view(
            stack.CONSENSUS, "canStageTournamentResult()",
            ["bool", "bool", "bool", "uint256", "bytes32", "bytes32"])
        return {"finished": finished, "failed": failed, "staged": staged, "epoch": n,
                "winner": winner, "state": state}

    def next_epoch(self, max_blocks: int = 1500, step: int = 5) -> dict:
        """Advance until the consensus seals a new epoch."""
        start = self.sealed()["epoch"]
        self.run_until(lambda _o: self.sealed()["epoch"] > start, max_blocks, step, what=f"epoch {start + 1} to seal")
        return self.sealed()

    def wait_finished(self, max_blocks: int = 800, step: int = 5) -> dict:
        """Advance until the sealed epoch's tournament has a result that can be staged."""
        self.run_until(lambda _o: self.can_stage()["finished"], max_blocks, step, what="tournament to finish")
        return self.can_stage()

    def txs_since(self, block: int) -> list[dict]:
        return self.chain.txs(block, self.chain.head())

    @staticmethod
    def by(txs: list[dict], node: str | None = None, function: str | None = None) -> list[dict]:
        return [t for t in txs if (node is None or stack.NODE_OF.get(t["from"]) == node)
                and (function is None or t["function"] == function)]

    def add_input(self, payload: bytes, priority_gwei: int | None = None) -> str:
        # addInput(address appContract, bytes payload): head is (app, offset=0x40), then length and data.
        data = (abi.selector("addInput(address,bytes)") + stack.APP[2:].rjust(64, "0") + f"{64:064x}"
                + f"{len(payload):064x}" + payload.hex().ljust(((len(payload) + 31) // 32) * 64, "0"))
        return self.chain.send(stack.ACCOUNTS[1], stack.INPUT_BOX, data, priority_gwei=priority_gwei)

    # ------------------------------------------------------------ reporting
    def check(self, ok: bool, text: str) -> bool:
        self.result.checks.append({"ok": bool(ok), "text": text})
        self.log(("PASS " if ok else "FAIL ") + text)
        return ok

    def finding(self, text: str) -> None:
        self.result.findings.append(text)
        self.log("NOTE " + text)

    def log(self, text: str) -> None:
        entry = {"t": round(time.time(), 2), "block": self.chain.head(), "text": text}
        self.timeline.append(entry)
        print(f"  [{entry['block']}] {text}", flush=True)

    def _violate(self, items: list[dict]) -> None:
        for item in items:
            key = (item["id"], item["epoch"], item["detail"])
            if key in self._seen:
                continue
            self._seen.add(key)
            item = dict(item, block=self.chain.head())
            self.result.violations.append(item)
            self.log(f"VIOLATION {item['id']} epoch {item['epoch']}: {item['detail']}")

    def _write_bundle(self) -> None:
        r = self.result
        folder = self.out_dir / r.id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "result.json").write_text(json.dumps(r.__dict__, indent=2, default=str))
        (folder / "timeline.json").write_text(json.dumps(self.timeline, indent=2))
        (folder / "ledger.json").write_text(json.dumps(
            [{k: t[k] for k in ("block", "position", "hash", "from", "to", "function", "ok", "revert", "gas_limit", "gas_used", "gas_price")}
             | {"node": stack.NODE_OF.get(t["from"])} for t in getattr(self, "ledger", [])], indent=2))
        for node in NODES:
            (folder / f"{node}.log").write_text(stack.logs_since(node, r.started_at - 5))


def run(harness: Harness, scenario) -> Result:
    result = Result(scenario.ID, scenario.TITLE, scenario.OUTCOME)
    print(f"\n=== {scenario.ID}: {scenario.TITLE}", flush=True)
    try:
        harness.begin(result)
        scenario.run(harness)
    except ScenarioFailed as exc:
        result.status = "failed"
        result.error = str(exc)
        harness.log(f"FAIL {exc}")
    except Exception as exc:  # noqa: BLE001
        result.status = "error"
        result.error = f"{exc.__class__.__name__}: {exc}"
        harness.log(traceback.format_exc())
    finally:
        try:
            harness.end()
        except Exception as exc:  # noqa: BLE001
            result.status = "error"
            result.error = (result.error or "") + f"; teardown: {exc}"
            stack.unpause_all()
            harness.chain.automine(True)
    print(f"=== {scenario.ID}: {result.status.upper()} ({result.seconds}s)", flush=True)
    return result
