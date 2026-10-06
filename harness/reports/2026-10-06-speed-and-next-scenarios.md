# Harness speed, and scenarios to try next

Oct 6, 2026.

## Where the time went

A golden epoch took about 110 s for about 320 blocks. The harness itself is cheap: observing chain, sling and the reference node takes 0.13 s, and mining a block 3 ms. Nearly all the time was fixed sleeps, 1.5 s after every 5 blocks, while nothing could happen until a deadline 250 blocks away.

## What changed

**Event-driven clock** (`core.py`, on by default; `HARNESS_FAST=0` restores the old clock). For coarse waits:

- If a transaction is pending, mine one block and wait for the next transaction, not a fixed time.
- If the result is staged and the sentries agree, step one block at a time; otherwise jump to two blocks before the staging period ends.
- If the tournament has finished, step one block (staging is due).
- If no one has joined, or any match is live, step one block in real time: dispute clocks see exactly what they saw before.
- Otherwise, jump to two blocks before the join window closes.

After a jump, the harness waits up to 6 s (the sling's 5 s poll plus slack) only when a node is expected to act, returning as soon as its transaction appears. dave's own e2e harness does the same, fast-forwarding 128 blocks when no match awaits a move.

Measured: G0 went from 226 s to 64 s with identical checks. The same clock serves every `run_until` with a coarse step, so the 1000-block staging waits in R4, C4 and SLN-37 collapse to one jump.

**Non-destructive suite on the fast clock** (G0, R1, R2, R3, R4, C4): all pass with the same outcomes as the slow runs, in about 22 min instead of about 62 min.

| Scenario | Slow clock | Fast clock |
| --- | --- | --- |
| G0 | 226 s | 68 s |
| R1 | 233 s | 74 s |
| R2 | 1267 s | 420-433 s |
| R3 | 1064 s | 571 s |
| R4 | 433 s | 95 s |
| C4 | 413 s | 71 s |

**One integrity issue found and fixed.** On the fast clock, R2's race step first saw nobody's stage pending. The cause was in the scenario, not the clock's jumps: R1, R2 and R3 mined 2 blocks before holding the clock, and with faster turnaround the node's transaction was already pending, so those 2 blocks mined it and the race window was gone. They now mine those blocks only when the mempool is empty. After the fix, fast R2 matches slow R2 exactly (the reference node's stage held in the mempool, sling sending its sentry claim instead).

**Integrity guard.** Jumps never happen with a pending transaction, before the first join, or while a match is live, and they stop two blocks short of every deadline. Anything that looks off is re-run with `HARNESS_FAST=0` before it is reported.

## Further speed-ups, not done yet

| Idea | Gain | Cost | Note |
| --- | --- | --- | --- |
| Parallel stacks (port-offset compose overrides, harness reads ports from env) | 2-3x throughput | About 1 h | Each stack is light except for sybil runs. |
| Checkpoints instead of rebuilds after destructive tests | Saves about 1 min per rebuild | Blocked | The reference node cannot restart on a reloaded Anvil dump: it needs historical state the dump lacks (the SLN-9 mechanism). |
| A second app with a short claim staging period | — | — | Not needed now that the clock jumps the 1000-block wait. |
| Sybil scratch on a capped volume, plus a patched cleanup | Makes large-epoch disputes possible | About 1 h | dave's Lua client keeps one snapshot per input under `/tmp`; it filled the disk at 321 inputs. |

## Scenarios to try next

Ranked by value for new findings, avoiding the repo's existing findings and its rejection patterns:

1. **The defending sling behind the proxy.** Today faults only reach a probe sling, which never defends. Running the stack's own sling through a compose override pointing at the proxy unlocks the next three.
2. **Throttling or timeouts during a live dispute** (listed gap: throttling). Does the defender still move before its clock runs out under HTTP 429 or slow responses? Distinct from SLN-14 (latest lag) and SLN-16/39 (boot).
3. **Send response lost** (design F2). The proxy forwards `eth_sendRawTransaction` but drops the reply. The lane is stateless (known debt 6), so check for double sends, nonce wedges, and paid duplicates within one dispute.
4. **Reorg within finality, depth 2 or less** (design T1). `anvil_reorg` can drop a node's join, stage or accept transaction. Finality is trusted only beyond depth 2, so this is in scope. Fast to run.
5. **dRPC/Cloudflare during a dispute.** Extends the #3 draft: does a dispute that starts after the sling falls behind go undefended? It is the consequence the draft argues but did not show.
6. **Large-epoch dispute at gap 64** (#1 full). Needs the sybil scratch fix.
7. **Two honest slings defending concurrently** (listed gap). Mostly overlaps #8; low priority.
