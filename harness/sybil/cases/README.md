# Dispute case drivers (2026-10-22)

Shell drivers used for the funds-safety dispute cases (`harness/reports/github-drafts/2026-10-22-funds-safety-disputes.md`). They are bash scripts around `cast`, `docker` and the sybil runner image; they are not wired into `harness/run.py`.

Set `CASES_RUN` to a scratch directory (default `/tmp/prt-cases`; it gets `run/dd/` with one data directory per node and `run/out/` with logs and `apps.txt`, the case → app, consensus map). Everything assumes the stack's Anvil at `127.0.0.1:8545`, the tester template at `tester/machine-image`, and foundry's `cast` on the PATH.

| File | What it does |
| --- | --- |
| `env.sh`, `env2.sh` | helpers: `deploy_app` / `deploy_app2` (tester app with the real withdrawal config), `start_node` / `start_node2` (one sling per app), `sybil` (one player container), `mine`, `canstage`, `standing`, the 40 Anvil dev keys by index |
| `ticker.sh` | one `anvil_mine` per second until the stop file exists; the players never mine |
| `dispute_case.sh NAME SALT PORT PK PLAYER "PROXY ENV"` | deploy, run `rpc_fault_proxy.py` with that env, sling behind it, wait for its join, fighting sybil (E2, E5, E9) |
| `edges.sh c1long\|c6\|multi\|h1d\|c7s`, `edges2.sh d3race\|c9tp\|seam\|h1d2` | C12, C13, C14, H8, C15; the D3 race, C9 third-party elimination, I1 seam and the clean H8 |
| `c2_epoch1.sh` | C2: sybil A at the root, sybil B into the child as soon as it exists |
| `c9x.sh`, `c9x_elim.sh` | C9: two silent sybils ahead of the sling, third-party `eliminateMatchByTimeout` |
| `c4.sh`, `copier.py` | C4: automine off, copy the sling's pending transactions from another account at 4x fees, mine |
| `watch.sh`, `wait_sealed.sh` | pollers for `EpochSealed` / `EpochStaged` / `canStageTournamentResult` |

Deployments from one key must not overlap (CREATE2 nonce collisions): `edges.sh` serializes its own with a lock, `dispute_case.sh` does not, so start it alone.

## Phase 2 (2026-10-23)

`p2.sh k12|k6|k8|l1|l3|p1|p4|m3|m5` runs on the shared Anvil (8545); `p2k.sh k3a|k3b|k4|k7` on a private Anvil (`CHAIN_PORT`, default 8546) where bursts are allowed. `env2.sh` adds `deploy_app3` (chosen factory, deployer account 39), `start_node3` (data directory on a named Docker volume) and `proxy`; `mainnet-factory.env` has the mainnet-parameter factory's addresses (same on every chain started from the dump with that deployer); `clocks.py TOURNAMENT FROM_BLOCK` prints each side's remaining clock after every move. Results in `harness/reports/github-drafts/2026-10-23-funds-safety-phase2-disputes.md`.

`p3.sh m5|m5base|p1|l3|k4|k7|k3a|k3b` is the fourth pass (private Anvil 8546, one case at a time, `step_mine` pauses the named containers while advancing in 500-block steps); `p3_all.sh` runs them in sequence.

`p4.sh k7s2|k3sc|k3sd` (and the first attempts `k7s`, `k3sa`, `k3sb`) is round 3 on the shortened-budget provider (`short-factory.env`: `CanonicalTournamentParametersProvider(12000, 3000, 300)`, allowance 950); results in `harness/reports/github-drafts/2026-10-27-funds-safety-round3-disputes.md`.
