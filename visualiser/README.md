# PRT Dev Stack visualiser

One read-only view of the chain and every node on it. It answers two questions on every screen: do the nodes agree with each other and with the chain, and what is the dispute doing right now.

It reads three kinds of source and never writes to any of them:

| Source | How | What it contributes |
| --- | --- | --- |
| Chain (Anvil) | JSON-RPC | Epochs, inputs, tournaments at every level, live clocks and bisection height, every transaction to the app's contracts with revert reasons |
| Sling nodes | Their SQLite state, opened read-only | Epoch boundaries, computed final state and commitment, stored inputs, dispute progress |
| Reference nodes | Their JSON-RPC API | Epoch and input status, machine hash and commitment, outputs and reports, app state and failure reason |

## Works with

Tested with these versions. Other versions may work; when one differs, the overview says so instead of failing silently.

| Part | Tested | What it depends on |
| --- | --- | --- |
| Chain | Anvil 1.4.3; any EVM JSON-RPC | Standard `eth_*` calls only. `finalized` is optional (falls back to `latest`). Revert reasons need historical state (an archive node or Anvil); without it they show as undecoded. |
| Contracts | Dave v3 (`v3.0.0-alpha.5`), rollups-contracts `v3.0.0-alpha.10` | Event and function ABIs in `vis/abi.json`. Other contract versions: regenerate it (see "When contracts change"). |
| Sling | dave `v3.0.0-alpha.5` (`node_version 2.0.0`) | Its SQLite file on the same machine: a path, or a local container read with `docker cp`. A different schema shows the node as down with "schema is not the one this visualiser reads". |
| Reference node | rollups-node `v2.0.0-alpha.13` | Its JSON-RPC (`cartesi_*` methods), local or remote. A missing method shows as "reference node has no cartesi_…". |
| History | A few tens of thousands of blocks | It indexes every block from each app's deployment into memory, and again after each restart. Long or public chains will be slow. |

## Use it with your own stack

1. Copy `visualiser.example.json` to `visualiser.json` and keep only what you run:
   - `chain.rpc`: your Anvil or other RPC.
   - `chain.dave_app_factory`: your Dave app factory, to list every app it deployed (optional).
   - one entry per node. A sling needs `db` (path to its `db.sqlite3`) or `container` (its container name, on this machine). A reference node needs `rpc`.
   - `accounts` on each node: its signer addresses, so its transactions are named in the ledger.
   - `app_names` and `labels`: names for apps and other addresses (an input sender, a sybil).
   - `optional: true` on any node you sometimes stop.
2. `python3 server.py visualiser.json` and open `http://127.0.0.1:8787`.
3. Check the top bar: every node should be green. If one is not, its pill and the Nodes table say why:

| You see | It means |
| --- | --- |
| `Sling down: … does not exist` / `docker cp … failed` | Wrong `db` path or container name, or the container is on another machine. |
| `schema is not the one this visualiser reads` | Your sling is a different dave version. |
| `reference node has no cartesi_…` / `cannot reach …` | Different rollups-node version, or wrong `rpc` URL. |
| `runs version X; this visualiser is tested with Y` | It runs, but some panels may be empty or wrong. |
| `No node follows this app` | An app the factory or `apps` list knows about that none of your nodes is configured for. |
| Ledger shows `reverted` with no reason | Your RPC has no historical state for that block. |

## Run it

As a service in the stack. Start Anvil, sling and the reference node first, then:

```sh
docker compose -f visualiser/compose.yaml up -d --build
```

Open `http://<this machine>:8787`. Other testers on the network can use the same address. The container reads sling's `prt-sling_sling-state` volume directly and reaches the chain and reference node through `host.docker.internal`, as the other services do.

On the host, with Python 3.10 or later and nothing to install:

```sh
python3 visualiser/server.py                              # same defaults and env vars as the old script
python3 visualiser/server.py visualiser/visualiser.json   # or with a config file
```

On macOS, Docker volumes are not visible from the host, so a sling node configured with `container` is read through `docker cp`. That copy is not atomic, so reads that fail an integrity check are retried. The compose service reads the volume directly and does not have this issue.

## Configure

Copy `visualiser.example.json`. Every field is optional except `nodes`.

- `chain.rpc` is the Anvil URL. `chain.dave_app_factory` makes every app the factory deployed show up, including ones no node follows yet.
- `nodes` lists each node once. A sling node needs `db` (path to `db.sqlite3`) or `container`; a reference node needs `rpc`. Add as many of each as you run. Sling follows one app per process, so a second app means a second sling entry.
- `accounts` on a node are its signer addresses. They label its transactions in the ledger.
- `optional: true` on a node marks it as expected to be off at times: it shows as "off" in grey instead of raising a "down" alert.
- `app_names` maps application addresses to names, used when no node reports one (the name otherwise comes from the reference node, and the last one seen is kept).
- `apps` adds application addresses to watch that no node or factory reports.
- `labels` names any other address, such as a sybil runner's or test bot's account. All ten default Anvil accounts are named already. Edits to `labels` are picked up on the next poll, without a restart.
- `ledger_limit` caps the transactions kept per application (default 20000). The oldest are dropped first.

Transactions from bots and scripts show up whoever sends them. A transaction sent straight to an app's contracts is recorded even if it reverts. One sent through another contract, such as a multicall, is recorded when it emits an event from the app's contracts and is marked "via contract call"; a reverted one of those leaves no event and is not seen.

Environment variables override the file: `VIS_CONFIG`, `VIS_HOST`, `VIS_PORT`, `VIS_CHAIN_RPC`.

## What you see

- **Overview.** Alerts first, then every app as an epoch ribbon: one lane for the chain and one per node. A column outlined in red with a notch is an epoch where the chain and nodes disagree. Below that, each node's health and how far it is behind finalized.
- **App.** Contract facts, how each node sees the app, the epoch table with every node's status side by side, and the full transaction ledger.
- **Epoch.** Four tabs:
  - Dispute: the tournament tree (root, matches, inner tournaments), a timeline on the block axis, and each commitment's clock.
  - Inputs: each input with its sender and payload, and whether each node stored or processed it.
  - Agreement: input range, final state and commitment from the chain and every node.
  - Transactions: every call made for this epoch, including reverts and their reasons.

A commitment is called honest when its root or final state equals what a node computed. Who sent the join does not matter: if the reference node joins first and sling defends that commitment, it is still honest.

Live updates arrive over server-sent events. The page does not redraw while you are selecting text, so hashes can be selected and copied. Clicking any hash copies it in full.

## API

All JSON, all read-only. Served from the collector's last snapshot, so the number of open tabs adds no load on the nodes or the chain.

| Route | Returns |
| --- | --- |
| `GET /api/v1/overview` | Chain, nodes, apps with recent epochs, alerts |
| `GET /api/v1/apps/{address}` | App contracts and every epoch |
| `GET /api/v1/apps/{address}/epochs/{n}` | Inputs, agreement checks, dispute tree, transactions |
| `GET /api/v1/apps/{address}/transactions` | Every transaction to the app's contracts |
| `GET /api/v1/stream` | Server-sent events, `{"version": n}` on each change |
| `GET /healthz` | 200 when the collector and chain are current, 503 otherwise |

The old `/api/status` and `/api/reference` answer 410 with a pointer to the new routes.

## When contracts change

Event topics, function selectors and error names come from `vis/abi.json`, generated from the rollups-node Go bindings:

```sh
python3 visualiser/tools/gen_abi.py /path/to/rollups-node
```

The server refuses to start if an event it depends on is missing from that file.

## Tests

The tests boot a real Anvil from the stack's `state.json` and drive real contract activity: inputs, a two-sybil dispute, a duplicate join that reverts, and a timeout win. Sling is simulated with a database built from dave's own `schema.sql`, and the reference node with a small JSON-RPC server.

```sh
export DAVE_DIR=/path/to/dave          # checkout at v3.0.0-alpha.5, for sling's schema
python3 visualiser/tests/stack.py      # API checks: agreement, honest classification, reverts, node down, rollback
python3 visualiser/tests/ended.py      # timeout win: ended match, eliminated commitment, rival-winner alert
python3 visualiser/tests/shots.py out/ # screenshots of every view; fails loudly on console errors or injected script
```

`shots.py` and `ended.py` need `pip install playwright && playwright install chromium`.

## Security

The server binds to `127.0.0.1` unless configured otherwise, and the compose service binds to all interfaces so other testers can reach it. There is no authentication: run it only on networks you trust. Pages are built with `textContent` only and served under a strict content security policy, so input payloads and app names are always shown as text.
