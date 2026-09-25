Local stack for testing both PRT nodes against one application on one Anvil.

Sling (`cartesi-rollups-prt-node` from dave `v3.0.0-alpha.5`) and the rollups reference node share the Dave v3 devnet in `anvil/state.json`. They do not start a second chain.

```sh
docker pull ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5
docker pull ghcr.io/riseandshaheen/sling-anvil:1.4.3
docker pull ghcr.io/riseandshaheen/rollups-node:v3-bump
```

The sling image stays named `sling-node`. `rollups-node:v3-bump` is an unofficial snapshot of `feature/contracts-bump` at `0baf78f4`, not a Cartesi release.

The echo machine is a [release tarball](https://github.com/riseandshaheen/prt-dev-stack/releases/tag/echo-c8217d7f), not a dave clone. See [echo/README.md](echo/README.md).

Walkthrough: [docs/quickstart-macos.md](docs/quickstart-macos.md).
