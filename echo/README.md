Stored echo Cartesi machine for dave `v3.0.0-alpha.6`. Template hash `0x27715ccf0c3dffcd6395cf52ad818daa74a5f30e8ef652cd3f0ab884f701382f`.

Stored by Cartesi Machine v0.22.0 (`cartesi/machine-emulator:0.22.0`, archive version 8), which the alpha.6 node requires: it refuses v0.21 images. The kernel is image-kernel v0.21.0 (`linux-6.5.13-ctsi-2-v0.21.0.bin`) and the rootfs is machine-emulator-tools v0.18.0 (`rootfs-tools.ext2`), the same pins as dave's `test/programs`. Built with dave's `build-echo.sh` command:

```sh
cartesi-machine --ram-image=./linux.bin --final-hash \
  --flash-drive=label:root,data_filename:./rootfs.ext2 \
  --revert-mode=none --store=./machine-image-rootfs \
  -- "ioctl-echo-loop --vouchers=1 --notices=1 --reports=1 --verbose=1 --reject=2"
```

The image is not in git. Download the release tarball (about 121 MB) and unpack it here:

```sh
curl -L -o echo-machine-image-rootfs-27715ccf.tar.gz \
  https://github.com/riseandshaheen/prt-dev-stack/releases/download/echo-27715ccf/echo-machine-image-rootfs-27715ccf.tar.gz
tar -xzf echo-machine-image-rootfs-27715ccf.tar.gz
```

That writes `machine-image-rootfs/`. The node refuses an image whose files lack the owner write bit; after unpacking as another user, `chmod -R u+w machine-image-rootfs`.

The alpha.5 image (template `0xc8217d7f…`, v0.21) is the `echo-c8217d7f` release.
