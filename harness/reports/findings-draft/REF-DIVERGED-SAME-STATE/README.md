# DRAFT: the reference node permanently halts an app when a rival with its own post-epoch state wins

Local draft, not yet claimed in qa-slingnode-v0. Reproduced Oct 5-6, 2026 on prt-dev-stack with the harness in `harness/`.

When the root tournament finishes, the reference node's PRT service compares only the winning **commitment** with its own. If they differ, it marks the application `DIVERGED`, a terminal status: the node stops producing claims for that app and never stages or accepts again. It does this:

- **before anything is accepted**, although DIVERGED is defined in its own code as disagreeing with what the chain *accepted*; and
- **without comparing post-epoch machine states**, so it halts even when the winner proves the same post-epoch state the node computed.

The reference node also sends no dispute moves (acknowledged in its logs), so with no sling running, any rival wins by timeout. Together: one bond and one timeout call take the root bond and leave the app with no node that will settle the epoch, even though settling it would produce the correct state.

## Stack

| Component | Version |
| --- | --- |
| Reference node | `ghcr.io/riseandshaheen/rollups-node:2.0.0-alpha.13`, packaging the official [rollups-node v2.0.0-alpha.13](https://github.com/cartesi/rollups-node/releases/tag/v2.0.0-alpha.13) release (tag `667962aa`) |
| Sling | `ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5` (dave `79874c0`), paused for the trigger |
| Contracts | Dave v3 devnet in `anvil/state.json`: echo app `0x6c2E…6146`, consensus `0x570c…7F68`, claim staging period 1000 blocks, root join allowance 300 blocks |
| Chain | Anvil 1.4.3, chain id 31337, harness as the only miner |
| Adversary | dave `v3.0.0-alpha.5` Lua sybil (`test/e2e/support/runners`), Anvil account 2, `patched_commitment` diverging at the first level-0 leaf (`meta_cycle = 1 << 44`) |

## Code

rollups-node `v2.0.0-alpha.13` (`667962aa`):

- `internal/prt/prt.go:518-521` (`reconcileAcceptedEpochs`, from L490): once the root tournament has `FinishedAtBlock` set, `if WinnerCommitment != *epoch.Commitment` → `setApplicationDiverged("Epoch %d has inconsistent commitment …")`. No machine-hash comparison precedes it, and it runs before any `EpochSealed` for the next epoch is observed.
- `internal/prt/consensus.go:287-296` (`matchConsensusSnapshotToEpoch`, the staging path) checks the commitment and then `WinnerPostEpochMachineStateHash`, but it is never reached once the app is DIVERGED.
- `internal/appstatus/appstatus.go:74-75`: "SetDiverged marks an application DIVERGED (terminal): the node's computed claim disagrees with what the chain accepted."
- `internal/prt/warnings.go:11-17`: "This node release sends no dispute moves; the commitment can lose by timeout", logged once per tournament when a match becomes active (`prt.go:337`), after the node's join and bond.
- `internal/prt/` has no call to `advanceMatch`, `winMatchByTimeout`, `sealInnerMatch…`, `sealLeafMatch` or `eliminateMatchByTimeout`.

## Reproduction

```sh
python3 harness/run.py r5_twin_defenders qa_ref_no_defense
```

1. **Control** (`r5_twin_defenders`): sling live. The reference node joins the honest commitment first; the sybil joins a rival; sling defends; the honest commitment wins; the reference node recovers its bond.
2. **Trigger** (`qa_ref_no_defense`): sling paused at the seal, so the reference node is the only validator. Same sybil. Then mine 1,200 blocks, past the staging period.
3. **Observe**: tournament winner and match deletion, every reference-node transaction, `bondRecovery()`, the reference node's app status, and whether the epoch is ever staged.

The trigger halts the app. Rebuild the stack afterwards (`docker compose down -v` in `sling/` and `reference/`, restart Anvil, quickstart from step 3).

## Result

Evidence: `harness/runs/20261005-234620/` (`R5/` control, `REF-NODEF/` trigger).

| | Control (sling live) | Trigger (reference node alone) |
| --- | --- | --- |
| Reference node's root commitment | honest | honest `0x2394a8fc…`, final state `0xc8217d7f…` |
| Rival commitment | `0x5da094bf…` | `0x641dbfd0…`, final state **`0xc8217d7f…`** (the same) |
| Reference node's txs during the dispute | `joinTournament` | `joinTournament` only |
| Sybil's txs | full bisection, inner and leaf matches | `joinTournament`, `winMatchByTimeout` |
| Match outcome | honest wins (`child tournament`) | `MatchDeleted` reason 1 (timeout), rival wins |
| Root bond | recovered by the reference node | owned by the sybil: `bondRecovery()` = recoverable, `0x3c44…93bc`, 0.36685 ETH |
| Reference node app status | `OK` | **`DIVERGED`** 30 blocks after the finish (block 2412) |
| Epoch staged 1,200 blocks later | yes, accepted | **no**; no node sent anything |
| `canStageTournamentResult()` | — | finished, not failed, not staged, winner state `0xc8217d7f…` |

The reference node's own `cartesi_getEpoch(3)` reports `machine_hash 0xc8217d7f…`, the winner's proven final state. Staging the rival's result was valid (`canStageTournamentResult` says so) and would have settled exactly the state the reference node computed. Dave's Lua oracle, replaying every input from the template, computed the same honest commitment as the reference node.

## Mainnet fidelity

Fidelity F0 (local devnet).

| Parameter | Mainnet | Used | Outcome with the mainnet value |
| --- | --- | --- | --- |
| Root join allowance | Days of blocks (set at deployment) | 300 blocks | Unchanged. With no defender, the honest commitment times out whatever the allowance; a longer one only delays the timeout. |
| Claim staging period | Deployment-set, typically days | 1000 blocks | Unchanged. DIVERGED fires at the tournament finish, before staging. |
| Block time and finality | 12 s, real finality | Harness-mined, finalized = head − 2 | Unchanged. The trigger compares two values once the finish is finalized. |
| Validators running | Operator choice | Reference node only (sling paused) | The precondition. With a live sling defending, the honest commitment wins (control). |
| Rival's final state | Attacker's choice | Equal to honest | With a wrong final state, DIVERGED is the right response. The finding is the equal-state case. |
| Epoch contents | Any | 0 inputs | Unchanged. The comparison does not depend on inputs; the sybil diverges mid-computation and ends on the honest state. On this fresh chain the shared final state equals the echo template hash, so re-run with inputs in the epoch before filing. |

## Threat model

- **Dave** (`docs/dispute-game.md`, "Security statement and assumptions", L19-64, `79874c0`): safety requires "at least one participant joins the correct commitment" and "can submit required transactions before its clocks … expire"; "a timeout can make an incorrect commitment survive if the correct participant does not act." The timeout win itself is protocol behaviour when no defender acts. **This part is `needs-assumption-failure`** and is not the finding.
- **Reference node**, its own definition (`appstatus.go:74-75`): DIVERGED means "the node's computed claim disagrees with what the chain accepted." Here nothing has been accepted, and the claim the chain would accept (the winner's post-epoch state) equals the node's. **This part is `contradicts-docs`** against the component's own code documentation.
- The reference node's inability to defend is acknowledged only in a runtime warning, logged after it has joined and bonded. Its `CHANGELOG.md` and `docs/` do not mention it (`not-covered` as documentation).

## Assessment

**What happens.** A reference-node-only PRT deployment, or any deployment whose sling is offline for one root allowance, loses its root bond to anyone willing to post one bond and call `winMatchByTimeout`. That much is the documented protocol risk of having no active defender. The added harm from this code is that the reference node then halts the app for good (`DIVERGED` is terminal) even when the rival claimed the correct post-epoch state, and nothing in the stack settles the epoch. An attacker gains the bond and freezes the app at no further cost, without needing a wrong final state.

**Who and cost.** Permissionless: one root bond (0.3335 ETH here), recovered with profit. The attacker needs the honest commitment's final state, which anyone can compute by running the machine.

**Recovery.** The app can be unstuck by a manual permissionless `stageTournamentResult` and, after the staging period, `acceptStagedTournamentResult`. The reference node stays DIVERGED until an operator intervenes, by re-registering or resetting its state. No redeploy is needed.

**Severity: medium (proposed).** Liveness is lost but recoverable without a redeploy; no wrong state is finalized. It is reachable only when no active defender is running, which is the stated precondition, but that is the default posture for an operator who runs the reference node alone in PRT mode.

**Likelihood: plausible.** It needs no sling for one allowance, a condition the reference node's own warning anticipates.

**Confidence: high** for the mechanism (code path plus reproduction with a control). Medium for the severity framing: maintainers may answer that DIVERGED on any commitment mismatch is intended as a conservative stop.

**Suggested fix.** Before setting DIVERGED at tournament finish, compare `WinnerPostEpochMachineStateHash` (and the outputs root) with the local claim, as `matchConsensusSnapshotToEpoch` already does on the staging path. If they match, stage or accept normally and only log the commitment mismatch. Separately, document in the reference node's docs that PRT mode joins but does not defend, so operators know a sling (or another defender) is required.
