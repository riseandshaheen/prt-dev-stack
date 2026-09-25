Local stack for testing both PRT nodes against one application on one Anvil.

Sling (`cartesi-rollups-prt-node` from dave `v3.0.0-alpha.5`) and the rollups reference node share the Dave v3 devnet in `anvil/state.json`. They do not start a second chain.

```sh
docker pull ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5
```

The sling image stays named `sling-node`. Build it from this repo with `docker build -f image/Dockerfile`. Build Anvil with `docker build -t sling-anvil:1.4.3 anvil`.

Walkthrough: [docs/quickstart-macos.md](docs/quickstart-macos.md).
