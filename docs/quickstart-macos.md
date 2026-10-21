# Run sling on macOS

## Index

- [Prerequisites](#prerequisites)
- [1. Pull the images](#1-pull-the-images)
- [2. Get the echo machine](#2-get-the-echo-machine)
- [3. Start Anvil](#3-start-anvil)
- [4. Start the sling node](#4-start-the-sling-node)
- [5. The reference node](#5-the-reference-node)
- [A different machine](#a-different-machine)
- [After an input](#after-an-input)
- [Troubleshoot](#troubleshoot)

## Prerequisites

You need:

- Apple Silicon. The published images are `linux/arm64` only.
- [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/).

You do not need Foundry, dave, or an emulator install. Nothing here compiles the emulator or the Rust node.

## 1. Pull the images

```sh
docker pull ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.6
docker pull ghcr.io/riseandshaheen/sling-anvil:1.5.1
```

`sling-node:3.0.0-alpha.6` is the `cartesi-sling-node` binary from dave [v3.0.0-alpha.6](https://github.com/cartesi/dave/releases/tag/v3.0.0-alpha.6) (built from [`image/Dockerfile`](../image/Dockerfile)). It links Cartesi Machine v0.22.0.

`sling-anvil:1.5.1` is Foundry Anvil 1.5.1, the version that wrote the release's devnet dump. `anvil/state.json` is that dump (canonical geometry: two levels, `log2step = [38, 0]`, `height = [54, 38]`) with the echo application deployed on top at block 24.

## 2. Get the echo machine

The stored machine is not in git. From `echo/`:

```sh
curl -L -o echo-machine-image-rootfs-27715ccf.tar.gz \
  https://github.com/riseandshaheen/prt-dev-stack/releases/download/echo-27715ccf/echo-machine-image-rootfs-27715ccf.tar.gz
tar -xzf echo-machine-image-rootfs-27715ccf.tar.gz
```

That writes `echo/machine-image-rootfs/`, stored by Cartesi Machine v0.22.0. The archive is about 121 MB. Compose defaults to `../echo/machine-image-rootfs` from `sling/`. The alpha.5 image (`echo-c8217d7f`) does not load in this node.

## 3. Start Anvil

From `anvil/`:

```sh
docker compose up -d
```

Wait until the container is healthy. It listens on port 8545, chain id `31337`. The dump already contains the echo application. Restarting this container reloads that file and drops every later transaction.

Anvil keeps historical state (`--preserve-historical-states`) and does not mine on its own.

## 4. Start the sling node

From `sling/`:

```sh
docker compose up -d
```

Compose points this image at the echo machine, the application in the dump (`0x53563a918B43Ca40FD966Ea1f065072383f33DB7`), and Anvil account 7, the application's only sentry. The node reads Anvil at `http://host.docker.internal:8545`, chain id `31337`, and keeps its database and snapshots in the `sling-state-alpha6` volume. The key goes in through `CARTESI_SLING_AUTH_PRIVATE_KEY`; alpha.6 has no key flag.

Then mine two blocks (see [Troubleshoot](#troubleshoot)). The first log line names the release:

```
Hello from the sling node (v3.0.0-alpha.6, Cartesi Machine 0.22.0, MARCHID 22)
```

Startup then checks the template against the chain (its hash, `CM_MARCHID` 22 and the pristine uarch hash), pins the data directory to the application and the chain, and says whether the signer is a sentry. A directory from another release exits 40; start a fresh volume.

To start over, from `sling/`:

```sh
docker compose down -v
```

## 5. The reference node

Not for this release. `reference/` runs rollups-node `v2.0.0-alpha.13`, which is built against Dave `v3.0.0-alpha.5` contracts and cannot read this dump's deployment. Leave it stopped until a rollups-node release built against Dave `v3.0.0-alpha.6` exists.

## A different machine

Store the machine with Cartesi Machine v0.22.0 (for example `docker run --rm -v "$PWD":/work -w /work cartesi/machine-emulator:0.22.0 cartesi-machine ... --store=./your-machine`). Then deploy from the Anvil container, passing its template hash. The arguments are template hash, claim staging period in blocks, sentry manager, sentries, withdrawal config, and the CREATE2 salt:

```sh
docker compose -f anvil/compose.yaml exec anvil cast send \
  --rpc-url http://127.0.0.1:8545 \
  --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
  0xF8f6dFA3A3e40C10926839FFd8a221C87C8b2ccD \
  "newDaveApp(bytes32,uint256,address,address[],(address,uint8,uint8,uint64,address),bytes32)" \
  0x1111111111111111111111111111111111111111111111111111111111111111 \
  1000 \
  0x0000000000000000000000000000000000000000 \
  "[0x14dC79964da2C08b23698B3D3cc7Ca32193d9955]" \
  "(0x0000000000000000000000000000000000000000,0,0,0,0x0000000000000000000000000000000000000000)" \
  0x0000000000000000000000000000000000000000000000000000000000000001
```

From `sling/`:

```sh
MACHINE_PATH=../path/to/your-machine \
APP_ADDRESS=0xYourApp \
docker compose up -d
```

`0x1111…1111` stands in for that machine's template hash. The salt is `1`, so the application address is not the echo address. Read `0xYourApp` from the `DaveAppCreated` log (the last log of the receipt: application, then consensus), or ask `calculateDaveAppAddress` with the same arguments first. The sentry stays Anvil account 7 unless you also change `PRIVATE_KEY`. A different application needs its own data volume.

## After an input

Add an input from the Anvil container:

```sh
docker compose -f anvil/compose.yaml exec anvil cast send \
  --rpc-url http://127.0.0.1:8545 \
  --private-key 0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d \
  0xEbE9f4Dfc04ae10bBeE663859c3dc5A23f94eA3C \
  "addInput(address,bytes)" \
  0x53563a918B43Ca40FD966Ea1f065072383f33DB7 \
  0x48656c6c6f2076726f6d204461766521
```

The node only reads finalized blocks. On this Anvil that is two blocks behind the head, so after an input, mine two extra blocks or the node will not see it.

A new epoch opens only after the previous tournament is accepted. This dump deploys the release's canonical clocks, which assume 12 s blocks: an epoch settles no sooner than 700 blocks after it seals, and a leaf join gets 600 blocks to build its commitment. Mine in bulk (`anvil_mine 700`) to move an uncontested epoch along.

## Troubleshoot

Mine two blocks after first boot, not only after an input. The dump starts at block 24 and Anvil does not mine on its own. The node reads finalized blocks, two behind the head:

```sh
docker compose -f anvil/compose.yaml exec anvil cast rpc anvil_mine 2
```

The node's exit status says why it stopped: 0 a requested stop, 1 another error (it retries after restart), 2 a usage error, 30 the flags, template or deployment disagree with the node, 40 the data directory was built by another release (`docker compose down -v` from `sling/`), 101 a panic.
