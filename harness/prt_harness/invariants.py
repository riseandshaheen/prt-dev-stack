"""Invariants I1-I7 from the test design, evaluated on every tick.

Each check returns a list of violations: {"id", "epoch", "detail"}. A scenario's
outcome class decides which ids it tolerates.
"""

from __future__ import annotations

from collections import Counter, defaultdict

from . import stack

WASTE_BUDGET = 3   # reverted txs per node per epoch before I4 fires


def v(id_: str, detail: str, epoch: int | None = None) -> dict:
    return {"id": id_, "epoch": epoch, "detail": detail}


def i1_i2_agreement(obs: dict, since_epoch: int) -> list[dict]:
    """I1 final state and commitment, I2 input partition: chain and both nodes agree."""
    out = []
    for e in obs["epochs"]:
        if e["number"] < since_epoch:
            continue
        for check in e["checks"]:
            if check["verdict"] != "diverge":
                continue
            id_ = "I2" if check["name"] == "Input range" else "I1"
            out.append(v(id_, f"{check['name']} diverges: {check['values']}", e["number"]))
    return out


def i3_safety(obs: dict, since_epoch: int, honest: dict[int, str]) -> list[dict]:
    """I3: every accepted or staged state equals the honest oracle (both nodes' agreed state)."""
    out = []
    for e in obs["epochs"]:
        if e["number"] < since_epoch:
            continue
        staged = (e.get("staged") or {}).get("state")
        reported = [n["final_state"] for n in e["nodes"].values() if n.get("final_state")]
        finals = set(reported)
        # The oracle is both nodes agreeing on a closed epoch; one node alone is not enough.
        if len(reported) == len(e["nodes"]) >= 2 and len(finals) == 1:
            honest.setdefault(e["number"], finals.pop())
        oracle = honest.get(e["number"])
        if staged and oracle and staged != oracle:
            out.append(v("I3", f"staged {staged[:12]}… but honest state is {oracle[:12]}…", e["number"]))
    return out


def i4_waste(ledger: list[dict], epoch_of_block) -> list[dict]:
    """I4: reverted txs per node per epoch stay under the budget."""
    counts: Counter = Counter()
    reasons = defaultdict(Counter)
    for tx in ledger:
        node = stack.NODE_OF.get(tx["from"])
        if node and not tx["ok"]:
            key = (node, epoch_of_block(tx["block"]))
            counts[key] += 1
            reasons[key][f"{tx['function']}: {tx['revert']}"] += 1
    return [v("I4", f"{node} sent {n} reverted txs ({dict(reasons[(node, epoch)])})", epoch)
            for (node, epoch), n in counts.items() if n > WASTE_BUDGET]


def i5_bonds(joins: list[dict], recoveries: list[dict], winners: dict[str, str]) -> list[dict]:
    """I5: each tournament's bond is recovered at most once, and only to its claimer.

    joins/recoveries are decoded CommitmentJoined / BondRecovered events;
    winners maps tournament -> winning commitment root (when finished).
    """
    out = []
    claimer = {}
    for j in joins:
        claimer.setdefault((j["address"], j["args"]["commitment"].lower()), j["args"]["submitter"].lower())
    per_tournament = Counter(r["address"] for r in recoveries)
    for t, n in per_tournament.items():
        if n > 1:
            out.append(v("I5", f"tournament {t[:10]}… paid its bond {n} times"))
    for r in recoveries:
        args = r["args"]
        root = str(args.get("commitment", "")).lower()
        paid_to = str(args.get("claimer", "")).lower()
        expected = claimer.get((r["address"], root))
        if expected and paid_to and paid_to != expected:
            out.append(v("I5", f"bond of {root[:12]}… paid to {paid_to} but claimer was {expected}"))
        if winners.get(r["address"]) and winners[r["address"]] != root:
            out.append(v("I5", f"bond paid for {root[:12]}…, not the winner {winners[r['address']][:12]}…"))
    return out


def i6_terminal(obs: dict, baseline_restarts: dict[str, int], container_states: dict[str, dict]) -> list[dict]:
    """I6: reference app not FAILED/CORRUPTED; sling not crash-looping."""
    out = []
    ref = obs.get("reference") or {}
    status = (ref.get("state") or "").upper()
    if status and status not in ("OK", "ENABLED"):
        out.append(v("I6", f"reference app status {status}: {ref.get('reason')}"))
    for node, st in container_states.items():
        if st["restarts"] > baseline_restarts.get(node, 0):
            out.append(v("I6", f"{node} restarted {st['restarts'] - baseline_restarts.get(node, 0)} times"))
        if not st["running"] and not st["paused"]:
            out.append(v("I6", f"{node} is {st['status']} (exit {st['exit_code']})"))
    return out


def i7_convergence(obs: dict, chain, lag_limit: int = 10) -> list[dict]:
    """I7: nodes near finalized, and no stuck nonces on the PRT signers."""
    out = []
    for node in ("sling", "reference"):
        h = obs["health"].get(node, {})
        if not h.get("ok"):
            out.append(v("I7", f"{node} unreachable: {h.get('error')}"))
    sling_block = (obs.get("sling") or {}).get("latest_processed_block")
    if sling_block is not None and obs["finalized"] - sling_block > lag_limit:
        out.append(v("I7", f"sling {obs['finalized'] - sling_block} blocks behind finalized"))
    ref_block = (obs.get("reference") or {}).get("last_input_check_block")
    if ref_block is not None and obs["finalized"] - ref_block > lag_limit:
        out.append(v("I7", f"reference {obs['finalized'] - ref_block} blocks behind finalized"))
    for account in (stack.REFERENCE_PRT, stack.SLING):
        latest = int(chain.call("eth_getTransactionCount", [account, "latest"]), 16)
        pending = int(chain.call("eth_getTransactionCount", [account, "pending"]), 16)
        if pending < latest:
            out.append(v("I7", f"{account[:10]}… nonce gap: pending {pending} < latest {latest}"))
    return out
