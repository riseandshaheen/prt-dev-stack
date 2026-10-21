Local stack for testing the PRT nodes against one application on one Anvil.

Sling (`cartesi-sling-node` from dave [`v3.0.0-alpha.6`](https://github.com/cartesi/dave/releases/tag/v3.0.0-alpha.6)) runs against the Dave v3 devnet in `anvil/state.json`. It does not start a second chain.

```sh
docker pull ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.6
docker pull ghcr.io/riseandshaheen/sling-anvil:1.5.1
```

Dave `v3.0.0-alpha.6` is a new deployment generation: every PRT address moved, the tournament events changed, and the node links Cartesi Machine v0.22, which refuses machines stored by v0.21. So this repo's dump, echo machine and image are all rebuilt for it:

| Piece | Now | Was (alpha.5) |
| --- | --- | --- |
| Anvil | Foundry 1.5.1 (`sling-anvil:1.5.1`) | 1.4.3 |
| `anvil/state.json` | the release's `-anvil-1.5.1` devnet, canonical geometry, with echo baked in at block 24 | three-level devnet, echo at block 25 |
| Echo app | `0x53563a918B43Ca40FD966Ea1f065072383f33DB7`, consensus `0x6A2E66b5F8d14237d8b858B987dC887bb4Df27cF` | `0x6c2E…6146` |
| Echo machine | template `0x27715ccf…382f`, stored by Cartesi Machine v0.22.0 (archive 8) | `0xc8217d7f…f3b2`, v0.21 |
| Sling flags | `--blockchain-http-endpoint`, `--blockchain-id`, `--data-dir`, key in `CARTESI_SLING_AUTH_PRIVATE_KEY` | `--web3-*`, `--state-dir`, `--web3-private-key` |

The devnet addresses are in [`anvil/deployments/31337/`](anvil/deployments/31337/). `DaveAppFactory` is `0xF8f6dFA3A3e40C10926839FFd8a221C87C8b2ccD`; `InputBox` keeps `0xEbE9f4Dfc04ae10bBeE663859c3dc5A23f94eA3C`.

## Reference node

`reference/` still targets rollups-node `v2.0.0-alpha.13`, which is built against Dave `v3.0.0-alpha.5` contracts. It cannot read the alpha.6 deployment in this dump, so do not start it until a rollups-node release built against Dave `v3.0.0-alpha.6` exists. The alpha.5 stack (both nodes) is the previous commit of this repo; `ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5`, `sling-anvil:1.4.3` and `rollups-node:2.0.0-alpha.13` stay published.

The echo machine is a release tarball, not a dave clone. See [echo/README.md](echo/README.md).

Walkthrough: [docs/quickstart-macos.md](docs/quickstart-macos.md).

## Visualiser

[`visualiser/`](visualiser/README.md) is a read-only web view of the chain and every node: epochs, tournaments, live clocks and transactions. It was written against Dave `v3.0.0-alpha.5` (its event ABIs and sling's SQLite schema); alpha.6 changed both, so it needs its ABIs regenerated and its sling reader updated before it shows this stack.

## Harness

[`harness/`](harness/README.md) is a break-testing harness for this stack. It takes over mining, drives the nodes through epochs, disputes and faults, and checks after every block that chain and nodes agree. Like the visualiser it is pinned to alpha.5 (addresses in `prt_harness/stack.py`, the alpha.5 sybil image, the reference node) and needs an update for alpha.6. Its scenarios follow [test-design-claude.md](test-design-claude.md). Write-ups are in [`harness/reports/`](harness/reports/).
