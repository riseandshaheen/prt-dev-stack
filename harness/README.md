# Harness

Break-testing for sling and the reference node running against each other on this stack's Anvil. It implements the P0 part of [test-design-claude.md](../test-design-claude.md).

The harness is the only miner. It turns Anvil's automine off, mines every block itself, and after each mine checks invariants I1–I6 on a three-way view of the chain, sling and the reference node. That view comes from the visualiser's `vis` package, so the harness and the UI read the same model.

## Run

Start the stack as in [docs/quickstart-macos.md](../docs/quickstart-macos.md), then from the repository root:

```sh
python3 harness/run.py                            # every scenario, in catalog order
python3 harness/run.py g0_golden c4_forged_sentry # some of them
```

Python 3.10 or later, with nothing to install. The harness reads chain, sling and reference-node state through the visualiser's `vis` package in [`../visualiser`](../visualiser/README.md), so the harness and the UI share one model. Set `VIS_DIR` only if you keep the visualiser elsewhere.

Environment: `HARNESS_FAST=0` restores the fixed-step clock (the default jumps to the next deadline whenever nothing is pending and no match is live); `HARNESS_NODES=sling` watches only the sling, for runs with the reference node stopped.

Each run writes `harness/runs/<timestamp>/<scenario>/` with `result.json` (checks, findings, invariant violations), `timeline.json`, `ledger.json` (every transaction with its decoded revert reason) and both nodes' logs.

## Scenarios

| ID | Module | Changes the stack for good |
| --- | --- | --- |
| G0 | `g0_golden` | No |
| R1 | `r1_join_race` | No |
| R2 | `r2_stage_race` | No |
| R3 | `r3_accept_race` | No, but adds inputs |
| R4 | `r4_sentry_path` | No |
| C4 | `c4_forged_sentry` | No, but one epoch takes the 1000-block path |
| R5 | `r5_twin_defenders` | No |
| R5b | `r5b_defender_absent` | Yes: the app halts. Rebuild the stack afterwards |
| T3 | `t3_chain_reset` | Yes: restarts Anvil. Rebuild afterwards |
| T3b | `t3b_resume_after_reset` | Run after T3 |
| SLN-39 | `qa_sln39` | No (uses a probe sling) |
| SLN-38 | `qa_sln38` | No, but the probe bonds a wrong commitment |
| SLN-8 | `qa_sln8` | No, but leaves a foreign bond unrecovered |
| SLN-37 | `qa_sln37` | No, settings restored |
| SLN-6 | `qa_sln6` | No |
| LOGRANGE | `qa_logrange` | No (uses a probe sling) |
| GAP64 | `qa_gap64` | No |
| REF-NODEF | `qa_ref_no_defense` | Yes: the app halts |
| TICKERR | `qa_tick_errors` (`HARNESS_NODES=sling`) | Yes: part C leaves a rival winner |
| MONKEY | `monkey_sling` (`HARNESS_NODES=sling`, `MONKEY_HOURS`, `MONKEY_SEED`) | Stops the reference node |

The `qa_*` scenarios reproduce claims from [Mugen-Builders/qa-slingnode-v0](https://github.com/Mugen-Builders/qa-slingnode-v0) and test new ones. Most fault the RPC through `qa/rpc_proxy.py`, vendored from that repo with added modes (random errors, dropped send replies, a deterministic dropped input, custom messages). A **probe sling** is a second sling container with its own volume and key, so faults reach only it; `harness/stack/sling-proxy.yaml` routes the stack's own sling through the proxy instead.

Results are written up in `reports/`.

To rebuild, run `docker compose down -v` in `sling/` and `reference/`, restart Anvil, then follow the quickstart from step 3.

## Sybil runner (dave v3.0.0-alpha.6)

Dispute scenarios (R5, R5b, MULTISYBIL and the funds-safety plan's C/D3/E cases) need an adversary that builds and plays PRT commitments: dave's Lua client. The alpha.6 release removed `test/Dockerfile`, so the image is built here from the release tree alone, on top of the Cartesi Machine 0.22.0 image (which ships the `cartesi` Lua 5.4 bindings the client needs), plus `cast` (taken from the stack's `sling-anvil:1.5.1` image) and `jq`, which the client shells out to. No Rust node, devnet or test programs: 617 MB, builds in under a minute.

```sh
B=$(mktemp -d) && mkdir -p "$B/dave"
git -C /path/to/dave archive v3.0.0-alpha.6 prt/client-lua test/e2e prt/contracts/src cartesi-rollups/contracts/src | tar -x -C "$B/dave"
# optional: the compiled ABIs the client's event-layout check reads (from a `just build-smart-contracts` tree)
cp -r /path/to/dave/prt/contracts/out/{ITournament,Tournament,MultiLevelTournamentFactory}.sol "$B/dave/prt/contracts/out/" 2>/dev/null
cp harness/sybil/Dockerfile "$B/" && docker build -t prt-harness/sybil-runner:3.0.0-alpha.6 "$B"
```

Or pull the published build (same recipe, linux/arm64 only, like the rest of this stack) and give it the local name the harness uses:

```sh
docker pull ghcr.io/riseandshaheen/sybil-runner:3.0.0-alpha.6
docker tag ghcr.io/riseandshaheen/sybil-runner:3.0.0-alpha.6 prt-harness/sybil-runner:3.0.0-alpha.6
```

`harness/sybil/sybil.lua` is the player: it replays every sealed epoch of an app from its template with the Lua oracle (which also cross-checks the sling's root commitment against a second implementation), then joins the epoch's tournament with a patched commitment and plays dave's honest strategy with it. It takes the deployed table from the chain (alpha.6 devnet: `log2step [38, 0]`, `height [54, 38]`), steers the dispute onto a chosen transition with one patch per level (`TRANSITION`), can claim a wrong but provable post-epoch state (`WRONG_FINAL=1`), play inside a child tournament (`TOURNAMENT`), or join the honest commitment and go quiet (`MODE=honest`, a claimer-slot adversary). `prt_harness/sybil.py` wraps it (`Sybil(epoch, player, mode, transition, ...)`). `harness/sybil/validity_proof.lua` prints the `MachineValidityProof` for any stored machine, so a third party can `stageTournamentResult` without a node. The runner mounts the machine template read-only and writes its snapshots to a 6 GB tmpfs; account keys are Anvil's dev keys by index (`PLAYER`).

Run one by hand against a tester-app instance (see `tester/README.md` for deploying one):

```sh
docker run --rm --tmpfs /tmp:rw,size=6g --add-host host.docker.internal:host-gateway \
  -v "$PWD/harness/sybil:/harness:ro" -v "$PWD/tester/machine-image:/machine:ro" \
  -e ENDPOINT=http://host.docker.internal:8545 -e INPUT_BOX=0xEbE9f4Dfc04ae10bBeE663859c3dc5A23f94eA3C \
  -e APP=0x<app> -e CONSENSUS=0x<consensus> -e EPOCH=0 -e PLAYER=2 -e MODE=fight \
  prt-harness/sybil-runner:3.0.0-alpha.6 /harness/sybil.lua
```

The harness owns the clock: the player never mines, so run a block ticker (one `anvil_mine` per second is dave's own e2e cadence). The old `prt-harness/sybil-runner:3.0.0-alpha.5` recipe (`docker build -f test/Dockerfile ...` from a dave checkout at that tag, 5.2 GB plus 11 GB of build cache) is superseded.

## Limits

- Anvil orders transactions by fee, so the harness can put its own transactions before or after a node's, but it cannot reorder two nodes' transactions. R1 and R2 report the order that happened.
- I7 (convergence and nonce gaps) is written but not yet called by any scenario.
- dave's Lua client keeps one machine snapshot per input under `/tmp`; sybil and oracle containers get a 6 GB tmpfs cap and refuse to start with less than 20 GB free. Run them on a short chain history.
- There are no checkpoints yet: every scenario starts from wherever the chain is.
- The P1 and P2 scenarios from the design are not implemented.
