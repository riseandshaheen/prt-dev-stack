# Funds-safety test plan: Dave PRT stack at v3.0.0-alpha.6

Oct 22, 2026 · @Shaheen · stack: dave `v3.0.0-alpha.6` (PRT contracts, `DaveConsensus`, `cartesi-sling-node`), rollups-contracts `v3.0.0-alpha.10`, Cartesi Machine v0.22.0.

## What this plan is

Ten test areas, each with the cases that decide whether user or validator funds can be lost, frozen or taken, ordered by how directly a failure moves money. Every case names the asset at stake, the invariant, how to run it, and the source line that makes the invariant true (paths relative to the dave tree at the tag; `RC/` is the rollups-contracts alpha.10 dependency under `cartesi-rollups/contracts/dependencies/`, `T` is `prt/contracts/src/tournament/Tournament.sol`, `DC` is `cartesi-rollups/contracts/src/DaveConsensus.sol`, `APP` is `RC/dapp/Application.sol`).

Priority: **P0** a failure ends in loss of funds reachable by an outsider (QA `critical`); **P1** a wrong result finalizes or the app freezes for good (`high`); **P2** funds stuck until someone acts, or a cost the honest side pays (`medium`). Severity vocabulary follows `qa-rollups-stack-v1/docs/severity.md`; relation to the documented model follows `docs/threat-models/`.

Where a case runs: **forge** against the real contracts (fast, deterministic, the only place to drive a full canonical two-level dispute without days of mining); **stack**, the local prt-dev-stack Anvil with sling containers and the Lua sybil; **Sepolia** when the trigger is a chain property. The stack is the right place for anything about the node's behaviour under time, RPC faults, restarts and races; forge is the right place for contract invariants.

Already covered elsewhere and not repeated here: the 31 honest-node obligations have forge pins in `test/properties/HonestNodeObligations.t.sol`; dave `test/e2e/rollups` covers kill-and-resume at every protocol point on the three-level devnet table; Nonso's cycle-2 PRs cover SLING-1..7 and PRT-1..2 (cross-checked locally 2026-10-22, see `harness/reports/github-drafts/`). This plan cites them where a case extends one.

## Funds map

Money sits in three places. The **Application** holds every deposit (Ether via `receive`, tokens via portals or direct transfer) and releases it only through `executeOutput` (a voucher proven against an *accepted* outputs root) or, after foreclosure, `issueRefund` and `withdraw`. The **tournaments** hold join bonds (root 0.36115 ETH, leaf 0.51265 ETH on the canonical table) and pay out through per-action refunds and `tryRecoveringBond`. The **validator's signer** holds the operating float (funding floor about 3.1 ETH) that reverts, bonds and gas spikes draw on. The single gate between a validator's claim and a user withdrawal is `acceptStagedTournamentResult` (DC:241-281): it is the only write to the accepted-roots set, and sentries can only shorten the wait to it, never block it.

---

## Area A · The settlement gate (P0)

Asset: everything in the Application. Invariant: no outputs root becomes executable except through `acceptStagedTournamentResult` of the epoch's `ROOT_WINNER`, after the staging period or unanimous sentries.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| A1 | Execute a voucher whose root is **staged but not accepted** | forge | `InvalidOutputsMerkleRoot`; `_outputsMerkleRoots` written only at DC:266 | APP:337-349, DC:183-191 |
| A2 | Execute a voucher after **all sentries claimed** the hash but nobody called accept | forge | revert; claims never write the set | DC:193-216 |
| A3 | Stage a `ROOT_FAILED` root (no live commitment at closure) | forge, stack | `TournamentFailedNoWinner`; epoch frozen; only the guardian can act | DC:174-176, 437-450 |
| A4 | Stage with a `MachineValidityProof` for a **different** machine hash, or a leaf proof at the wrong address, or `iflags_Y == 0`, or a non-`RX_ACCEPTED` yield | forge | `InvalidMachineMerkleProof` / validity revert; outputs root cannot be detached from the winner's state | DC:456-462, `RC/library/LibMachineValidityProof.sol`:24-43 |
| A5 | Accept for a **non-current** epoch number, or accept twice | forge | `epochNumber == _epochNumber` check; second accept reverts (unstaged) | DC:243-249 |
| A6 | Accept **before** the staging period with N > 0 sentries and N-1 claims | forge | `ClaimStagingPeriodNotOverYet` | DC:250-256, 464-466 |
| A7 | `provideMerkleRootOfInput` with index ≥ upper bound, and with bytes whose hash is not the InputBox's | forge | zero root (fixpoint) / `InputHashMismatch`; tournament stays bound to `[lower, upper)` | DC:370-390 |
| A8 | Epoch k+1 input added **in the acceptance block** of epoch k, before and after the accept tx in block order | stack | `_inputIndexUpperBound` sampled at accept; the input lands in exactly one epoch on chain, in sling's `epochs` table and in the oracle | DC:263-265 |
| A9 | Accepted-roots set is **monotone**: an output proven against epoch 1's root executes after epochs 2..n settle | forge | succeeds (by design; proofs carry no epoch) | APP:328-351, `RC/common/OutputValidityProof.sol` |
| A10 | Inputs added **before** `DaveConsensus` deployment | stack | belong to epoch 0 and are computed from the template; the root tournament's final state covers them | DC:135-140 |

A3 and A8 are the stack cases that matter most: A3 is the path where zero honest nodes turn a timeout win into a stageable wrong state, and the only brake is Area F.

## Area B · Output execution and replay (P0)

Asset: Application balance and token holdings. Invariant: each output executes at most once, exactly as encoded, and only vouchers move value.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| B1 | Execute the same voucher twice; execute from a destination that **re-enters** `executeOutput` with the same index during the call | forge | `OutputNotReexecutable`; bit set before the external call (CEI) | APP:171-179 |
| B2 | Re-enter with a **different** valid output from the destination | forge | succeeds (by design); confirm total value moved equals the sum of the two vouchers, no double-spend of the first | APP:177-184 |
| B3 | Voucher with `value > address(this).balance` | forge | `InsufficientFunds`, bit rolled back, retryable | `RC/library/LibAddress.sol`:24-26 |
| B4 | Voucher to an ERC-20 whose `transfer` **returns false** (no revert) | forge | call "succeeds", output marked executed, tokens not moved; **funds-safety gap to document**: `safeCall` ignores return data; only `DelegateCallVoucher` + `SafeErc20Transfer` checks it | APP:673-681, LibAddress.sol:20-40 |
| B5 | Voucher whose destination reverts; then retry after the destination is fixed | forge | first attempt reverts with bubbled data and clears the bit; retry succeeds once | APP:653-669 |
| B6 | `DelegateCallVoucher` that writes `_isForeclosed`, `_outputsMerkleRootValidator` or the owner slot | forge | succeeds: the machine can rewrite the Application's storage. Record as the trust boundary (the consensus is trusted for every slot); test that a **non-machine** path cannot produce such a voucher (A1, A4) | APP:685-692, LibAddress.sol:46-59 |
| B7 | Notice or unknown selector passed to `executeOutput` | forge | `OutputNotExecutable`, no state change | APP:660, 667 |
| B8 | Siblings length ≠ 63, or `outputIndex` with bits above height | forge | revert in `LibOutputValidityProof` / `LibBinaryMerkleTree` | `RC/library/LibOutputValidityProof.sol`:15-30 |
| B9 | **Foreclosed app**: execute a voucher from an already accepted epoch | forge, stack | succeeds. `executeOutput` has no foreclosure check, so accepted vouchers and `withdraw`/`issueRefund` draw on the same pool. Measure whether a user can be paid twice for one balance (voucher from epoch k plus withdrawal from the last finalized state k) | APP:163-184 vs 189, 220, 260 |

B9 is the most important new case in this area: it is reachable by an outsider once a guardian forecloses, and nothing in the docs says which of the two exits wins.

## Area C · Dispute safety on the canonical table (P0)

Asset: the correctness of every accepted state, so everything in the Application. Invariant: with one live honest node acting within `G`, `T+G` and the shared `G` for proof plus fallback, and censorship ≤ `C`, the honest commitment is never eliminated and the root finishes with its state. Known gap: the canonical two-level table (`[38,0]/[54,38]`, T = 120 min) has **never run node-against-chain through a height-38 leaf seal, proof and settlement** (`docs/todo.md`:21-27). On the stack this needs the devnet rebuilt with `DEVNET_GEOMETRY=canonical` (the shipped dump already is) and hours of wall clock; the forge suites in `prt/contracts/test/properties` are the fast path for the contract half.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| C1 | One Lua sybil (`patched_commitment`) against sling on the canonical table, through root bisection, inner seal, child join, **height-38 leaf seal and `winLeafMatch`** at a maximum-size input | stack (long) | sling wins, stages, accepts; oracle equals settled state; one leaf proof refunded within `WIN_LEAF_MATCH` | T:582-660, `libs/Gas.sol`:17-35 |
| C2 | Sybils in **both** final-state classes of a child, one copying the honest class with a different root | stack, forge | honest side's class wins through a same-class distinct root if needed (obligation 20); parent propagates | T:758-771, 1161-1171 |
| C3 | **CF-01 handover**: sybil joins the leaf child one block before closure with a one-block clock and seals immediately | forge | proof reverts once the opponent expired; the node's fallback timeout claim lands; both within one shared `G` (27 blocks at G = 25) | T:594-600, `MatchClocks.sol`:146-155 |
| C4 | **Identical-copy front-run** of every honest action (join, advance, seal, proof, claim) by an adversary that orders the block | stack (automine off) | protocol state unchanged; honest copies revert; measure the honest node's paid gas per epoch (L-06 says up to 3.76M per leaf proof). Extends SLING-7 to an adversary | T:125-129, `docs/node-architecture.md`:664-670 |
| C5 | Honest root join at exactly `G - 1` blocks after root start, then the worst in-bound child join at `T + G - 1` | forge | honest clock survives with the in-flight reserve (F-01 regression) | `arbitration-config/ClockBudgets.sol`:63-78, `libs/Clock.sol`:124-161 |
| C6 | Honest node **offline for > G** at a response, then back | stack | lateness beyond `G` drawn from `C`; on devnet `C = 0`, so the honest side loses: confirm the loss is logged as the ERROR the docs promise and that the node exits 30 at the next epoch after a divergent state settles | `docs/epoch-lifecycle.md`:249-253 |
| C7 | Zero honest nodes: sybil wins the root by timeout, stages | stack | stage succeeds (`ROOT_WINNER`); accept blocked only by the staging period; a sentry node (account 7) running late logs its dissenting claim | DC:162-191, CL "sentry also claims its own computed state" |
| C8 | Propagation at `resultAt + pausedAllowance` exactly, and one block before | forge | revert at the instant (inclusive), success one block before; `eliminateInnerTournament` deletes both parents afterwards (obligation 19) | T:749-752, 1097-1103, 829-834 |
| C9 | `eliminateMatchByTimeout` and `eliminateInnerTournament` by a third party on sybil-vs-sybil matches so the root can finish | stack | root finishes; honest result stageable; third party refunded from the pool | T:369-397, 1059-1065 |
| C10 | Input backlog larger than the root window at seal (L-01): N inputs of maximal compute placed before the predictable acceptance block | stack (scaled) | honest root join misses closure; price it on the stack at gap 1 and gap 64; the only backstop is F1 | `docs/dimensioning.md`:174-188 |
| C11 | A **reorg** of depth 3 past Anvil's finalized across a join or seal | stack | joins and settlement calldata come from finalized state only; the Latest "Foam" revalidates; no stale mutation lands | `docs/node-architecture.md`:545-588 |

## Area D · Bond and refund accounting (P2, P0 for D3)

Asset: validator bonds and the operating float. Invariant: every bond is recovered exactly once by its recorded claimer, refunds never exceed their caps, and the pool pays nothing on a reverted action.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| D1 | `tryRecoveringBond` while running, with no winner, after recovery | forge | `TournamentNotFinished`, `NoWinner`, then `true` as a no-op | T:416-454 |
| D2 | Winner payout arithmetic: balance ≤ bond; balance > bond (`bond + (balance-bond)/10`, rest burned to `address(0)`) | forge | exact amounts; `assert(success)` on the burn | T:435-449 |
| D3 | **Claimer slot capture**: adversary copies the honest root join first (same commitment root), stays silent | stack | honest join reverts `ClockAlreadyInitialized`, honest node defends anyway (obligation 2), adversary receives the winner payment, honest node's own bond was never posted (it reverted) but it paid the gas; check the node still completes the epoch (payment owed is to the adversary) | T:214-243, `docs/dispute-game.md`:830-832 |
| D4 | Claimer that **refuses ETH** (contract without `receive`, or EIP-7702 delegated code that reverts) | forge, stack | refund silently skipped; `tryRecoveringBond` returns `false` forever, pool locked; the node holds out of every later epoch (completion requires no payment owed) | T:1316-1330, `docs/node-architecture.md`:592-596 |
| D5 | **Signer change** mid-epoch and after an epoch | stack | node recovers only the current signer's bonds; anyone can recover the old signer's with `tryRecoveringBond`, paid to the recorded claimer | `docs/epoch-lifecycle.md`:150-167 |
| D6 | Refund cap: an action at `tx.gasprice` far above `basefee + 10 gwei`, and at an allocation above its cap | forge | refund = `min(balance, allocation × 50 gwei, units × min(gasprice, basefee+10 gwei))` | T:1292-1312 |
| D7 | Refund farming: `winLeafMatch` with padded proof bytes; batched actions sharing one tx | forge | padding rejected by the state transition (`InputBytesWithoutInput`); the TX constant is paid per action (documented slack, RA:169-182) | T:582, `docs/prt-refund-accounting.md`:169-182 |
| D8 | **No-winner** child or root: where does the balance go | forge | stays locked forever (documented policy gap); quantify per eliminated pair | `docs/prt-refund-accounting.md`:108-109, 197-198 |
| D9 | ETH conservation across a whole dispute: sum of bonds in = refunds + winner payment + burn + locked | stack, forge | balances on all tournament clones reconcile to zero or to the documented locked remainder (harness invariant I5) | T whole |
| D10 | Signer below the funding floor when a leaf batch is due | stack | per-tick ERROR; the action is not sent; dispute clock keeps running (quantify blocks lost per ETH short) | node README "Fund the signer" |

## Area E · Clocks, finality and provider trust (P1)

Asset: the honest node's ability to meet deadlines, so the accepted state. Invariant: the node acts within `G` of each due action and its reads of the chain are complete and current.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| E1 | **Frozen `finalized`** while `latest` advances (SLING-5) during an active match | stack + fault proxy | node misses a response that was due; measure blocks lost; today no WARN/ERROR until the deadline consequence | `docs/node-architecture.md`:451-453 |
| E2 | `eth_getLogs` response **omitting one tournament event** (match created / sealed) | stack + proxy | Hero waits instead of acting; refold when the fold fails; measure delay against `G` | `docs/node-architecture.md`:528-543 |
| E3 | Omitted `InputAdded` at a catch-up chunk tail | stack + proxy | ingestion stalls, ERROR after 5 min, no wrong commitment ever built | `docs/node-architecture.md`:464-481, 504-509 |
| E4 | Finality stall: `finalized` lags `latest` by more than `T` | stack | commitment build cannot start; lateness drawn from `C`; on devnet the honest side loses, logged | `docs/dimensioning.md`:362-367 |
| E5 | Relay / submit endpoint **holds a nonce** for R seconds (doomed tx) | stack + proxy that delays `eth_sendRawTransaction` | next action lands `R + poll + inclusion - G` late; node does not supersede the pending tx (todo); quantify | `docs/runbooks/submit-relay.md`:16-38 |
| E6 | Submit endpoint on **another chain id**, read endpoint correct | stack | exit 30 at start; a swap mid-run is caught only at the next start | CL "chain id" |
| E7 | Read endpoint behind a **10,000-block `eth_getLogs` cap** during a cold start on a long history | stack + proxy | node splits ranges; time to reach the live epoch stays under the root allowance (otherwise seed from the settled state) | `docs/node-architecture.md`:217-221 |
| E8 | `--polling-interval 30` (default) against G = 25 blocks on a 12 s chain, and `--polling-interval 0` (SLING-4) | reasoning + stack | default costs up to 2.5 of 5 minutes per action; document the recommended interval for a dispute | `docs/node-architecture.md`:200-217 |

## Area F · Guardian, foreclosure and the emergency exit (P0)

Asset: every deposit of an app whose consensus went wrong. Invariant: foreclosure during the staging period stops a wrong state from being accepted, and afterwards each depositor can recover exactly their recorded deposit or last finalized balance, once.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| F1 | **Foreclose during the staging period** of a sybil's wrong result (C7) | stack | `acceptStagedTournamentResult` reverts `ApplicationForeclosed`; the wrong root never enters the set; `wasInputFinalized` stays false for the epoch's inputs | DC:241, APP:295-298 |
| F2 | Foreclose **one block after** acceptance | stack | too late: wrong root accepted and its vouchers executable (B9). This is the guardian's whole window; measure it in blocks for staging period 1000 and for the release fixture | DC:250-256 |
| F3 | `claimStagingPeriod = 0` with no sentries (PRT-2) | forge | stage and accept in one block; guardian window 0; already reproduced, keep as regression | DC:124, 251-255 |
| F4 | `issueRefund` for each portal kind (Ether, ERC-20, ERC-721, ERC-1155 single and batch) for inputs of the frozen epoch | forge, stack | exact recorded amounts returned from the payload, not from current balances; `_refunded` bit per input | APP:186-215, `RC/refund/RefundOutputBuilder.sol`:63-81 |
| F5 | `issueRefund` for an input the validator says is **finalized** (index < lower bound), and for an input whose sender is not a canonical portal | forge | revert (`wasInputFinalized`) / `UnknownInputSender` | APP:200-202, DC:412-420 |
| F6 | Refund to a **contract depositor that rejects Ether** | forge | reverts, bit not set, stays refundable; no other depositor blocked | APP:193-208 |
| F7 | `proveAccountsDriveMerkleRoot` against the validator's last finalized hash; twice; with a wrong drive position | forge | one-shot; `AccountsDriveMerkleRootAlreadyProved`; wrong position fails the machine-root recomputation | APP:217-255 |
| F8 | `withdraw` per account through `UsdWithdrawalOutputBuilder`: correct 32-byte account, 31-byte account, same index twice, an account holding a non-USD asset | forge | `SafeErc20Transfer` of the uint96 balance; `InvalidAccountSize`; replay bit; non-USD unreachable (document) | APP:257-281, `RC/withdrawal/UsdWithdrawalOutputBuilder.sol`:14-54 |
| F9 | **Zero `WithdrawalConfig`** (guardian 0, builder 0) as the echo app uses | forge | `isValid` passes; `foreclose` uncallable; `withdraw` reverts on decode. Means "no emergency exit at all": a release note, not a bug, but every production app must be checked for it | `RC/library/LibWithdrawalConfig.sol`:10-34 |
| F10 | **Double exit** after foreclosure: user with balance in the last finalized state also holds an executable voucher from an accepted epoch (B9) | forge | determine whether `withdraw` + `executeOutput` can both pay for the same balance; this decides B9's severity | APP:163-184, 257-281 |
| F11 | Foreclosure stops deposits: portal deposit after `foreclose` | forge, stack | whole tx reverts (`ApplicationForeclosed` via InputBox); assets never stranded at the app | `RC/inputs/InputBox.sol`:23 |
| F12 | Sentry manager `0x0` (echo app) and a compromised sentry key | forge | no rotation possible; a sentry can only shorten acceptance, never block or corrupt it | DC:283-296, 464-466 |
| F13 | Terminal application (halt or exception on an input): node behaviour and the exit | stack | epoch never stageable; node defends then holds with ERROR; guardian forecloses; F4/F7 recover funds | `docs/epoch-lifecycle.md`:70-78 |

## Area G · Deposits and input integrity (P2)

Asset: deposits that never get recorded or that distort the epoch. Invariant: every recorded deposit matches the asset movement, and inputs cannot be forged, oversized or placed outside their epoch.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| G1 | Ether sent to the app via `receive`, ERC-721/1155 via holder hooks, plain ERC-20 `transfer` | forge | held, no input, no refund path (document; the machine cannot see them) | APP:36-37, 160 |
| G2 | ERC-20 with **fee-on-transfer** or rebasing semantics through `Erc20Portal` | forge | `Erc20TransferValueIsNotBalanceDelta` revert; no deposit recorded for a wrong amount | `RC/portals/Erc20Portal.sol`:31-41 |
| G3 | Portal pointed at an app whose `getInputBox` reverts or returns a non-contract | forge | `ApplicationReverted` / `InputBoxNotDeployed`; asset transfer rolled back | `RC/portals/Portal.sol`:46-92 |
| G4 | Input at exactly 64 KiB encoded and one byte over | forge | accepted / `InputTooLarge` | `RC/inputs/InputBox.sol`:44-48 |
| G5 | Input spam: 10k minimal inputs in one epoch at the default snapshot gap | stack | node keeps up; epoch seals at the acceptance block with the right upper bound (A8); feeds C10's pricing | DC:263-265 |
| G6 | Input whose payload makes the guest **declare a yield above the TX buffer** (SLING-6) or **halt** | stack | node stalls / app terminal; on-chain step accepts the same yield (SEAM class): confirm the settled state through a dispute, not by agreement | `machine/step` `SendCmioResponse.sol`:42-56 |
| G7 | Inputs from the same block as the deployment and from the deployment block itself | stack | counted in epoch 0 exactly once | DC:135-140 |

## Area H · Node state and signer safety (P1)

Asset: the honest node's liveness as the only defender. Invariant: a restart, a seed, a second process or a key mistake never silently turns the honest node into zero honest nodes.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| H1 | **Two nodes, one signer** (nonce sharing) during a dispute | stack | nonces collide, one node's actions drop; measure blocks lost; `node.lock` does not cover this (documented) | `docs/node-architecture.md`:712-720 |
| H2 | Seed a data dir from a **tampered snapshot** whose stored hash tree matches but whose data differs | stack | accepted at start (hash tree not verifiable, documented); the node later computes a wrong state and loses honestly. Record as `known-debt` with the cost | node README "Operator notes", `docs/todo.md` |
| H3 | Seed from the settled state of epoch k while bonds of epoch k-1 are unrecovered | stack | node never revisits k-1; bonds recoverable by anyone (D5) | `docs/epoch-lifecycle.md`:150-153 |
| H4 | SIGKILL during leaf commitment build, during `winLeafMatch` send, during `tryRecoveringBond` batch; restart | stack | resumes; no duplicate bond, no lost action beyond one poll + inclusion; extends SLING-1 to mid-dispute | `docs/node-architecture.md`:592-608 |
| H5 | Disk below 1 GiB during a dispute | stack | batch refused with ERROR; dispute actions still sent? (determine: the refusal covers batches and epoch rolls, not Hero actions) | CL "1 GiB" |
| H6 | Exit-code contract under the systemd policy for every startup failure (SLING-2 regression) plus a lost dispute (exit 30 at next epoch) | stack | 30 for terminal, 40 for rebuild, 1 for retryable; a restart loop never masks a terminal condition | `fatal.rs` |
| H7 | Key material never logged: malformed URL (SLING-3), KMS errors, panics | stack | token absent from every log path | CL "redact" |

## Area I · Agreement between implementations (P1)

Asset: the correctness of the accepted state when node and on-chain step disagree. Invariant: the on-chain `CartesiStateTransition` is the single authority; a divergence must be settled by a proof, and agreement between two off-chain implementations is not evidence.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| I1 | Replay of the three cycle-1 SEAM divergences on v0.22 (accept length ≠ 32, declared length > buffer, `mcycle == imcyclemax`) | stack + forge step proof | step and node agree, or the disagreement is proven through Solidity and the honest node's commitment matches the step | `docs/computation-hash.md` "Where implementations must agree" |
| I2 | Sling vs Lua oracle commitment at the canonical root stride (2^38) on a 64 KiB input | stack | equal roots; any difference is a P0 lead | `test/e2e/rollups/test_env.lua` oracle |
| I3 | Template hash determinism: rebuild the echo and a token app with the v0.22 toolchain on two hosts | stack | identical `cartesi-machine-stored-hash`; otherwise the deployed template cannot be reproduced for a dispute | QA backlog "Template-hash rebuild determinism" |
| I4 | 2^24 inputs per epoch coordinate cap | reasoning | priced flood (~5e11 gas) accepted as out of model; keep the number current for the two-level table | `docs/node-architecture.md`:182-188 |

## Area J · Deployment and configuration (P1)

Asset: the whole app, before any user deposits. Invariant: a misconfigured or wrong-chain deployment fails loudly rather than running with short clocks or no exit.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| J1 | Devnet or Sepolia parameter provider replayed at its CREATE2 address on another chain (L-08), consensus wired to it | forge (fork) | runs with `C = 0` or 8 h silently; the node does not compare budgets with the chain's registration. Candidate node check | `prt/contracts/script/Deployment.s.sol`:175-179 |
| J2 | `newDaveApp` with `claimStagingPeriod` 0, sentries empty, zero `WithdrawalConfig` (the release fixtures and the echo app) | forge | all accepted; print the resulting guardian window and exit availability as a deployment checklist | `cartesi-rollups/contracts/src/DaveAppFactory.sol`:34-48 |
| J3 | Address squatting of the predicted app address for a salt (CTR-1) | forge | deploy reverts for that salt; no funds at risk but a griefing vector | `RC/dapp/ApplicationFactory.sol`:54-78 |
| J4 | Deploy script on a Glamsterdam chain (PRT-1) | Sepolia | Foundry 1.5.1 simulates under Prague and sends a 4.2M gas limit that runs out under EIP-8037 state pricing. EIP-8037 lifts the 2^24 cap off state gas (total cap 2^32-1), so a 22M-gas deploy is admitted: fix is a live-chain gas estimate in the deploy path, not a contract change. Regression until the script does that | EIP-8037 "Transaction gas limit", PR 9 |
| J5 | Node started against a factory whose level table is **not** the canonical one (three-level devnet) with a canonical-table template | stack | refused at startup or joins the right geometry (node compiles no geometry in); a claimer pinned to the wrong root stride cannot join | CL "two levels" |
| J6 | Slot-time change (6 s blocks) on a chain registered at 12 s | reasoning + Anvil `--block-time` | every budget halves with no signal; document as a deployment-generation trigger | `docs/dispute-game.md`:590-594 |

---

## Order of work

1. **Forge first, this week:** A1-A7, A9, B1-B9, D1-D2, D6-D8, F3-F12, G1-G4, J2-J3. Real contracts, minutes each, and they settle the two open questions that decide severities elsewhere: whether B9/F10 allow a double exit, and what F9's zero config means for the apps we actually deploy.
2. **Stack, short:** A8, A10, C7 + F1 + F2 (the guardian window end to end), D3, D5, D10, E1, E6, G5, H1, H4-H7. Each fits in the existing repro pattern (no-sentry apps on the shared dump, one node per container, fault proxy on the host).
3. **Stack, long:** C1 (the untested canonical leaf path), C2, C4, C9, C11, E2-E5, F13, I1-I2. These need the Lua sybil image rebuilt for alpha.6 and the harness ported (the open follow-up from the stack bump).
4. **Chain and reasoning:** C10 pricing, I4, J1, J4, J6.

## Gaps this plan cannot close

- The honest-survival argument is a two-level search reviewed only by its author (`docs/todo.md`:68-71). Tests confirm instances, not the theorem.
- Hash-tree verification of a seed image is not available in v0.22 (H2): a trusted image is an operating assumption.
- No submit relay has passed qualification (E5): front-running costs (C4) are an operating cost until one does.
- Mainnet parameters (`C = 1 week`) cannot be run; every clock case is run at devnet `C = 0` and extrapolated per `docs/mainnet-fidelity.md`.

---

## Running all of it on the local stack

Everything in this plan runs locally except one case. Forge cases run against the real contracts on the host. Stack cases run on the prt-dev-stack Anvil (1.5.1, alpha.6 devnet dump) with sling containers, the Lua sybil, the fault proxy from Nonso's SLING-5 PR, and `cast`. Nothing needs a testnet key or testnet funds.

| Need | How it runs locally |
| --- | --- |
| Transaction ordering and front-running (C4, D3, SLING-7 class) | `anvil_setAutomine false`, let the pool fill, mine one block; order by gas price or by send order |
| RPC faults (E1-E7) | the stdlib fault proxy between a node container and Anvil: frozen `finalized`, dropped logs, delayed `eth_sendRawTransaction`, range caps, wrong chain id |
| Reorgs (C11) | Anvil's `anvil_reorg`, or `evm_snapshot` / `evm_revert` and re-mine a different history past the depth-2 finalized tag |
| Long clocks (700-block root allowance, 1000-block staging) | `anvil_mine N` is seconds for any N; the harness mines at a controlled pace only while a node has a deadline running |
| **Mainnet budgets** (C = 1 week, G = 5 min, T = 120 min) | deploy a second parameter set on the local chain from the release artifacts: `CanonicalTournamentParametersProvider(12000, 604800, 300)`, a `MultiLevelTournamentFactory` on it, and a `DaveAppFactory` wired to it; apps created there get the 51,100-block root allowance. Clocks are block counts, so mining 50,400 blocks takes seconds. The caveat is wall clock: the node's commitment build (T) is real time, so a dispute must be mined at a pace that leaves the honest node its real budget; burst-mine only through quiet windows |
| Testnet budgets (C = 8 h) and the wrong-chain replay (J1) | deploy the Sepolia provider bytecode with the same CREATE2 salt; it lands at the published Sepolia address on the local chain, which is exactly the L-08 hazard |
| Slot-time change (J6) | `anvil --block-time 6` against a provider registered at 12 s |
| Input-backlog pricing (C10, G5) | the tester app's `burnCycles` makes each input cost a chosen number of cycles |
| Compute-heavy and malformed guests (G6, I1, SLING-6 class) | custom templates stored with `cartesi/machine-emulator:0.22.0` |
| Mainnet gas and fee behaviour (D6, D7 caps) | Anvil's base fee and `--gas-price`; the `basefee + 10 gwei` cap is a contract constant, exercised in forge |

**The one exception: J4**, the Glamsterdam deploy-gas cap (PRT-1). Its trigger is EIP-8037 state-creation pricing, which Anvil 1.5.1 does not implement, so a local chain prices the deploys at 3.1M and deploys them fine. Nonso measured it on public Sepolia with read-only, keyless `eth_estimateGas` calls; that costs nothing and needs no account, and the local cross-check already repeated it. To run it offline we would need an execution client with the Glamsterdam fork enabled (a local reth or geth dev chain, or an Anvil release that adds the fork). Until then J4 stays a read-only public-RPC check, not a testnet deployment.

What local runs cannot give is confidence about mainnet *time*: every clock case is run at devnet or simulated budgets and extrapolated per `qa-rollups-stack-v1/docs/mainnet-fidelity.md`.

## Using qa-tester-app

[Mugen-Builders/qa-tester-app](https://github.com/Mugen-Builders/qa-tester-app) is a C++ rollups app that is told, input by input, what to do: emit N notices, reports, vouchers or delegate-call vouchers with chosen destination, value and payload, emit a raw output, echo, reject after reporting, raise a guest exception, halt or crash the guest, yield with a bogus reason, publish a wrong outputs root, burn cycles, and keep a libcma ledger of every portal deposit whose USD balances live in a real **accounts drive** that the alpha.10 contracts can prove. It ships a real `WithdrawalConfig` (guardian = Anvil account 0, 2^17 accounts of one leaf, `TestUsdWithdrawalOutputBuilder`, drive index 560). The echo app has none of this: it can only echo an input as a notice, voucher and report, and its withdrawal config is zero, so Areas B, F and G cannot be driven end to end on echo at all. The tester app is the right application for this plan.

What it covers, by case:

| Area | Tester command | Cases |
| --- | --- | --- |
| A, B | `emitVouchers(count, dest, value, payload)` | A1, A2, A9 (vouchers to prove against staged, claimed and old roots), B1-B5 (replay, reentrancy, insufficient funds, false-returning ERC-20, reverting destination) |
| B | `emitDelegateCallVoucher(dest, payload)` | B6 (code in the Application's storage context), F8/F10 via `withdrawErc20Safe` |
| B | `emitRawOutput(bytes)` | B7, B8 (unknown selectors, malformed outputs that still hash into the tree) |
| A | `corruptOutputsRoot(mode)` | A4 (a winner whose tx buffer holds a wrong or short root cannot be staged), plus the sling's reaction to an app that publishes a wrong root (new case: the node's `INVALID_OUTPUTS_ROOT` equivalent) |
| F | portal deposits (all five kinds) + `WithdrawErc20`, `withdrawErc20Safe`, `WithdrawEther`, `WithdrawErc721`, `WithdrawErc1155Single` | F4 (refunds of every kind after foreclosure), F8 (`withdraw` per account from the proved accounts drive), F10 (double exit: a user with a drive balance and an executable voucher) |
| F | `raiseException`, `halt(mode)`, `crash(kind)` | F13 (terminal application: node holds, guardian forecloses, funds exit) |
| F, G | `setDepositPolicy(1|2)` | deposits the app rejects: what the ledger, the refund path and the accounts drive say about them |
| G | `echo(bytes)`, `emitNotices(count, 2097056, pattern)` | G4 (64 KiB inputs), output-size boundaries |
| G, I | `manualYield(reason, data)` | G6 (bogus yields: the sling's reaction versus the on-chain step), SLING-2 cell (d) class |
| C, G | `burnCycles(iterations, progress)` | C10 and G5 (input backlog that outruns the root window), per-input compute contract |
| E, H | `batch(bytes[])` | many actions per input to stress snapshot and advance batching under kills (H4) |

What it does not help with: bonds and refunds (Area D), clocks and provider trust (Area E), node state (Area H). Those are protocol and node behaviour, independent of the application.

**Porting it to this stack.** The repo pins emulator 0.21.0, Cartesi CLI 2.0.0-alpha.38 and SDK 0.12.0-alpha.44, so `cartesi build` stores an archive-7 template the alpha.6 node refuses. There is no SDK on emulator 0.22 yet (the newest, 0.12.0-alpha.44, predates the 0.22.0 release). The path that works today:

1. `cartesi build` for the drives only: it produces `.cartesi/root.ext2`, `.cartesi/app.sqfs` and `.cartesi/image/config.json`. The riscv64 build runs under Docker's qemu emulation, which this Mac has.
2. Boot and store the template with `cartesi/machine-emulator:0.22.0`, replicating the CLI's machine config from that `config.json` (two flash drives, the 64 MiB `state` NVRAM on `/dev/uio0`, the `[machine.env]` exports, the entrypoint), with the same kernel (`linux-6.5.13-ctsi-2-v0.21.0.bin`) the echo template uses.
3. Re-check `accounts_drive_start_index` against the stored config (NVRAM start `>> 22`); the drive sizes decide it.
4. Set `QA_SAFE_ERC20_TRANSFER` to the devnet `SafeErc20Transfer` (`0xBB7c5367B74544542F5cE553Ed0DF4B3efAfEEdD`); the portal and token addresses in its `cartesi.toml` already match this dump (rollups-contracts alpha.10 is unchanged).
5. Deploy with `DaveAppFactory.newDaveApp(hash, stagingPeriod, sentryManager, sentries, (guardian, 0, 17, 560, builder), salt)` so the guardian and the withdrawal builder are live, and bake it into `anvil/state.json` next to echo.
6. Its "terminal states on the node" table describes the Go node; the sling reacts differently (SLING-2 and SLING-6 showed exit 1, exit 101 and a retry loop), so each terminal command becomes a new sling case rather than a known answer.

Done on 2026-10-22: the drives built with CLI 2.0.0-alpha.37 under riscv64 emulation (its own boot step fails, harmlessly), the template stored with `cartesi/machine-emulator:0.22.0` as archive 8, hash `0xf345a724da144e7acb8897b73dcb972f08d7f7baae33922e721e70115b44cc98`, NVRAM at `0x8c000000` so the drive index is 560 as in the repo's config. It is deployed in `anvil/state.json` as `0xbd6536cb8df504acb6d0856029344e2b07608bde` (consensus `0x993c46cbc50226319ea9668b235c7e7a5e708d64`, guardian account 0, USD builder, sentry account 7, staging period 1000) and a sling node seeded from it, computed a notice, a voucher and an Ether deposit, and joined its epoch. Recipe and deploy command: [tester/README.md](tester/README.md). The hash differs from the repo's `0x8e4f27b6…` because v0.22 changes every machine hash.

## Added 2026-10-22 (second pass): edge cases the alpha.6 Lua sybil runner makes drivable

Same table shape as the areas above. All run on fresh no-sentry tester-app instances unless the case says otherwise, with the sybil runner from `harness/sybil/` and the fault proxy from `harness/qa/rpc_fault_proxy.py`; results in `harness/reports/github-drafts/2026-10-22-funds-safety-disputes.md`.

| ID | Case | Run | Expected | Source |
| --- | --- | --- | --- | --- |
| C12 | **Dispute inside a running input**: sybil steered onto a transition at big cycle 2^22 of a `burnCycles(1.2M)` input (`TRANSITION=2^42-1`), so the height-38 child is built over 2^18 live instructions by both sides; also the oracle-vs-sling root on a real input (I2) | stack (long) | roots equal; both sides build the child commitment; leaf resolved by STEP; measure the child build time on each side against `T` | `computation/commitment.lua` `build_small_machine_commitment`, `engine/cache` "computing quartet" |
| C13 | **Honest node paused past its whole clock** mid-dispute (C6 taken to its end): sybil with a wrong final state wins the match by timeout, a third party stages the wrong state with a valid machine proof, accept lands, the node comes back | stack | node logs the loss at ERROR; the wrong state settles (C = 0, nobody else defends); the node detects the divergence at the next epoch and exits 30, never builds on the foreign state | `docs/epoch-lifecycle.md`:249-253, C6 |
| C14 | **Three sybils at once** against one sling (each at a different root leaf), the sling at one tx per tick | stack | honest result; count the sling's moves and reverts; time to settle | SLN-13, `multi_sybil.py` |
| C15 | **Late sentry vs a staged wrong result**: app deployed with one sentry (account 33) and no node; a sybil wins epoch 1 by walkover with a wrong final state and a third party stages it; the sentry node starts after staging | stack | the sentry node computes its own state and `submitSentryClaim`s the honest one (a dissent); the staged result is still acceptable after the staging period (sentries only shorten it); the node's reaction to the foreign accepted state | DC:162-191, 241; CL "sentry also claims its own computed state" |
| E9 | **Submit hold of 60 s** (`HOLD_SENDRAW_SECS=60`, more than 2G), after E5 at 20 s and 30 s | stack + proxy | lateness above the grace drains the clock; at this R the honest side should run out before the leaf; quantify blocks lost per move and where it dies | `docs/runbooks/submit-relay.md`:16-38, E5 |
| H8 | **Two nodes, one key, through a real dispute** (H1 only drove the uncontested path) | stack | nonces interleave or collide on advances; count reverted shared-key transactions; the dispute is still won and the epoch settles | H1, `provider.rs` nonce handling |

---

# Phase 2 (2026-10-23): the areas the first run left thin

All local. Adversary: `prt-harness/sybil-runner:3.0.0-alpha.6` (`harness/sybil/`). Mainnet budgets come from a second parameter set deployed on the local chain: `CanonicalTournamentParametersProvider(12000, 604800, 300)` from the release artifacts, a `MultiLevelTournamentFactory` on it and a `DaveAppFactory` wired to it ("the mainnet factory"); apps created there have `maxAllowance = 51,100`, `G = 25`, `T = 600` blocks. Where a case needs the honest build budget shortened, deploy a provider with `commitmentBudget` of a few minutes instead and say so. Nodes run with `--polling-interval 1 --snapshot-gap-inputs 1` on Docker volumes (not bind mounts; G5).

## Area K · Clocks under censorship (P0)

Invariant: with censorship ≤ C and every honest action within its window, the honest commitment is never eliminated; lateness is charged exactly as the contracts say.

| ID | Case | Expected | Source |
| --- | --- | --- | --- |
| K1 | Mainnet factory, one sybil, honest node delayed by exactly `G` blocks at one root response (hold its tx in the pool, mine `G`, release) | response accepted, no discount; clock balance unchanged after the move | `libs/Clock.sol`:148-161 |
| K2 | Same, delayed `G + k` blocks for k = 1, 10, 100 | accepted while `elapsed < balance`; balance reduced by exactly k; at `elapsed >= balance` rejected and the honest side eliminated by timeout | Clock.sol:148-161, `MatchClocks.sol`:91-119 |
| K3 | Cumulative lateness across root and child summing to `C - 1` then `C + 1` | survives at `C - 1`; eliminated at `C + 1` (C is one global budget) | `docs/dispute-game.md`:45-49 |
| K4 | Child-return refill: sybil delegates twice into children; measure the honest clock after each return | returned clock is `min(carried + T + 2G, max(r1, r2))`, never above the envelope | `MatchClocks.sol`:184-194, T:777-789 |
| K5 | CF-01 handover at the leaf: sybil joins the child one block before closure with a one-block clock and seals immediately | proof reverts `CannotAdvanceTimedOutClock`; node's fallback timeout claim lands; total within one `G` | T:594-600 |
| K6 | Honest node offline across a response deadline (stop container for `G + 5` blocks) on the mainnet factory | lateness drawn from C; dispute still won; the node logs the overdue as WARN then ERROR after 5 min of wall clock | `node-architecture.md`:200-217 |
| K7 | Honest node offline past its whole balance | eliminated; `ERROR` naming the loss; node exits 30 at the next epoch after the divergent state settles | `epoch-lifecycle.md`:249-253 |
| K8 | Root join at `G - 1` blocks after root start on the mainnet factory, then the worst in-bound child join at `T + G - 1` | survives with the in-flight reserve (F-01 regression on the live contracts) | `ClockBudgets.sol`:63-78 |

## Area L · Adversaries at scale (P1)

| ID | Case | Expected | Source |
| --- | --- | --- | --- |
| L1 | 8 sybils, 4 per final-state class, one root | honest wins; root finishes only after sybil-vs-sybil matches are eliminated by a third party; count honest actions and gas | T:369-397, 1059-1065 |
| L2 | Staggered release: sybil pairs formed inside the join window release survivors one per `G` after closure | honest side plays one match per release; settlement delayed by about `(A + (h-2)G)` per the L-03 bound; no clock loss | `docs/prt-delay-bound.md`:240-256 |
| L3 | Delay per root bond: one sybil joins `G + 1` blocks before closure and delegates once | settlement delayed by about one allowance `A` for one bond (L-02) | DB:211-238 |
| L4 | Node resources during L1: RPC requests per tick, RSS, data-dir growth | bounded; requests grow linearly in sybils at dispute start (documented); no OOM | `provider-requirements.md` |
| L5 | Sybil joins the honest final-state class with a distinct root and wins the child (obligation 20) | parent propagates the same-class win; honest commitment survives | T:758-771 |

## Area M · Economics with real gas (P2)

| ID | Case | Expected | Source |
| --- | --- | --- | --- |
| M1 | `anvil_setNextBlockBaseFeePerGas` to 300 gwei during the honest leaf proof; signer funded at the README floor minus 10% | per-tick ERROR about the reserve; proof not sent until fee drops or balance tops up; clock loss measured | node README "Fund the signer" |
| M2 | Same spike with the signer at the floor | proof lands; refund = `min(allocation x 50 gwei, units x (basefee + 10 gwei))`, so the node pays the difference; net cost recorded | T:1292-1312 |
| M3 | Claimer-slot capture payout: adversary joins the honest root first (D3), honest node wins; call `tryRecoveringBond` | payment goes to the adversary address; the honest node's balance shows only gas spent | T:435-449, DG:830-832 |
| M4 | Maximum input (65,508-byte blob) through a real leaf proof | `winLeafMatch` gas and refund against `WIN_LEAF_MATCH` 5,536,000; proof bytes metered at 16/byte | `libs/Gas.sol`:17-35 |
| M5 | Wrapper contract that re-submits the honest action with padded calldata to farm the per-byte refund | padding rejected by the state transition (`InputBytesWithoutInput`); only the TX constant is farmable | T:582 |

## Area N · Operations across epochs (P1, soak)

| ID | Case | Expected | Source |
| --- | --- | --- | --- |
| N1 | 24 h soak: one app, inputs every 30 s, epochs rolled every 200 blocks, node SIGTERM-restarted every 20 min, `anvil_reorg` depth 3 every hour | every epoch settles with the oracle's state; bonds recovered once each; DB and snapshot count bounded (old-epoch snapshots dropped) | `epoch-lifecycle.md` "Node data flow" |
| N2 | Second node seeded from the settled state of epoch k (`--template-path` = snapshot dir) while the first keeps running | starts at k, never touches earlier epochs, joins k's tournament; earlier bonds stay recoverable by anyone | README "Operator notes" |
| N3 | Signer change mid-dispute (stop, restart with another key in the same dir during a match) | the new signer continues the match (actions are permissionless); old signer's bond recoverable by anyone | EL:150-167 |
| N4 | SIGKILL during the leaf commitment build and during the `winLeafMatch` send | resumes; proof lands within the window or the fallback claim does | NA:592-608 |
| N5 | `anvil_reorg` of depth 3 across the honest join and across a seal | no stale mutation lands; the Foam revalidates; join re-sent from finalized state | NA:545-588 |
| N6 | Graceful stop (SIGTERM) during an input batch and during a dispute tick | batch dropped and replayed; prepared action still goes out; exit 0 | README "Stop the node" |

## Area O · Sentries, guardian, exit at scale (P1)

| ID | Case | Expected | Source |
| --- | --- | --- | --- |
| O1 | 3 sentries, all honest nodes: acceptance in the staging block once the third claim lands | accept succeeds before the period; claims counted per hash | DC:193-216, 464-466 |
| O2 | 3 sentries, one claims a different hash, period 50 | accept only after the period; dissent visible as a claim with another hash | DC:250-256 |
| O3 | Sentry rotation by the manager mid-epoch; the new sentry claims | rotated ID keeps prior claims; new address can claim | DC:283-296 |
| O4 | Sybil wins the root (C7 setup) with a sentry node running late | sentry logs its own computed state as a dissent claim; guardian forecloses in the window | CL "sentry also claims" |
| O5 | Exit at scale: 50 ledger accounts after foreclosure; prove the drive once; 50 `withdraw` calls | each pays once; gas per withdraw recorded; second call `AccountFundsAlreadyWithdrawn` | APP:257-281 |
| O6 | Exit tooling on v0.22: `account-driver-reader --dump-proof` against a v0.22 snapshot; `cartesi-rollups-machine-tool prove accounts-drive` | expected to fail on archive 8 (reader links 0.20); the emulator's `--initial-proof` path is the only working route; document the user-facing gap | qa-tester-app `docs/accounts-drive.md` |

## Area P · Seams (P1)

| ID | Case | Expected | Source |
| --- | --- | --- | --- |
| P1 | Accept yield with declared length ≠ 32 (tester `corruptOutputsRoot 1/2`) through a dispute to the leaf proof | node and on-chain step agree, or the proof settles the disagreement; staging fails on the wrong root | `SendCmioResponse.sol`, DC:456-462 |
| P2 | Yield exactly at `mcycle == imcyclemax` (needs a crafted input; `burnCycles` tuned) | node, CLI and step agree (SEAM-4 on v0.22) | `computation-hash.md` |
| P3 | Declared length above the TX buffer (SLING-6 guest) through a dispute | node stalls (known); the step accepts; document the divergence with a proof trace | `machine_stf.rs`:115-120 |
| P4 | Exception input (`raiseException`) and halt through a dispute to settlement attempt | epoch defended, `MachineValidityProof` fails on staging, terminal ERROR | EL:70-78 |
