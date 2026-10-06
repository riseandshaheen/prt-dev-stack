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

R5 and R5b need dave's sybil runner image. From a dave checkout at `v3.0.0-alpha.5`:

```sh
docker build -f test/Dockerfile --build-arg DAVE_EMULATOR_GITLINK=$(git rev-parse :machine/emulator) -t prt-harness/sybil-runner:3.0.0-alpha.5 .
```

The image is 5.2 GB and the build cache is 11 GB more; run `docker builder prune` afterwards. `harness/sybil/sybil.lua` drives dave's own Lua sybil against this Anvil. It replays every sealed epoch from the template first, which also checks both nodes' commitments against a third implementation.

## Limits

- Anvil orders transactions by fee, so the harness can put its own transactions before or after a node's, but it cannot reorder two nodes' transactions. R1 and R2 report the order that happened.
- I7 (convergence and nonce gaps) is written but not yet called by any scenario.
- dave's Lua client keeps one machine snapshot per input under `/tmp`; sybil and oracle containers get a 6 GB tmpfs cap and refuse to start with less than 20 GB free. Run them on a short chain history.
- There are no checkpoints yet: every scenario starts from wherever the chain is.
- The P1 and P2 scenarios from the design are not implemented.
