# prt-harness/sybil-runner:3.0.0-alpha.6
# dave v3.0.0-alpha.6's Lua PRT client (prt/client-lua) with its e2e support runners, on top of the
# Cartesi Machine 0.22.0 image (Lua 5.4 bindings in /usr/lib/lua/5.4), plus cast (foundry 1.5.1,
# from the stack's anvil image) and jq, which the client shells out to. The alpha.6 release removed
# test/Dockerfile; this image carries no Rust node, no devnet and no test programs.
FROM ghcr.io/riseandshaheen/sling-anvil:1.5.1 AS foundry

FROM cartesi/machine-emulator:0.22.0
USER root
RUN apt-get update && apt-get install -y --no-install-recommends jq ca-certificates procps \
    && rm -rf /var/lib/apt/lists/*
COPY --from=foundry /usr/local/bin/cast /usr/local/bin/cast
# prt/client-lua, test/e2e (support/runners, rollups/dave, setup_path.lua), contract sources and the
# compiled ITournament/Tournament/MultiLevelTournamentFactory ABIs (prt/contracts/out) for reference.
COPY dave /dave
ENV LUA_PATH="/dave/prt/client-lua/?.lua;/dave/test/e2e/support/?.lua;/dave/test/e2e/rollups/?.lua;;"
WORKDIR /dave/test/e2e/rollups
ENTRYPOINT ["lua5.4"]
