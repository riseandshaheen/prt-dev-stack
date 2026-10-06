"""Reference node posts a bond it cannot defend.

The reference node's PRT service (rollups-node feature/contracts-bump, internal/prt) joins the
root tournament, stages, accepts and recovers bonds, but has no code that advances, seals or
times out a match. Here it is the only validator: sling is paused before the epoch seals and
stays down. A dave sybil joins a rival commitment after the reference node's join.

Run r5_twin_defenders first as the control (same setup, sling live). This scenario halts the
application: rebuild the stack afterwards.
"""

import time

from .. import stack
from ..chain import events
from ..core import MUST_HALT_SAFELY, ScenarioFailed
from ..sybil import Sybil, available

ID = "REF-NODEF"
TITLE = "Reference node as the only validator cannot defend its own bond"
OUTCOME = MUST_HALT_SAFELY
WATCH_BLOCKS = 1200          # past the 1000-block claim staging period
SYBIL = stack.ACCOUNTS[2]    # dave player 3
PRT_VERBS = ("advanceMatch", "winMatchByTimeout", "eliminateMatchByTimeout", "sealInnerMatchAndCreateInnerTournament",
             "sealLeafMatch", "winLeafMatch", "winInnerTournament", "eliminateInnerTournament")


def run(h):
    if not available():
        raise ScenarioFailed("sybil runner image is not built")
    folder = h.out_dir / ID
    folder.mkdir(parents=True, exist_ok=True)
    try:
        # Pause right after the seal: pausing earlier would also remove the sentry claim for the
        # epoch in flight and cost a needless 1000-block staging fallback.
        sealed = h.next_epoch()
        stack.pause("sling")
        for tx in h.by(h.chain.pending(), "sling"):
            h.chain.call("anvil_dropTransaction", [tx["hash"]])
        h.log("sling paused at the seal: the reference node is the only validator")
        n, t = sealed["epoch"], sealed["tournament"]
        start = h.chain.head() + 1
        seal_block = next(e["block"] for e in events(h.chain, stack.CONSENSUS, 0, h.chain.head(), {"EpochSealed"})
                          if e["args"]["epochNumber"] == n)
        h.run_until(lambda _o: any(e["args"]["submitter"].lower() == stack.REFERENCE_PRT
                                   for e in events(h.chain, t, seal_block, h.chain.head(), {"CommitmentJoined"})),
                    60, 1, what="the reference node's join")
        ref_root = events(h.chain, t, seal_block, h.chain.head(), {"CommitmentJoined"})[0]["args"]["commitment"].lower()
        h.log(f"epoch {n}: reference node joined {ref_root[:14]}… and owns the root bond")

        sybil = Sybil(n, player=3, mode="fight")
        try:
            deadline = time.time() + 1800
            while not sybil.saw("HONEST") and sybil.alive and time.time() < deadline:
                time.sleep(2)
            h.check((sybil.value("HONEST") or "").lower() == ref_root,
                    "dave's Lua oracle computes the same honest commitment as the reference node")
            h.run_until(lambda _o: sybil.saw("JOINED") or not sybil.alive, 120, 1, settle=2, what="sybil join")
            h.check(sybil.saw("JOINED"), "sybil joined a rival commitment")
            # dave's own cadence: one block per ~1.5 s while a match is live.
            h.run_until(lambda _o: h.can_stage()["finished"], 3000, 1, settle=1.5, what="the tournament to finish")
        finally:
            sybil.stop()
            (folder / "sybil.log").write_text("\n".join(sybil.lines))
        result = h.can_stage()
        winner = result["winner"].lower()
        finished_at = h.chain.head()
        h.log(f"tournament finished at block {finished_at}: winner {winner[:14]}…")
        h.check(winner != ref_root, "the sybil's rival commitment won the tournament")

        txs = h.txs_since(start)
        ref_txs = h.by(txs, "reference")
        verbs = sorted({x["function"] for x in ref_txs})
        played = [x for x in ref_txs if x["function"] in PRT_VERBS]
        sybil_moves = sorted({x["function"] for x in txs if x["from"] == SYBIL})
        h.log(f"reference node txs during the dispute: {verbs}; sybil moves: {sybil_moves}")
        h.check(not played, f"the reference node sent no match move ({len(played)})")

        deleted = events(h.chain, t, start, h.chain.head(), {"MatchDeleted"})
        h.log(f"match deletions: {[(d['args']['reason'], d['args']['winnerCommitment']) for d in deleted]}")

        # Afterwards: bond ownership, app status, and whether anyone settles.
        watch_start = h.chain.head() + 1
        for _ in range(WATCH_BLOCKS // 10):
            h.mine(10, settle=1)
        after = h.txs_since(watch_start)
        disposition, claimer, payment = h.chain.view(t, "bondRecovery()", ["uint8", "address", "uint256"])
        obs = h.observe()
        ref = obs.get("reference") or {}
        s = h.sealed()
        h.log(f"{WATCH_BLOCKS} blocks later: sealed epoch {s['epoch']} staged={s['staged']}; bond disposition "
              f"{disposition}, claimer {claimer}, payment {payment}; reference app {ref.get('state')}: {ref.get('reason')}; "
              f"node txs {sorted({(stack.NODE_OF[x['from']], x['function'], x['ok']) for x in after if stack.NODE_OF.get(x['from'])})}")
        h.check(claimer.lower() == SYBIL, "the root bond now belongs to the sybil, not the reference node")
        h.check((ref.get("state") or "").upper() == "DIVERGED", f"the reference node flags the app DIVERGED ({ref.get('state')})")
        h.check(not s["staged"] and s["epoch"] == n, "no one stages the result: the epoch never settles")
        (folder / "summary.txt").write_text(
            f"epoch {n}\nreference root {ref_root}\nwinner {winner}\nfinished at {finished_at}\n"
            f"reference verbs {verbs}\nsybil moves {sybil_moves}\n"
            f"bond disposition {disposition} claimer {claimer} payment {payment}\n"
            f"reference app {ref.get('state')}: {ref.get('reason')}\nstaged after {WATCH_BLOCKS} blocks: {s['staged']}\n")
    finally:
        stack.unpause("sling")
