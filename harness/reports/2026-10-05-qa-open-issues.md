# Open qa-slingnode-v0 claims, tested on prt-dev-stack

Oct 5, 2026 · sling `ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5` (dave `79874c0`), reference node `test-contracts-bump`, echo app, Anvil 1.4.3

The six open claims in [Mugen-Builders/qa-slingnode-v0](https://github.com/Mugen-Builders/qa-slingnode-v0/issues) were tested on this stack. Unlike the repo's existing proofs, which use dave's e2e Lua lab and a sling built from source, these runs use the published sling image with the Go reference node running alongside on the same Anvil.

Faults reach only a disposable **probe sling**: the same image, with its own state volume and a key the stack never uses, reading through the repo's `rpc_proxy.py`. The stack's own sling and the Go node read Anvil directly, so the honest side of every run is unaffected. SLN-37 is the exception: it changes the chain, so it runs on the stack's own sling, and the settings were restored afterwards.

## Summary

| Issue | Claim | Result on this stack |
| --- | --- | --- |
| [#39](https://github.com/Mugen-Builders/qa-slingnode-v0/issues/39) | -32602 at startup panics the sling | **Reproduced**: panic at `blockchain_reader/mod.rs:134:14`, exit 101, no database |
| [#38](https://github.com/Mugen-Builders/qa-slingnode-v0/issues/38) | A dropped `InputAdded` makes the sling commit the wrong epoch | **Reproduced**, and taken further: the probe bonded the wrong commitment and lost it to the honest nodes |
| [#37](https://github.com/Mugen-Builders/qa-slingnode-v0/issues/37) | Fixed 15M gas wedges the sling | **Reproduced** for a block gas limit below 15M. The low-balance variant was **not reproduced** at devnet fees; the over-reservation is measured |
| [#8](https://github.com/Mugen-Builders/qa-slingnode-v0/issues/8) | Sling pays mined reverts the Go node avoids; a foreign first claimer takes the bond | **Reproduced, both halves**: sling 11 mined reverts vs Go 0 over 365 node txs; an outsider's bond is owned by the outsider and left unrecovered |
| [#6](https://github.com/Mugen-Builders/qa-slingnode-v0/issues/6) | Bond recovery re-scans the whole tree every tick | **Not reproduced at this scale**: reads per idle tick stayed flat as tournaments went from 12 to 14 |
| [#42](https://github.com/Mugen-Builders/qa-slingnode-v0/issues/42) | More than 2^24 inputs in one epoch crashes the sling | **Source-verified only**; a real run needs 16.7M inputs |

## #39: a generic -32602 at startup panics the sling

A probe sling was started behind the proxy in pass-through mode (control), then with `--error-range=-32602:0`, which errors every `eth_getLogs` (trigger).

| | Control | Trigger |
| --- | --- | --- |
| `eth_getLogs` served | 1 | 1 (errored) |
| Container | running | exited, code 101 |
| `/state/db.sqlite3` | created | not created |
| Log | ingesting | `thread 'main' panicked at cartesi-rollups/node/src/blockchain_reader/mod.rs:134:14` |

This matches SLN-39 exactly, on the published image. One injected error is enough. The node has no restart policy in this stack's compose file, so it stays down.

## #38: a dropped InputAdded makes the sling commit the wrong epoch

A funded probe sling (Anvil account 9) caught up through the proxy. Two inputs were then added, and the proxy dropped the second (`--drop-input-index`, a deterministic mode added to the vendored proxy).

| | Chain | Probe | Stack sling | Go node |
| --- | --- | --- | --- | --- |
| Inputs in epoch 1 | 2 | **1** | 2 | 2 |
| Commitment | — | **`0xa3fba54d…`** | `0x30bbd3b1…` | `0x30bbd3b1…` |
| Errors logged | — | none | — | — |

The probe then **bonded its wrong commitment on chain**: account 9 joined `0xa3fb…` next to the Go node's `0x30bb…`. The honest commitment won the dispute, defended by the stack's sling, and the probe lost its bond.

This adds to SLN-38. On a stack where another honest validator is live, the faulty-provider case costs the misled operator its bond and is caught. If the misled sling were the only active defender, which is the stack's default posture because the Go node does not play matches, a wrong commitment from a faulty provider would go uncontested.

## #37: a fixed 15M gas limit wedges the sling

**Block gas limit 10M** (`anvil_setBlockGasLimit`, restored to 30M afterwards): over one epoch, none of sling's transactions landed. It logged 38 errors, for example:

> failed to submit acceptStagedTournamentResult transaction … error code -32000: intrinsic gas too high -- tx.gas_limit > env.block.gas_limit

With no sentry claim, the epoch took the 1000-block fallback, and the Go node accepted it at +1020. The app settled only because the Go node estimates its gas. Reproduced.

**Balance 0.001 ETH** (restored afterwards): sling's sentry claim was rejected twice ("Insufficient funds for gas * price + value"), then landed priced at `15,000,000 gas × 15 wei`. This Anvil's base fee is about 7 wei, so 0.001 ETH is plenty and the wedge did not form. The over-reservation is still real: that sentry claim used about 79k gas but reserved 15M, about 190×. At a mainnet-like 20 gwei, sling needs about 0.3 ETH available per transaction where about 0.0016 ETH is spent. Not reproduced as a wedge at devnet fees; the claim's mechanism is confirmed.

**Control**: back at 30M and full balance, sling claimed and accepted at +30.

## #8: mined reverts, and a foreign first claimer

**Mined reverts.** Across every harness run on this stack (365 node transactions, G0 through the runs above):

| Node | Mined reverts | Breakdown |
| --- | --- | --- |
| Sling | 11 | 5 × `acceptStagedTournamentResult` → `IncorrectEpochNumber`; 3 × `joinTournament` → `ClockAlreadyInitialized`; 2 × `stageTournamentResult` → `TournamentResultAlreadyStaged`; 1 undecoded |
| Go node | 0 | — |

Each sling revert is a race the Go node won or a step it repeated, paid in gas. The Go node never sent a transaction that reverted.

**Foreign first claimer.** At the seal of epoch 3, both nodes were paused, and an outsider (Anvil account 2, running dave's Lua client with the honest commitment) joined first. When the nodes resumed, neither sent a join, so there were no reverts in this case. They then did all the settlement work: the Go node staged, and sling sent the sentry claim and the accept. The bond belongs to the outsider: `bondRecovery()` returns disposition `recoverable`, claimer `0x3c44…93bc`, payment 0.3335 ETH. Neither node calls `tryRecoveringBond` for a claimer that isn't its own, so more than an hour and a thousand blocks later the bond is still in the tournament.

## #6: bond recovery reads per tick

A read-only probe (Anvil account 8, balance set to 0 so it cannot send) ran behind a counting proxy. Requests were measured over 12 idle ticks at three points as history grew:

| Epoch | Tournaments (root + inner) | Requests per tick | `bondRecovery` per tick | `tournamentStanding` per tick |
| --- | --- | --- | --- | --- |
| 10 | 12 | 13.1 | 2.0 | 0.8 |
| 11 | 13 | 11.6 | 1.2 | 1.0 |
| 12 | 14 | 11.6 | 1.2 | 1.0 |

Reads did not grow with history. The steady 1–2 `bondRecovery` calls per tick fit re-checking unrecovered bonds; SLN-8 left one behind. This history has only two inner tournaments, so a deep-dispute history, the case the claim is about, was not tested. Not reproduced, not ruled out.

## #42: more than 2^24 inputs in one epoch

Checked against dave `79874c0` and the running sling:

- `engine/ruler.rs:88-90`: `assert!(fed_windows <= structure.max_inputs(), "more inputs than the epoch admits")`. The issue cites `:86`.
- `engine/structure.rs:61`: `max_inputs() = 1 << log2_input_span`. The live sling's `sling_config` has `log2_input_span = 24`.
- `DaveConsensus.sol:136` and `:265` seal with `getNumberOfInputs()`, with no cap.
- `client-lua/computation/machine.lua:207-219` caps at `1 << ROLLUP_LOG2_MAX_ADVANCE_STATES_PER_EPOCH`, which is 24 in the emulator.

The claimed mechanism is present in the code. Running it needs 16,777,217 inputs in one epoch, about 28,000 full 30M-gas blocks of `addInput` on Anvil; that was not attempted.

## Reproduce

From `prt-dev-stack/` with the stack running:

```sh
python3 harness/run.py qa_sln39 qa_sln38 qa_sln8 qa_sln37 qa_sln6
```

SLN-8 needs the sybil runner image (see `harness/README.md`). Bundles from these runs: `harness/runs/20261005-213427` (SLN-39) and `harness/runs/20261005-213503` (the rest). They include probe logs, proxy logs and full ledgers.

Two scenario checks were wrong on first run and have been corrected but not rerun. `qa_sln8` waited for a bond recovery that never comes, and `qa_sln37` expected the low-balance variant to block every transaction at devnet fees. The results above are from the chain and logs, not from those checks.
