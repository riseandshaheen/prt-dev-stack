Stored echo Cartesi machine. Template hash `0xc8217d7fa39a7a4ba65e5efacb1cfca9996dd76ceea945299fea9f4f2786f3b2`.

The image is not in git. Download the release tarball:

```sh
curl -L -o echo-machine-image-rootfs-c8217d7f.tar.gz \
  https://github.com/riseandshaheen/prt-dev-stack/releases/download/echo-c8217d7f/echo-machine-image-rootfs-c8217d7f.tar.gz
tar -xzf echo-machine-image-rootfs-c8217d7f.tar.gz
```

That writes `machine-image-rootfs/` here. It embeds the Cartesi kernel v0.21.0 and guest tools rootfs v0.18.0.
