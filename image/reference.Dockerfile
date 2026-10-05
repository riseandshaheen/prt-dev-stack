# Runtime image for the rollups reference node.
# Installs the official v2.0.0-alpha.13 packages. It does not compile the node.
FROM debian:trixie-20250811

ARG ROLLUP_NODE_VERSION=2.0.0-alpha.13
ARG EMULATOR_VERSION=0.21.0
ARG TARGETARCH
ARG NODE_RUNTIME_DIR=/var/lib/cartesi-rollups-node

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu \
 && case "$TARGETARCH" in \
      amd64) \
        EMU_SHA=5f13034f43454c340062c677146daabe77c587cee1bd60342c95b8ad8f1463a3; \
        NODE_SHA=5c75b12082bded69e589781715c74ce7ddf433a7ed2fa6f831e92315fa6dc003 ;; \
      arm64) \
        EMU_SHA=866f0bde2db53b9b8e6a6eac85e5ad4116338379fa25cdfcc5e4bf835f948bb1; \
        NODE_SHA=ab4007baa7ea37ed58957fa94478c5f6e6999ca5234520bbfecb82e9c9c9bab3 ;; \
      *) echo "unsupported architecture: $TARGETARCH"; exit 1 ;; \
    esac \
 && curl -fsSL -o /tmp/cartesi-machine-emulator.deb \
      "https://github.com/cartesi/machine-emulator/releases/download/v${EMULATOR_VERSION}/machine-emulator_${TARGETARCH}.deb" \
 && echo "${EMU_SHA}  /tmp/cartesi-machine-emulator.deb" | sha256sum --check \
 && curl -fsSL -o /tmp/cartesi-rollups-node.deb \
      "https://github.com/cartesi/rollups-node/releases/download/v${ROLLUP_NODE_VERSION}/cartesi-rollups-node-v${ROLLUP_NODE_VERSION}_${TARGETARCH}.deb" \
 && echo "${NODE_SHA}  /tmp/cartesi-rollups-node.deb" | sha256sum --check

RUN set -eu \
 && groupadd --system --gid 102 cartesi \
 && useradd --system --uid 102 --gid cartesi --shell /usr/sbin/nologin --no-create-home cartesi \
 && passwd --lock cartesi \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      procps \
      /tmp/cartesi-machine-emulator.deb \
      /tmp/cartesi-rollups-node.deb \
 && rm -rf /var/lib/apt/lists/* /tmp/cartesi-*.deb \
 && mkdir -p ${NODE_RUNTIME_DIR}/snapshots ${NODE_RUNTIME_DIR}/data ${NODE_RUNTIME_DIR}/logs \
 && chown -R cartesi:cartesi ${NODE_RUNTIME_DIR}

USER cartesi

WORKDIR /var/lib/cartesi-rollups-node

HEALTHCHECK --interval=1s --timeout=1s --retries=5 \
    CMD curl -G -f -H 'Content-Type: application/json' http://127.0.0.1:10000/readyz

CMD ["cartesi-rollups-node"]
