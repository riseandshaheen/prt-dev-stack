# DRAFT: behind dRPC or Cloudflare, the sling cannot sync and stays wedged while reporting healthy

Local draft, not yet claimed in qa-slingnode-v0. Reproduced Oct 6, 2026 on prt-dev-stack.

The sling splits an oversized `eth_getLogs` only when the error matches one of `--long-block-range-error-codes`, whose default is `-32005 -32600 -32602 -32616` (Infura, Alchemy, QuickNode). Two public providers answer an oversized range with codes outside that list: **dRPC** (`35`) and **Cloudflare** (`-32047`). Behind either, the sling cannot ingest from a fresh start. A running sling that falls behind by more than the provider's range limit stays stuck for as long as it uses that provider. The process keeps running and only logs a warning each tick.

## Provider survey

One oversized `eth_getLogs` (a USDC Transfer filter over 100,000 blocks) to each keyless public endpoint, plus local dev nodes with a 5-block limit. Oct 6, 2026:

| Endpoint | Code | Message | Default list |
| --- | --- | --- | --- |
| geth `--rpc.rangelimit` (local) | `-32602` | exceed maximum block range | yes |
| reth `--rpc.max-blocks-per-filter` (local) | `-32602` | query exceeds max block range | yes |
| rpc.flashbots.net | `-32602` | query exceeds max block range 100000 | yes |
| 1rpc.io | `-32602` | eth_getLogs is limited to 0 - 50 blocks range | yes |
| Blast public | `-32600` | up to a 10 block range | yes |
| **eth.drpc.org** | **`35`** | ranges over 10000 blocks are not supported on free plan | **no** |
| **cloudflare-eth.com** | **`-32047`** | 'fromBlock'-'toBlock' range too large. Max range: 800 | **no** |
| PublicNode | `-32602` | Archive requests require a personal token | yes, though not a range error |
| Merkle | `-32005` | Rate limit exceeded | yes, though a rate limit |

## Code

dave `79874c0`:

- `cartesi-rollups/node/src/args.rs:138-143`: the default codes, commented "-32005 Infura; -32600, -32602 Alchemy; -32616 QuickNode".
- `cartesi-rollups/node/src/chain.rs:149-171` (`logs_bisecting`): a matching error splits the range; any other error is pushed to `errors`, and the whole fetch returns `get_logs failed`. `chain.rs:180-185` matches codes as substrings of the error's Debug text.
- `blockchain_reader/mod.rs:164`: the failure is logged at warn level, "blockchain read failed, retrying next tick", and the next tick retries the same range.
- `docs/node-architecture.md:184-188` ("Chain ingestion stance"): "Oversized `eth_getLogs` ranges are handled by binary range partitioning, triggered by provider-specific error codes passed in as configuration."

The reference node takes the other approach: an explicit `CARTESI_BLOCKCHAIN_MAX_BLOCK_RANGE` (rollups-node `internal/config`) that never sends an oversized range.

## Reproduction

```sh
python3 harness/run.py qa_logrange
```

A probe sling (same image, its own state and key) reads Anvil through the vendored `rpc_proxy.py`, which answers any `eth_getLogs` spanning 100 blocks or more with the provider's code and message. The 10,000-block limit is scaled to 100 so it binds on this chain. The stack's own nodes read Anvil directly.

- **A**: fresh probe behind each provider, 60 s.
- **B**: probe synced through a pass-through proxy; the proxy switches to dRPC's code; the probe is paused while 150 blocks are mined, then resumed; then the proxy switches back.
- **C**: throttling, 30% of `eth_getLogs` failing at random with `-32005` versus an unlisted code.

## Result

Evidence: `harness/runs/20261006-011316/LOGRANGE/`.

| Case | Running | Processed block | Lag after 60 s | `eth_getLogs` | Log |
| --- | --- | --- | --- | --- | --- |
| A control, `-32602` | yes | 814 | 0 | 138 (split) | clean |
| A dRPC, `35` | yes | **5** | **839** | 13 | `blockchain read failed, retrying next tick: get_logs failed: [ErrorResp … code: 35 …]` every tick |
| A Cloudflare, `-32047` | yes | **5** | **869** | 13 | same, code `-32047` |
| B synced, then a 150-block gap behind dRPC | yes | 893 → **893** | **181** | 39 failed | same |
| B after switching back to pass-through | yes | caught up | 0 | | |

C, throttling: with `-32005` the probe made 81 `eth_getLogs` per minute against 60 at baseline (x1.35) and kept up. With an unlisted code it made 51 and fell 6 blocks behind. Splitting in response to a rate limit adds requests, but at the head's 1-2 block ranges the effect is small. Not part of this finding.

## Consequence in a dispute

Run `harness/runs/20261006-101702/TICKERR/` (`python3 harness/run.py qa_tick_errors` with `HARNESS_NODES=sling`): sling as the only validator, behind the proxy.

1. Epoch 4 sealed; sling joined its commitment `0x2394a8fc…` through a pass-through proxy.
2. The proxy switched to dRPC's code (35 over 100 blocks) and the sling fell 150 blocks behind.
3. dave's Lua sybil (Anvil account 2) joined a rival commitment and played its moves.

| | Result |
| --- | --- |
| Sling dispute moves | **0** |
| Sling failed ticks during the dispute | 312 (`blockchain read failed, retrying next tick: get_logs failed: … code: 35 …`) |
| Tournament winner | the sybil's `0x641dbfd0…`, not the sling's commitment |
| Container status throughout | running |

For contrast, in the same run with no wedge, the sling won the same kind of dispute with 48 moves while 20% and 30% of its `eth_getLogs` failed at random (the other faults cost time, not the dispute). The visualiser raised "the honest commitment's clock is running (41 blocks left) while Sling is 403 blocks behind" throughout.

This sybil (dave's `patched_commitment`) claims the honest final state, so the rival's win does not by itself finalize a wrong state. A sybil claiming a wrong final state would win the same undefended tournament, and staging is permissionless: it could stage its own result, and with the sling's sentry claim also stalled, acceptance follows after the claim staging period.

## Mainnet fidelity

Fidelity F0, with the provider limit scaled from 10,000 to 100 blocks.

| Parameter | Mainnet | Used | Outcome with the mainnet value |
| --- | --- | --- | --- |
| Provider range limit | dRPC free tier 10,000 blocks; Cloudflare 800 | 100 | Same mechanism. A fresh start fails whenever the first query spans more than the limit, which it does once the app is older than about 33 hours (dRPC) or 2.7 hours (Cloudflare) on Ethereum. |
| Downtime before the wedge (B) | Anything over the limit | 150 blocks | Ethereum: over about 33 h behind dRPC, or about 2.7 h behind Cloudflare. On a 2 s L2 the same block counts pass in about 5.5 h and 27 min. |
| Error codes | As surveyed | Replayed exactly by the proxy | Same. |
| Sling sleep | 30 s default | 5 s | Only the retry cadence changes. |

## Threat model

- RPC class `visible-error`: the provider fails loudly with a code the node can see. `docs/rpc-trust.md` treats this class as in scope.
- dave `docs/node-architecture.md` L184-188 documents that splitting depends on configured codes, so a missing code is partly operator configuration. What the docs do not say: which providers the defaults cover, that any other code means the node cannot ingest at all rather than falling back to smaller ranges, or that the node keeps running and reports nothing beyond a warning. Proposed relation: `not-covered` for the failure mode, on top of documented configuration.
- Sibling contrast: the reference node bounds every range by configuration and never needs the provider's error to split.

## Assessment

**What happens.** A sling pointed at dRPC's free tier or Cloudflare never ingests on a fresh start. An already-running sling stays stuck after any outage longer than the provider's limit. Throughout, the process is up and logs only a warning per tick, so a container health check or a "process alive" monitor sees nothing wrong. If that sling is the app's only active defender (on this stack it is, since the reference node sends no dispute moves), every dispute in that period goes undefended and can be lost by timeout.

**Who and cost.** No attacker is needed; it is a provider choice. dRPC is a common free endpoint.

**Recovery.** Add the code to `--long-block-range-error-codes`, or switch providers. The node then catches up at once (B). No redeploy.

**Severity: medium (proposed).** The dispute run shows the defender playing no moves while looking healthy, and the rival winning the tournament. That is a liveness and bond loss with no attacker needed beyond someone willing to dispute. It reaches **high** (a wrong result finalized) only if a sybil also claims a wrong final state while the sling is wedged; that was not run. The case for low is that the documented fix is configuration.

**Likelihood: plausible.** Confidence: high for the mechanism and the codes as surveyed today. Providers can change their codes.

**Suggested fix, in order of cost.**
1. Add `35` and `-32047` to the defaults, and document which providers the list covers.
2. On any `get_logs` error over a range wider than one block, split anyway after N repeats of the same error. The code list then becomes an optimisation rather than a requirement.
3. Offer a `--max-block-range` cap, like the reference node, so oversized ranges are never sent.
4. Surface a sustained ingestion stall in health or metrics, not only as a warning line.
