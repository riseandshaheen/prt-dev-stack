# Runtime image for the published sling node. It does not compile Dave or the emulator.
# Pinned to dave v3.0.0-alpha.6, whose node is cartesi-sling-node (Cartesi Machine v0.22).
FROM debian:trixie-slim

ARG DAVE_VERSION=3.0.0-alpha.6
ARG TARGETARCH

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl libgomp1 \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/cartesi/dave/releases/download/v${DAVE_VERSION}/cartesi-sling-node-${DAVE_VERSION}-${TARGETARCH}.tar.gz" \
      -o /tmp/node.tar.gz \
 && tar -xzf /tmp/node.tar.gz -C /usr/local/bin \
 && rm /tmp/node.tar.gz \
 && chmod 755 /usr/local/bin/cartesi-sling-node

RUN mkdir -p /opt/dave/devnet \
 && curl -fsSL "https://github.com/cartesi/dave/releases/download/v${DAVE_VERSION}/cartesi-rollups-prt-${DAVE_VERSION}-anvil-1.5.1.tar.gz" \
      -o /tmp/devnet.tar.gz \
 && tar -xzf /tmp/devnet.tar.gz -C /opt/dave/devnet \
 && rm /tmp/devnet.tar.gz

ENTRYPOINT ["cartesi-sling-node"]
