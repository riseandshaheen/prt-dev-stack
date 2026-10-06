Local stack for testing both PRT nodes against one application on one Anvil.

Sling (`cartesi-rollups-prt-node` from dave `v3.0.0-alpha.5`) and the rollups reference node share the Dave v3 devnet in `anvil/state.json`. They do not start a second chain.

```sh
docker pull ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5
docker pull ghcr.io/riseandshaheen/sling-anvil:1.4.3
docker pull ghcr.io/riseandshaheen/rollups-node:2.0.0-alpha.13
```

The sling image stays named `sling-node`. `rollups-node:2.0.0-alpha.13` packages the official [rollups-node v2.0.0-alpha.13](https://github.com/cartesi/rollups-node/releases/tag/v2.0.0-alpha.13) release. That release publishes `.deb` packages, not a container tag.

The echo machine is a [release tarball](https://github.com/riseandshaheen/prt-dev-stack/releases/tag/echo-c8217d7f), not a dave clone. See [echo/README.md](echo/README.md).

Walkthrough: [docs/quickstart-macos.md](docs/quickstart-macos.md).

## Harness

[`harness/`](harness/README.md) is a break-testing harness for this stack. It takes over mining, drives both nodes through epochs, disputes and faults (races, sentry paths, reorgs, chain resets, RPC faults through a proxy, dave's Lua sybil as the adversary), and checks after every block that chain, sling and the reference node agree. Its scenarios follow [test-design-claude.md](test-design-claude.md) and reproduce claims from [qa-slingnode-v0](https://github.com/Mugen-Builders/qa-slingnode-v0). Write-ups are in [`harness/reports/`](harness/reports/).

