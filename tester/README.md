Stored [qa-tester-app](https://github.com/Mugen-Builders/qa-tester-app) machine for dave `v3.0.0-alpha.6`. Template hash `0xf345a724da144e7acb8897b73dcb972f08d7f7baae33922e721e70115b44cc98`.

The tester app is told, input by input, what to do: emit notices, reports, vouchers and delegate-call vouchers with chosen destination, value and payload, emit a raw output, echo, reject, raise an exception, halt or crash, yield with a bogus reason, publish a wrong outputs root, burn cycles. It keeps a libcma ledger of every portal deposit and a real accounts drive the contracts can prove for emergency withdrawals. Its command catalog is `docs/inputs.md` in that repo. It is the application for the funds-safety plan ([test-plan-funds-safety.md](../test-plan-funds-safety.md)); the echo app cannot drive outputs, withdrawals or deposits.

## How this image was built

Source: qa-tester-app `ab223cb3` (2026-10-09). The repo pins emulator 0.21 and Cartesi CLI 2.0.0-alpha.38; no SDK on emulator 0.22 exists, so the drives come from the CLI and the machine is stored by hand:

1. `cartesi build` (CLI 2.0.0-alpha.37 here; its own boot step fails, which does not matter) produces `.cartesi/root.ext2` (alpine + guest tools 0.18.0, 21 MB) and `.cartesi/app.sqfs` (the static riscv64 binary, 1.3 MB). Both are kept here next to the image. Docker's riscv64 emulation is enough.
2. Boot and store with `cartesi/machine-emulator:0.22.0`, mirroring the CLI's init script (two labelled flash drives, the 64 MiB `state` NVRAM on `/dev/uio0` owned by `dapp`, the `[machine.env]` exports, the entrypoint). The kernel is the same `linux-6.5.13-ctsi-2-v0.21.0.bin` the echo image uses:

```sh
docker run --rm -v "$PWD":/work -w /work cartesi/machine-emulator:0.22.0 cartesi-machine \
  --ram-image=./linux.bin --ram-length=128Mi \
  --flash-drive=label:root,data_filename:./root.ext2 \
  --flash-drive=label:app,data_filename:./app.sqfs \
  --nvram=label:state,length:64Mi \
  --append-init='chown dapp:dapp /dev/uio0' \
  --env=QA_TOKEN=0x7a051EDffC0884cd88d4a377F4C87BE074CF6c81 \
  --env=QA_ETHER_PORTAL=0x035b11Be55656c6cfC822D1CaE568C1Af2e497b0 \
  --env=QA_ERC20_PORTAL=0x3332DE61a8BB9aC84893b2f552Fe81C9a6dC5419 \
  --env=QA_ERC721_PORTAL=0x397c352d18DFf47CC8a6143403142cf7afd5Ff7E \
  --env=QA_ERC1155_SINGLE_PORTAL=0x585F56351A66f131E176a345662215C772f80451 \
  --env=QA_ERC1155_BATCH_PORTAL=0xee33550a22e3Cf6Cc265524dC9bcfD99D2307EBe \
  --env=QA_SAFE_ERC20_TRANSFER=0xBB7c5367B74544542F5cE553Ed0DF4B3efAfEEdD \
  --env=QA_LOG=1 \
  --revert-mode=none --final-hash --store=./machine-image \
  -- '/mnt/app/qa-tester /dev/uio0'
```

The addresses are this dump's devnet (rollups-contracts alpha.10; `cartesi address-book` values). `QA_SAFE_ERC20_TRANSFER` is set to the devnet `SafeErc20Transfer`, which the repo's `cartesi.toml` leaves at zero.

3. Check the accounts-drive index: the NVRAM is stored at `0x8c000000`, so `accounts_drive_start_index = 0x8c000000 >> 22 = 560`, as in the repo's `cartesi.toml`. Re-check after any change to the drive sizes.

The hash differs from the repo's `0x8e4f27b6…` because Cartesi Machine v0.22 changes every machine hash (archive version 8 here, 7 there) and because the init script differs in quoting.

## Deploying it

Unlike echo, the tester app carries a real `WithdrawalConfig`: guardian Anvil account 0, 2^17 accounts of one 32-byte leaf, drive index 560, `TestUsdWithdrawalOutputBuilder` `0xB4D253c7a110241561B3eD6d632846dF7d4e9Af7` (pays out `TestUsdc` `0x7a051EDffC0884cd88d4a377F4C87BE074CF6c81`). From the repository root, against the running Anvil:

```sh
cast send --rpc-url http://127.0.0.1:8545 \
  --private-key 0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80 \
  0xF8f6dFA3A3e40C10926839FFd8a221C87C8b2ccD \
  "newDaveApp(bytes32,uint256,address,address[],(address,uint8,uint8,uint64,address),bytes32)" \
  0xf345a724da144e7acb8897b73dcb972f08d7f7baae33922e721e70115b44cc98 \
  1000 \
  0x0000000000000000000000000000000000000000 \
  "[0x14dC79964da2C08b23698B3D3cc7Ca32193d9955]" \
  "(0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266,0,17,560,0xB4D253c7a110241561B3eD6d632846dF7d4e9Af7)" \
  0x0000000000000000000000000000000000000000000000000000000000000009
```

Then from `sling/`, with its own data volume:

```sh
MACHINE_PATH=../tester/machine-image APP_ADDRESS=0x<app from DaveAppCreated> docker compose -p prt-tester up -d
```

Inputs are ABI calldata (`cast calldata 'emitNotice(bytes)' 0x01`, see `docs/inputs.md` upstream) sent through `InputBox.addInput`; deposits go through the portals. The stored machine is published as release [`tester-f345a724`](https://github.com/riseandshaheen/prt-dev-stack/releases/tag/tester-f345a724) (`qa-tester-machine-image-f345a724.tar.gz`, sha256 in the release notes). Download and unpack it here:

```sh
curl -L -o qa-tester-machine-image-f345a724.tar.gz \
  https://github.com/riseandshaheen/prt-dev-stack/releases/download/tester-f345a724/qa-tester-machine-image-f345a724.tar.gz
tar -xzf qa-tester-machine-image-f345a724.tar.gz && chmod -R u+w machine-image   # the node refuses an image whose files lack the owner write bit
```

## Verified on 2026-10-22

A sling node (account 8, not a sentry) seeded from this image, received `emitNotice`, `emitVouchers` and an `EtherPortal.depositEther` input, won and settled epoch 0, and after epoch 1 sealed executed all three inputs (`processing input 1:0` to `1:2`) and rolled to epoch 2 with no WARN or ERROR. Two things to know when driving it: with the node's default `--snapshot-gap-inputs 64`, open-epoch inputs are executed only when the epoch seals, so add `CARTESI_SLING_SNAPSHOT_GAP_INPUTS=1` to the compose environment to see each input processed as it lands; and after any transaction that must be visible to the node, mine two more blocks (the node reads `finalized`, two behind the head).

## Sepolia build for the platform team

A separate build of the same app, configured for Ethereum Sepolia (the previous cycle's test token, the team's guardian, the canonical `SafeErc20Transfer`, and the USD builder the alpha.10 factory computes for that token), is released from the temporary fork [riseandshaheen/qa-tester-app](https://github.com/riseandshaheen/qa-tester-app) as `v0.2.0-alpha.1`, template `0xa1ed40bfdfc6d80e65e0ec5ecc0006107833db0b370bec4c92b2f05a76806686`. The image here (`0xf345a724…`) keeps the devnet addresses and is the one baked into `anvil/state.json`.
