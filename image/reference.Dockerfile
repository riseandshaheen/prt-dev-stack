# Runtime image for the rollups reference node.
# Installs the official v2.0.0-alpha.14 packages. It does not compile the node.
FROM debian:trixie-20250811

ARG ROLLUP_NODE_VERSION=2.0.0-alpha.14
ARG EMULATOR_VERSION=0.22.0
ARG TARGETARCH
ARG NODE_RUNTIME_DIR=/var/lib/cartesi-rollups-node

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu \
 && case "$TARGETARCH" in \
      amd64) \
        EMU_SHA=906ae4cd0524ef254ea9a83712e6ad353c36123acae21628fb93c1c47955429a; \
        NODE_SHA=ba4d1f5780adc75c46f9f9e4fe53f19e62543a5754104172f0f6aa80a0063fc6 ;; \
      arm64) \
        EMU_SHA=212710b5ad74e469368cd5f54b006eebde1e8620833acca5be9a845cf74ab2cc; \
        NODE_SHA=d37c805f61b3149a418a933c656492775985c8e4a11c08e5a8dd9185900ac5a6 ;; \
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
