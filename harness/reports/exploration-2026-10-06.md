# Exploration log, 2026-10-06

Untouched areas (not covered by any QA claim as of SLN-56), run against the published
`sling-node:3.0.0-alpha.5` with `HARNESS_NODES=sling` (reference node stopped).

## 1. Crash / restart (`crash_restart`, ID CRASH)

Kills are `docker kill` (SIGKILL); the sling's state volume is kept.

### K1: killed while computing the new epoch
Run bundle `runs/20261006-153041`.

| delay after EpochSealed | epoch | sling joins after restart | ERROR/panic lines |
| --- | --- | --- | --- |
| 0.5 s | 15 | 1 | 0 |
| 3.0 s | 16 | 1 | 0 |
| 8.0 s | 17 | 1 | 0 |

Same commitment each epoch (`0x917c7bcc89…`; echo with no new inputs). **Pass.**

The first run then stopped at K2 because of a harness bug, not a sling one: the sling ingests
only up to the finalized block (Anvil: head - 2), so its join appears only after two more blocks;
the scenario had stopped mining at the seal. Fixed (mine one block at a time until the join is pending).

### K2: killed with its join in the mempool
Run bundle `runs/20261006-153918`.

| case | epoch | joined | sling txs | reverts |
| --- | --- | --- | --- | --- |
| K2a join mined while the sling is down | 20 | yes | join, accept | 0 |
| K2b join dropped from the mempool while down | 21 | yes (resent within ~3 blocks of restart) | join, accept | 0 |

**Pass.** A restart rebuilds the action from chain state and resends it.

### K3: killed repeatedly during a sybil dispute
| kill every | down | epoch | kills | sling moves | result |
| --- | --- | --- | --- | --- | --- |
| 3 moves | 2 blocks | 22 | 16 | 48 | **won** (12.4 min, 0 reverts, 0 errors) |
| 1 move | 5 blocks | 23 | 2 | 2 | **LOST**: sybil `winMatchByTimeout` at block 8501 |

## FINDING candidate: a kill during startup bricks the node permanently

The second K3 kill landed ~3 s after the previous restart, while the sling was still starting.
Every start since fails before any worker runs:

```
Error: could not create `storage`
  0: Inner error: `Cartesi Machine error -6: unable to create directory '/state/snapshots/.work-1-0': File exists`
```

`docker start` twice more: same error, exit 1. The volume holds `/state/snapshots/.work-1-0` with a
half-cloned machine (366 MB `.bin` and the rest, 10:27). Evidence: `runs/20261006-153918/CRASH/evidence/`.

Source (dave `79874c0`):
- `args.rs:284-290`: `Storage::initialize(...)` on **every** start ("could not create `storage`").
- `storage/open.rs:69`: `initialize` always calls `set_initial_machine`, which `checkout`s the template
  machine into the staging namespace (`storage/open.rs:183-186`).
- `storage/snapshots.rs:249-257`: the working clone is named `.work-{process id}-{seq}`. In a container the
  node is PID 1 and seq starts at 0, so every start uses the same name `.work-1-0`.
- `storage/snapshots.rs:277-293`: `sweep_stale_staging` removes orphaned `.work-*`, documented as running
  "before any worker holds a clone", but it is called from `lib.rs:57` (`run`), i.e. **after**
  `Storage::initialize` has already tried to clone into `.work-1-0`. The sweep is never reached.

So a SIGKILL / OOM kill / container stop during the few seconds of the startup clone leaves a
directory that makes every later start fail at the same path. With `restart: always` or a Kubernetes
liveness probe it crash-loops; the node defends nothing until an operator deletes the directory by
hand. Dispute impact here: the sybil won epoch 23 by timeout.

Why dave's own crash tests miss it: `chaos` and `kill_*` (`docs/test-harness.md`) kill at protocol
events after startup and respawn the node as a host process, which gets a new PID, so a leftover
`.work-<old pid>-0` never collides (and the later sweep removes it). In a container the node is PID 1
on every start (image entrypoint is the binary, no init), so the name repeats.

### Deterministic repro: `harness/qa/startup_kill_repro.sh`
Throwaway sling container + new volume against the stack's Anvil (the stack's sling untouched). A
read-only sidecar watches for `.work-1-0`; the script stops the node the moment it appears.

| run | stop method | result after restart |
| --- | --- | --- |
| CONTROL | SIGKILL well after startup | running |
| TRIGGER (`runs/startup-kill-20261006-160846`) | SIGKILL while `.work-1-0` exists | **exit 1 on 3 of 3 further starts**, `unable to create directory '/state/snapshots/.work-1-0': File exists` |
| TRIGGER (`runs/startup-kill-20261006-161004`) | `docker stop` | running: SIGTERM ignored (PID 1, no handler yet), SIGKILL arrived after 10.3 s, after the clone had been published |

Window: `.work-1-0` exists for **1.0-2.0 s** of every start (3 starts measured; 408 MB echo machine,
macOS Docker VM on SSD). It scales with machine size and disk speed; a clone longer than the stop
grace period (10 s Docker, 30 s Kubernetes default) would also turn a graceful stop into the trigger.

Triggers in practice: OOM kill or host crash/power loss during startup, `docker kill` / `kill -9`,
a Kubernetes liveness/startup probe or eviction during boot, any stop whose grace period is shorter
than the clone. Once hit, the node never starts again until someone deletes the directory.

Severity thinking: low likelihood per start (short window), but the effect is permanent and silent
to the protocol (no tx, no on-chain signal); with `restart: always` it shows as a crash loop.
Leaning **medium** if the sling is an app's only defender (the K3 run: sybil won by timeout), else low.
Fix: run `sweep_stale_staging` before `Storage::initialize`, or name the clone with a random/unique
suffix, or remove an existing `.work-*` target before cloning.

Manual recovery confirmed: after deleting `.work-1-0` the node started, caught up, and logged `local commitment lost dispute tournament for epoch 23` (`runs/20261006-153918/CRASH/evidence/sling-after-manual-recovery.log`). Severity agreed: **low** (needs a containerised node).
