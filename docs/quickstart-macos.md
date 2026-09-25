# Run sling and the rollups reference node on macOS

## Index

- [Purpose](#purpose)
- [Prerequisites](#prerequisites)
- [Sling node](#sling-node)
  - [1. Get the node image](#1-get-the-node-image)
  - [2. Build the Anvil image](#2-build-the-anvil-image)
  - [3. Start Anvil](#3-start-anvil)
  - [4. Build the echo machine](#4-build-the-echo-machine)
  - [5. Deploy the echo application](#5-deploy-the-echo-application)
  - [6. Start the sling node](#6-start-the-sling-node)
  - [A different machine](#a-different-machine)
- [Reference node](#reference-node)
  - [1. Clone and build the image](#1-clone-and-build-the-image)
  - [2. Start the node](#2-start-the-node)
  - [3. Register the same application](#3-register-the-same-application)
- [After an input](#after-an-input)

## Purpose

This checkout is for running both nodes against one application on one Anvil.

The chain is the Dave v3 devnet in `anvil/state.json`. One echo application is deployed on it. Sling (`cartesi-rollups-prt-node` from dave `v3.0.0-alpha.5`) and the rollups reference node both attach to that Anvil. They do not start a second chain.

Sling recomputes the machine and can play a tournament. The reference node reads inputs, runs the machine, serves JSON-RPC, and can join, stage, and accept. It does not play bisection moves.

Split the keys. Sling uses Anvil account 7. The reference node sends claims from account 0 and PRT transactions from account 6. Send inputs from another account, such as account 1. Sharing any of those keys collides nonces.

## Prerequisites

You need:

- Apple Silicon. The published sling image is `linux/arm64` only.
- [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/).
- `cast` on the host. Install Foundry, then `foundryup`:

  ```sh
  curl -L https://foundry.paradigm.xyz | bash
  foundryup
  ```

  Host Foundry can be newer than 1.4.3. Only the Anvil container must be 1.4.3.

Leave ports `8545`, `5433`, `10000`, `10011`, and `10012` free.

Clone this repo next to a [dave](https://github.com/cartesi/dave) checkout at `v3.0.0-alpha.5`. Compose defaults to `../../dave/test/programs/echo/machine-image-rootfs` from `sling/` and `reference/`. That directory is not in the dave git tree. Build it in [step 4](#4-build-the-echo-machine). You do not need dave submodules, `just`, or a compiled emulator.

Nothing here compiles the emulator or the Rust node.

## Sling node

Use Docker. Pull the published node image for dave `v3.0.0-alpha.5`.

### 1. Get the node image

```sh
docker pull ghcr.io/riseandshaheen/sling-node:3.0.0-alpha.5
```

### 2. Build the Anvil image

There is no published Anvil image that loads this dump. From `anvil/`:

```sh
docker build -t sling-anvil:1.4.3 .
```

That Dockerfile installs Foundry Anvil 1.4.3. `state.json` was written by that version. A newer Anvil will not load it.

If GitHub throttles the Foundry archive during the build, download `foundry_v1.4.3_linux_arm64.tar.gz` from the [v1.4.3 release](https://github.com/foundry-rs/foundry/releases/tag/v1.4.3) into `anvil/`, then build from a context that copies that file instead of curling it.

### 3. Start Anvil

From `anvil/`:

```sh
docker compose up -d
```

The dump is the devnet before the echo application exists. Restarting this container reloads that file and drops every later transaction, including the application from the next deploy. Deploy again after a restart.

Anvil keeps historical state (`--preserve-historical-states`). The reference node needs that in order to find the application.

Wait until the container is healthy. It listens on port 8545, chain id `31337`.

### 4. Build the echo machine

Cloning dave does not give you the machine. `just build-echo` writes `test/programs/echo/machine-image`. This stack mounts `machine-image-rootfs`. Build that directory with the published Cartesi Machine `0.21.0` package. Do not use Homebrew `cartesi-machine` 0.20.

From the parent of this repo:

```sh
git clone --branch v3.0.0-alpha.5 --depth 1 https://github.com/cartesi/dave.git
```

Download the pinned kernel and guest rootfs into `dave/test/programs/`:

```sh
cd dave/test/programs
curl -L -o linux.bin \
  https://github.com/cartesi/image-kernel/releases/download/v0.21.0/linux-6.5.13-ctsi-2-v0.21.0.bin
curl -L -o rootfs.ext2 \
  https://github.com/cartesi/machine-emulator-tools/releases/download/v0.18.0/rootfs-tools.ext2
```

`rootfs.ext2` is about 350 MB.

Build and store the echo image with the Linux emulator package. From the same `dave/test/programs/` directory:

```sh
curl -L -o /tmp/machine-emulator.deb \
  https://github.com/cartesi/machine-emulator/releases/download/v0.21.0/machine-emulator_arm64.deb

docker run --rm \
  -v "$PWD":/work \
  -v /tmp/machine-emulator.deb:/tmp/machine-emulator.deb:ro \
  debian:trixie-slim \
  bash -lc 'apt-get update && apt-get install -y /tmp/machine-emulator.deb &&
    cd /work &&
    rm -rf echo/machine-image-rootfs &&
    cartesi-machine --ram-image=./linux.bin --final-hash \
      --flash-drive=label:root,data_filename:./rootfs.ext2 \
      --revert-mode=none --store=./echo/machine-image-rootfs -- \
      "ioctl-echo-loop --vouchers=1 --notices=1 --reports=1 --verbose=1 --reject=2"'
```

The stored hash must be `0xc8217d7fa39a7a4ba65e5efacb1cfca9996dd76ceea945299fea9f4f2786f3b2`. If it is not, stop. The deploy below is locked to that hash.

### 5. Deploy the echo application

Deploy with Anvil account 0. The sling signer is account 7 (`0x14dC79964da2C08b23698B3D3cc7Ca32193d9955`), and that account is the only sentry. The salt is zero, so the application address is `0x6c2E2F9665b8f941aA8D94ea3f0287F7884a6146`.

```sh
cast send --rpc-url http://127.0.0.1:8545 \
  --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
  0xd34BEC37Fa5816ABA2f87BdaD2E13dd1B161370f \
  "newDaveApp(bytes32,uint256,address,address[],(address,uint8,uint8,uint64,address),bytes32)" \
  0xc8217d7fa39a7a4ba65e5efacb1cfca9996dd76ceea945299fea9f4f2786f3b2 \
  1000 \
  0x0000000000000000000000000000000000000000 \
  "[0x14dC79964da2C08b23698B3D3cc7Ca32193d9955]" \
  "(0x0000000000000000000000000000000000000000,0,0,0,0x0000000000000000000000000000000000000000)" \
  0x0000000000000000000000000000000000000000000000000000000000000000
```

Send later inputs from a different account than 0, 6, or 7. Account 0 is the reference-node claimer, account 6 is its PRT signer, and account 7 belongs to sling.

### 6. Start the sling node

From `sling/`:

```sh
docker compose up -d
```

Compose already points this image at the echo machine, the application above, and Anvil account 7. The node talks to Anvil at `http://host.docker.internal:8545` and stores its database in the `sling-state` volume.

### A different machine

Change the template hash in the deploy and pass that image and the new application address:

```sh
cast send --rpc-url http://127.0.0.1:8545 \
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

## Reference node

This is not an official rollups-node release. There is no published image that speaks Dave `v3` and rollups-contracts `v3.0.0-alpha.10`. The steps below clone [cartesi/rollups-node](https://github.com/cartesi/rollups-node) at `feature/contracts-bump` ([PR 798](https://github.com/cartesi/rollups-node/pull/798)) and build it. That branch is generated from rollups-contracts `v3.0.0-alpha.10` and Dave contracts `v3.0.0-alpha.4`. Dave `v3.0.0-alpha.5` did not change those contracts, so this image can share the Anvil already running above.

Do not start the `ethereum_provider` / `devnet` service that ships with that repository. Point this node at the Anvil on port 8545.

The Docker build needs several gigabytes free. It includes machine emulator `0.21.0`. Mount the same echo image sling uses.

### 1. Clone and build the image

From the parent of this repo (next to `dave` and `prt-dev-stack`):

```sh
git clone --branch feature/contracts-bump https://github.com/cartesi/rollups-node.git
cd rollups-node
docker build --target rollups-node -t cartesi/rollups-node:v3-bump .
```

`next/2.0` is the wrong branch. It still pins older contracts.

### 2. Start the node

From `reference/`. Compose starts Postgres on host port 5433 and the node on `10011`. It reads Anvil at `http://host.docker.internal:8545`.

```sh
docker compose up -d
```

Claims use Anvil account 0. PRT transactions use account 6.

### 3. Register the same application

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

## After an input

Both nodes only read finalized blocks. On this Anvil that is two blocks behind the head, so after an input, mine two extra blocks or neither node will see it.

A new epoch opens only after the previous tournament is accepted. An uncontested join still has to wait out the tournament clock (about 300 blocks here) before the result can be staged.
