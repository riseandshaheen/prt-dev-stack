# PRT dev stack: cross-implementation break-testing design

Sep 29, 2026 · @Shaheen

## Purpose and scope

This system tests the one thing no existing suite tests: sling (dave `v3.0.0-alpha.5`) and the rollups reference node (`feature/contracts-bump`) running against each other on one Anvil. It also covers operational hazards specific to [prt-dev-stack](https://github.com/riseandshaheen/prt-dev-stack).

What the existing suites already cover, and this design does not repeat:

| Suite | Topology | Already covered |
| --- | --- | --- |
| [dave](https://github.com/cartesi/dave) `test/e2e/rollups` | One sling node vs Lua sybils, own devnet | Kills at every protocol point (join, settle, catch-up, commitment build, mid-match), seeded chaos kills, multi-sybil, sealed-leaf timeouts, sybil-vs-sybil GC, STF coverage, big input, deposit/withdrawal |
| [rollups-node](https://github.com/cartesi/rollups-node) `test/integration` | Go node alone, CLI-driven external actors | PRT lifecycle and staging, sentry settlement via CLI signers, passive dispute observer (win and loss), foreclosure, restart, same-block inputs, snapshot policy, reject/exception inputs, multi-app |
|  |  |  |

Neither repo ever runs the other implementation. Every scenario here targets either the interplay of the two nodes or a hazard of this stack. Node containers were not run while writing this; the Anvil state dump was booted on Anvil 1.4.3 and probed directly.

## Deployment facts that shape the design

In every dispute, sling is the only active defender, and its key is the app's only sentry. These values were read from the echo app's consensus at `0x570c32Cb7eAc495D59e30c78d775FacB4d027F68`.

| Fact | Value | Why it matters |
| --- | --- | --- |
| Go node's PRT actions | `JoinTournament`, `StageTournamentResult`, `AcceptStagedTournamentResult`, `TryRecoveringBond` only | It never advances, seals or times out a match. |
| Sling's standing lookup | By commitment root, not by sender | Both nodes compute the same root, so sling defends a commitment the Go node joined. The first joiner owns the bond (`claimers[root] = msg.sender`). |
| Sentries | 1, account 7 `0x14dC…9955` (sling's default key) | The key is public in `sling/compose.yaml`. |
| Sentry manager | `0x0` | The sentry can never be rotated. |
| Claim staging period | 1000 blocks | A disagreeing sentry is not a veto: it removes the fast path, and acceptance happens after 1000 blocks anyway. |
| Root bond | 0.3335 ETH | Basis for bond-conservation checks. |
| Starting state | Epoch 0 sealed at block 25, 0 inputs | Checkpoint baseline. |
| Tournament clock | `block.number` | The harness is the clock. |
| Mining | Anvil automines every tx | Node and adversary txs advance tournament clocks. |
| Finality | `finalized` = head − 2 | Anvil allows a depth-3 reorg past it (verified). |
| Epoch boundary | `acceptStagedTournamentResult` snapshots `getNumberOfInputs()` | Input partition depends on who wins the accept race and in-block order. |

Signers: the Go node claims with account 0 and sends PRT txs with account 6; sling uses account 7.

## Harness architecture

The harness is the only miner, and every node talks to Anvil through its own fault proxy. It is written in Python (pytest, web3.py, Docker SDK), because the stack is compose-driven and both reference harnesses are tied to their own repos.

&#91;embedded content: harness architecture · 5 components around the stack\]

With automine off, node txs wait in the mempool until the clock controller mines, so it can put both nodes' txs in one block and order them by gas price. For real disputes, the adversary runs dave's sybil runner, built from `test/Dockerfile` at `v3.0.0-alpha.5` and pointed at this Anvil, reusing `patched_commitment` and `dummy_commitment`.

## Invariants

The checker evaluates these every tick, not only at the end of a run. Any violation fails the scenario unless its outcome class allows it.

| ID | Invariant | Check |
| --- | --- | --- |
| I1 | Agreement | For every settled epoch, sling `settlement_info.final_state` and `computation_hash` equal the Go node's machine hash and commitment, and both equal `EpochSealed` on chain. |
| I2 | Partition | Sling `epochs.input_index_boundary` equals the Go node's epoch bounds and the on-chain bounds. |
| I3 | Safety | While sling is live and inside its time budget, the accepted state equals the honest oracle. |
| I4 | Waste budget | Reverted txs per node per epoch stay under a threshold. Catches retry storms that "eventually succeeds" hides. |
| I5 | Bond conservation | Each bond is recovered exactly once, by its claimer; ETH accounting balances. |
| I6 | No unexpected terminal state | The Go app never goes FAILED or CORRUPTED unless expected; sling does not crash-loop. |
| I7 | Convergence | Within k blocks after a fault clears, both nodes are near finalized, with no nonce gaps on accounts 6 and 7. |

## Scenario format and honest oracle

Each scenario is a declarative file with three parts:

1. **Starting checkpoint.** An Anvil state dump, a tarball of the `sling-state` volume, and a `pg_dump`, all taken with both nodes stopped so they are consistent. This lets a scenario start at, for example, "epoch 1 sealed, dispute-ready" without replaying setup.
2. **Event-triggered steps.** Steps fire on chain events or log markers, for example "on `CommitmentJoined` from account 6 → pause sling". Dave's harness uses the same log-marker approach.
3. **Expected outcome class.** One of `MUST_SETTLE_HONEST`, `MAY_LOSE_CONSISTENTLY` or `MUST_HALT_SAFELY`, which decides which invariants may be violated.

The honest oracle is a golden run with no faults; echo is deterministic, so its hashes are reusable. Agreement between sling and the Go node is a second oracle, but both use the same emulator, so agreement cannot rule out a common-mode emulator bug.

Every run produces an artifact bundle: the block timeline, a per-node tx ledger (sent, landed, reverted with decoded reason), invariant results, all logs, and the seed.

## Scenario catalog

Build P0 first: it targets the two-node interplay that nothing else exercises.

### P0: two-implementation interplay

| ID | Scenario | Induce | Pass |
| --- | --- | --- | --- |
| R1 | Join race | Both nodes' joins in one block, in both orders | One `CommitmentJoined`; loser absorbs `ClockAlreadyInitialized` without retrying; only the claimer attempts bond recovery |
| R2 | Stage race and proof differential | Same-block stage; then pause each node in turn so the other stages alone | Each implementation's `MachineValidityProof` is accepted on its own; loser handles `TournamentResultAlreadyStaged` |
| R3 | Accept race with same-block inputs | Inputs ordered before and after the accept tx, sent by the Go node, then by sling | I2 holds in every ordering |
| R4 | Sentry path | (a) sling live; (b) pause sling after staging; (c) sentry claim before vs after staging | In (b), the Go node accepts at exactly block +1000 without spamming `ClaimStagingPeriodNotOverYet` |
| R5 | Twin defenders | Hold sling until the Go node's join lands, then a sybil joins | Sling defends a commitment it didn't submit and wins; the Go node recovers the bond; I5 holds |
| R5b | Defender absent | As R5, but sling stays down through the timeout | Sybil state is accepted after 1000 blocks; both nodes detect the divergence and stop rather than build on it |
| C4 | Compromised public sentry key | Forge a wrong sentry claim with account 7 before sling sends its own | Sling skips on `hasSentryClaimedInEpoch`; settlement falls back to the 1000-block path |
| T3 | Chain reset under persisted state | Restart the Anvil container; volumes survive | Tournament addresses are deterministic, so the same tournament reappears with a different history; both nodes halt safely |

### P1: chain, RPC and configuration faults

| ID | Scenario | Pass |
| --- | --- | --- |
| T1 | Reorg within finality (depth ≤ 2) dropping a join, advance, stage or accept tx | Dropped txs are resubmitted; nothing observable changes |
| T2 | Reorg beyond finality (depth 3–10), out of spec | Safe halt passes; silent divergence fails |
| R6 | Stale-plan sweep: `docker pause` sling for X blocks at each dispute phase, then resume | No stale `advanceMatch` from in-memory state; output is an honest-loss-vs-X curve, the real safety margin |
| F1 | One node's RPC lags `finalized` by N blocks | Go node's "join window closed" FAILED path fires with the other node live; sling's late defense is measured |
| F2 | Tx lands but the send response is lost, per tx type | No double join or stage, no nonce wedge |
| F4 | Key collision: two slings on the default key, or Go PRT signer set to account 7 | Nonce contention stays bounded; nothing corrupts |
| F6 | ENOSPC or read-only `/state` during commitment build | Sling fails loudly and recovers once space returns |
| C1 | Wrong `MACHINE_PATH` on one node | It refuses to join rather than bond a commitment it can't defend |
| C2 | Late start: Go registers, or sling starts empty, N epochs in | I1 and I2 hold after catch-up from block 25 |

### P2: load

- **S1 Input flood.** Many inputs per block and large payloads; measure each node's lag and check I1 and I2.
- **S2 Seeded cross-node soak.** 100+ epochs drawing randomly from the catalog, faulting both nodes, both RPC paths and the chain. Dave's chaos scenario only kills sling.

## CI and next steps

The published images are arm64-only, so CI needs arm64 runners or rebuilt images. `image/Dockerfile` already handles `TARGETARCH`; the rollups-node snapshot image would need its own amd64 build.

- [ ] Scaffold the harness: clock controller, RPC fault proxy, three-way observer, invariant checker
- [ ] Record the golden run and first checkpoints (fresh, epoch 1 sealed, dispute-ready)
- [ ] Implement P0: R1–R5, R5b, C4, T3
- [ ] Build dave's sybil runner image at `v3.0.0-alpha.5` against this Anvil for R5 and R5b
- [ ] Implement P1, then the S2 soak
