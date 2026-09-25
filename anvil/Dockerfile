# Foundry Anvil 1.4.3. Newer Anvil will not load state.json.
FROM debian:trixie-slim

ARG TARGETARCH
ARG FOUNDRY_VERSION=1.4.3

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && curl -fsSL "https://github.com/foundry-rs/foundry/releases/download/v${FOUNDRY_VERSION}/foundry_v${FOUNDRY_VERSION}_linux_${TARGETARCH}.tar.gz" \
      -o /tmp/foundry.tar.gz \
 && tar -xzf /tmp/foundry.tar.gz -C /usr/local/bin anvil cast \
 && rm /tmp/foundry.tar.gz \
 && chmod 755 /usr/local/bin/anvil /usr/local/bin/cast \
 && apt-get purge -y curl \
 && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["anvil"]
