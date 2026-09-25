# Run sling and the rollups reference node on macOS

## Index

- [Purpose](#purpose)
- [Prerequisites](#prerequisites)
- [1. Pull the images](#1-pull-the-images)
- [2. Get the echo machine](#2-get-the-echo-machine)
- [3. Start Anvil](#3-start-anvil)
- [4. Start the sling node](#4-start-the-sling-node)
- [5. Start the reference node](#5-start-the-reference-node)
- [6. Register the same application](#6-register-the-same-application)
- [A different machine](#a-different-machine)
- [After an input](#after-an-input)

## Purpose

This checkout is for running both nodes against one application on one Anvil.

The chain is the Dave v3 devnet in `anvil/state.json`, with the echo application already deployed. Sling (`cartesi-rollups-prt-node` from dave `v3.0.0-alpha.5`) and the rollups reference node both attach to that Anvil. They do not start a second chain.

Sling recomputes the machine and can play a tournament. The reference node reads inputs, runs the machine, serves JSON-RPC, and can join, stage, and accept. It does not play bisection moves.

Split the keys. Sling uses Anvil account 7. The reference node sends claims from account 0 and PRT transactions from account 6. Send inputs from another account, such as account 1. Sharing any of those keys collides nonces.

The echo application is `0x6c2E2F9665b8f941aA8D94ea3f0287F7884a6146`. Its template hash is `0xc8217d7fa39a7a4ba65e5efacb1cfca9996dd76ceea945299fea9f4f2786f3b2`. The sling signer `0x14dC79964da2C08b23698B3D3cc7Ca32193d9955` is the only sentry.

## Prerequisites

You need:

- Apple Silicon. The published images are `linux/arm64` only.
- [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/).

Leave ports `8545`, `5433`, `10000`, `10011`, and `10012` free.

You do not need Foundry, dave, or a rollups-node checkout. Nothing here compiles the emulator or the Rust node.

## 1. Pull the images

```sh
docker pull ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5
docker pull ghcr.io/riseandshaheen/sling-anvil:1.4.3
docker pull ghcr.io/riseandshaheen/rollups-node:test-contracts-bump
```

`sling-anvil:1.4.3` is Foundry Anvil 1.4.3. `state.json` was written by that version. A newer Anvil will not load it.

`rollups-node:test-contracts-bump` is not an official Cartesi release. It is a snapshot of [cartesi/rollups-node](https://github.com/cartesi/rollups-node) `feature/contracts-bump` ([PR 798](https://github.com/cartesi/rollups-node/pull/798)), generated from rollups-contracts `v3.0.0-alpha.10` and Dave contracts `v3.0.0-alpha.4`. Dave `v3.0.0-alpha.5` did not change those contracts.

## 2. Get the echo machine

The stored machine is not in git. From `echo/`:

```sh
curl -L -o echo-machine-image-rootfs-c8217d7f.tar.gz \
  https://github.com/riseandshaheen/prt-dev-stack/releases/download/echo-c8217d7f/echo-machine-image-rootfs-c8217d7f.tar.gz
tar -xzf echo-machine-image-rootfs-c8217d7f.tar.gz
```

That writes `echo/machine-image-rootfs/`. The archive is about 121 MB. Compose defaults to `../echo/machine-image-rootfs` from `sling/` and `reference/`.

## 3. Start Anvil

From `anvil/`:

```sh
docker compose up -d
```

Wait until the container is healthy. It listens on port 8545, chain id `31337`. The dump already contains the echo application. Restarting this container reloads that file and drops every later transaction.

Anvil keeps historical state (`--preserve-historical-states`). The reference node needs that in order to find the application.

## 4. Start the sling node

From `sling/`:

```sh
docker compose up -d
```

Compose points this image at the echo machine, the application in the dump, and Anvil account 7. The node talks to Anvil at `http://host.docker.internal:8545` and stores its database in the `sling-state` volume.

## 5. Start the reference node

From `reference/`. Compose starts Postgres on host port 5433 and the node on `10011`. It reads Anvil at `http://host.docker.internal:8545`. Do not start the `ethereum_provider` / `devnet` service that ships with rollups-node.

```sh
docker compose up -d
```

Claims use Anvil account 0. PRT transactions use account 6.

## 6. Register the same application

The application lives on the chain. `app register` only writes it into this node's Postgres.

```sh
docker compose exec node cartesi-rollups-cli app register \
  --address 0x6c2E2F9665b8f941aA8D94ea3f0287F7884a6146 \
  --template-path /machine \
  --prt \
  --name echo \
  --print-json
```

For a different application, pass that address and mount its machine at `/machine`.

JSON-RPC is [http://127.0.0.1:10011](http://127.0.0.1:10011). Inspect is on port `10012`.

## A different machine

Deploy from the Anvil container. Change the template hash and pass that image and the new application address:

```sh
docker compose -f anvil/compose.yaml exec anvil cast send \
  --rpc-url http://127.0.0.1:8545 \
  --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
  0xd34BEC37Fa5816ABA2f87BdaD2E13dd1B161370f \
  "newDaveApp(bytes32,uint256,address,address[],(address,uint8,uint8,uint64,address),bytes32)" \
  0x1111111111111111111111111111111111111111111111111111111111111111 \
  1000 \
  0x0000000000000000000000000000000000000000 \
  "[0x14dC79964da2C08b23698B3D3cc7Ca32193d9955]" \
  "(0x0000000000000000000000000000000000000000,0,0,0,0x0000000000000000000000000000000000000000)" \
  0x0000000000000000000000000000000000000001
```

From `sling/`:

```sh
MACHINE_PATH=../path/to/your-machine \
APP_ADDRESS=0xYourApp \
docker compose up -d
```

`0x1111…1111` stands in for that machine's template hash. The salt is `1`, so the application address is not the echo address. Read `0xYourApp` from the `DaveAppCreated` log. The sentry stays Anvil account 7 unless you also change `PRIVATE_KEY`.

## After an input

Both nodes only read finalized blocks. On this Anvil that is two blocks behind the head, so after an input, mine two extra blocks or neither node will see it.

A new epoch opens only after the previous tournament is accepted. An uncontested join still has to wait out the tournament clock (about 300 blocks here) before the result can be staged.
