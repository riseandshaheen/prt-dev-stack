# Monkey testing: sling and PRT only

Oct 6, 2026 · sling `ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5` as the only validator (reference node stopped) · seed 7 · 2.5 h · run `harness/runs/20261006-034131/MONKEY/` (`epochs.txt` has one line per epoch)

## Setup

- **Sling is the only validator.** It joins every root tournament, plays, stages, claims as the app's sentry, accepts and recovers bonds.
- **The stack's own sling reads the chain through the fault proxy**, so RPC faults reach the node that acts.
- **An oracle sling** (same image, unfunded key, direct to Anvil, no faults) computes every epoch independently. Chain, sling and oracle are compared on input count, final state and commitment once each epoch is accepted.
- **Each epoch gets a seeded random plan:**

| Fault | How often (of 73 epochs) |
| --- | --- |
| Inputs: 0, 1-10 or 60-70, payload 1 B to 20 KB | every epoch |
| Extra inputs in the sealing block, ordered ahead of the accept | 24 |
| Sling restart / hard kill / pause (20-200 blocks) | 19 / 9 / 15 |
| Phase of the sling fault: right after the seal, mid join window, at staging | random |
| Reorg depth 1-2 (within finality) | 40 |
| RPC: 400 ms latency / 20% `-32005` / 10% `-32603` / 30% lost send replies / clean | 18 / 7 / 15 / 17 / 16 |
| Base-fee spike to 50 gwei | 15 |
| Forged sentry claim with the sentry's key | 7 |

The harness used the event-driven clock: one block at a time while a node had something to do, jumps to two blocks before each deadline otherwise.

## Result

| | |
| --- | --- |
| Epochs | 73 |
| Inputs processed | 1,680 (up to 72 per epoch) |
| Chain / sling / oracle | **73 agree**, 0 diverge, 0 incomplete |
| Settlement | median 299 blocks (the join window); max 1,300 (the forged-sentry fallback) |
| Mined reverts by sling | **0** across every epoch |
| Sling crashes | **2** (epochs 38 and 73), both the same boot panic |

Every forged sentry claim (7 epochs) correctly fell back to the 1000-block staging path and settled the honest state. Reorgs that dropped sling's join, stage or accept were absorbed: sling rebuilt and resent the intent next tick. Lost send replies (17 epochs), latency and random errors only delayed actions. Restarts, kills and pauses at every phase, including mid-batch with 60+ inputs, never changed a result.

Mined reverts were zero because sling was alone. In the earlier dual-node runs every sling revert was a race lost to the reference node (SLN-8).

## Findings

**1. A restart during a transient RPC error kills the sling for good (SLN-39 class, new trigger).** Both crashes followed a planned restart or kill while the proxy injected `-32603` "internal error" into 10% of `eth_getLogs`:

```
thread 'main' (1) panicked at cartesi-rollups/node/src/blockchain_reader/mod.rs:134:14:
fail to get sealed epoch 0: TransportError(ErrorResp(ErrorPayload { code: -32603, message: "internal error", data: None }))
```

The boot query `.expect()`s, so one transient error at startup is fatal. This stack's compose file has no restart policy, so the node stays down. In epoch 73 the app sat with a decided but unstaged tournament for about 340 blocks until the harness restarted sling. Had the crash come before the join, the epoch would have ended with no winner.

This is the line SLN-39 reports, with `-32602`. The run adds:

- any code fails, not only `-32602`, including the most generic transient error a provider returns;
- a restart under a mildly flaky provider is enough; with a 10% error rate it happened 2 times in about 28 restart or kill events; and
- the consequence on a single-validator deployment is an unstaged epoch, or a lost join.

Worth adding to SLN-39 as cross-check evidence rather than a new finding.

**2. One failed call fails the whole tick (observation, not yet measured cleanly).** During an aborted run, a stale proxy injected `-32603` into 10% of `eth_getLogs` for half an hour. Sling logged "dispute planning failed, retrying next tick" 23 times and "blockchain read failed" 16 times in 10 minutes: it makes several calls per tick, and any single failure abandons the tick. A modest per-call error rate therefore becomes a large per-tick failure rate, which spends the sling's dispute clock under a flaky provider. Next step: measure ticks lost against the per-call error rate during a live dispute.

## Harness problems found during the run

All fixed. None was a sling bug, but each first looked like one:

- A killed run left its fault proxy holding the port; the next run's proxy failed to bind but looked started. Proxies now check their own process and are killed on exit, including on SIGTERM.
- The monkey loop did not refresh its view each step, so the clock never jumped and an epoch took 30 minutes.
- A reorg dropped sling's join; the clock, still using the pre-reorg view, jumped 292 blocks, and sling's resent join landed at the deadline. The tournament ended with no winner. The loop now re-observes after any fault, and the clock never jumps while a commitment is less than 3 blocks deep.
- Sealed-epoch going backwards after a reorg was read as "settled"; the loop now waits until the epoch is past the one under test, and agreement needs all three values present.

The monkey now also detects a crashed sling, records it with its last log lines and starts it again, so a long run survives a crash.

## Stack state

The reference node is stopped. Sling is back on direct Anvil. Rebuild the stack to bring the reference node back.
