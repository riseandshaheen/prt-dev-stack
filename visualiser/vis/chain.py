"""Chain truth: an incremental index of every contract a PRT app touches.

Everything here is read from the chain, never inferred. Per poll it
  1. checks the head and whether the indexed history is still canonical,
  2. indexes new logs from consensus contracts, the input box and every
     tournament (root and inner, discovered as they are created),
  3. records every transaction sent to those contracts, with revert reasons,
  4. refreshes live views (standing, clocks, bisection height) of open tournaments.
"""

from __future__ import annotations

import time
from collections import deque

from . import abi
from .eth import Eth, RpcError

STANDINGS = ["matches active", "awaiting closure", "root winner", "root failed",
             "inner winner", "inner eliminable, no winner", "inner eliminable, winner expired"]
BOND = ["tournament running", "no winner", "recoverable", "recovered"]
PHASES = ["uninitialized", "bisecting", "ready to seal", "sealed"]
DELETE_REASONS = ["step", "timeout", "child tournament"]
WINNERS = ["none", "one", "two"]
ZERO_ADDRESS = "0x" + "0" * 40

S_APP = {
    "consensus": ("getOutputsMerkleRootValidator()", ["address"]),
    "template_hash": ("getTemplateHash()", ["bytes32"]),
    "deploy_block": ("getDeploymentBlockNumber()", ["uint256"]),
}
S_CONSENSUS = {
    "input_box": ("getInputBox()", ["address"]),
    "factory": ("getTournamentFactory()", ["address"]),
    "staging_period": ("getClaimStagingPeriod()", ["uint256"]),
    "sentry_count": ("getNumberOfSentries()", ["uint256"]),
    "sentry_manager": ("getSentryManager()", ["address"]),
}
SEALED = ("getCurrentSealedEpoch()", ["uint256", "uint256", "uint256", "address", "bool", "uint256", "bytes32", "bytes32"])
CAN_STAGE = ("canStageTournamentResult()", ["bool", "bool", "bool", "uint256", "bytes32", "bytes32"])
CAN_ACCEPT = ("canAcceptStagedTournamentResult()", ["bool", "bool", "bool", "uint256", "bytes32", "bytes32"])
STANDING = ("tournamentStanding()", ["uint8", "bool", "bool", "bytes32", "bytes32", "bytes32", "uint64", "uint64"])
DESCRIPTOR = ("tournamentDescriptor()", ["bytes32", "uint256", "uint64", "uint64", "uint64", "uint8", "uint64", "uint64"])
BOND_RECOVERY = ("bondRecovery()", ["uint8", "address", "uint256"])
BOND_VALUE = ("bondValue()", ["uint256"])
COMMITMENT = ("commitmentStanding(bytes32)", ["bool", "bytes32", "address", "bool", "uint64", "uint64"])
BISECTING = ("bisectingMatch(bytes32)", ["uint8", "bytes32", "bytes32", "bytes32", "uint256", "uint256", "uint64", "uint8"])


def _pick(values: list, index: int, table: list[str]):
    return table[index] if 0 <= index < len(table) else str(index)


class ChainIndex:
    def __init__(self, rpc_url: str, factory: str | None = None, ledger_limit: int = 5000):
        self.eth = Eth(rpc_url)
        self.rpc_url = rpc_url
        self.factory = factory
        self.ledger_limit = ledger_limit
        self.notices: deque = deque(maxlen=50)
        self.generation = 0  # bumped on every rebuild, so node caches can be dropped with it
        self._reset()

    # ------------------------------------------------------------------ state

    def _reset(self) -> None:
        self.chain_id: int | None = None
        self.head = 0
        self.finalized = 0
        self.indexed_to = -1          # last block fully indexed
        self.hashes: dict[int, str] = {}  # recent block hashes, for reorg detection
        self.apps: dict[str, dict] = {}
        self.tournaments: dict[str, dict] = {}  # address -> tournament, all apps
        self.ledgers: dict[str, deque] = {}     # app -> its transactions, capped per app
        self.ledger_dropped: dict[str, int] = {}
        self.senders: dict[str, str] = {}       # tx hash -> from
        self.discovered: set[str] = set()
        self._log_txs: dict[str, set[str]] = {}

    # ------------------------------------------------------------------- poll

    def poll(self, wanted_apps: set[str]) -> None:
        eth = self.eth
        chain_id, head_hex, finalized_block, latest_block = eth.batch([
            ("eth_chainId", []), ("eth_blockNumber", []),
            ("eth_getBlockByNumber", ["finalized", False]),
            ("eth_getBlockByNumber", ["latest", False]),
        ])
        for item in (chain_id, head_hex):
            if isinstance(item, RpcError):
                raise item
        chain_id = int(chain_id, 16)
        head = int(head_hex, 16)
        if self.chain_id is not None and chain_id != self.chain_id:
            self._notice("chain id changed", f"{self.chain_id} to {chain_id}; index rebuilt")
            self._reset()
            self.generation += 1
        self.chain_id = chain_id
        self._check_canonical(head)
        self.head = head
        self.finalized = int(finalized_block["number"], 16) if isinstance(finalized_block, dict) and finalized_block else head
        if isinstance(latest_block, dict):
            self.hashes[head] = latest_block["hash"]

        self._discover_factory_apps(head)
        for address in sorted(wanted_apps | self.discovered):
            if address not in self.apps:
                self._add_app(address)

        start = self.indexed_to + 1
        if self.apps and start <= head:
            first = min((app["deploy_block"] for app in self.apps.values()), default=0)
            start = max(start, first)
            self._index_range(start, head)
        self.indexed_to = max(self.indexed_to, head)
        self._remember_hashes(head)
        self._refresh_live()

    def _notice(self, kind: str, detail: str) -> None:
        self.notices.appendleft({"at": time.time(), "kind": kind, "detail": detail, "block": self.head})

    def _check_canonical(self, head: int) -> None:
        """Rebuild when the chain went backwards or a block we indexed was replaced."""
        if self.indexed_to < 0:
            return
        if head < self.indexed_to:
            self._notice("chain rewound", f"head {head} is below indexed block {self.indexed_to}; "
                         "a chain reset or deep rollback. Index rebuilt from scratch.")
            self._reset_keep_meta()
            return
        probe = [b for b in sorted(self.hashes) if b <= self.indexed_to][-8:]
        if not probe:
            return
        replies = self.eth.batch([("eth_getBlockByNumber", [hex(b), False]) for b in probe])
        mismatched = [b for b, r in zip(probe, replies)
                      if not isinstance(r, dict) or r.get("hash") != self.hashes[b]]
        if mismatched:
            depth = self.indexed_to - min(mismatched) + 1
            beyond = min(mismatched) <= self.finalized
            self._notice("reorg" if not beyond else "reorg past finalized",
                         f"blocks from {min(mismatched)} were replaced (depth {depth}). Index rebuilt.")
            self._reset_keep_meta()

    def _reset_keep_meta(self) -> None:
        notices, chain_id = self.notices, self.chain_id
        self._reset()
        self.notices, self.chain_id = notices, chain_id
        self.generation += 1

    def _remember_hashes(self, head: int) -> None:
        keep = {b: h for b, h in self.hashes.items() if b > head - 64}
        self.hashes = keep

    # ------------------------------------------------------------------- apps

    def _discover_factory_apps(self, head: int) -> None:
        if not self.factory:
            return
        cursor = getattr(self, "_factory_cursor", 0)
        if cursor > head:
            return
        logs = self.eth.get_logs([self.factory], [abi.TOPIC["DaveAppCreated"]], cursor, head)
        for log in logs:
            decoded = abi.decode_log(log)
            if decoded:
                self.discovered.add(decoded["args"]["appContract"].lower())
        self._factory_cursor = head + 1

    def _static_calls(self, address: str, table: dict) -> dict:
        names = list(table)
        replies = self.eth.batch([
            ("eth_call", [{"to": address, "data": abi.encode_call(table[n][0])}, "latest"]) for n in names])
        out = {}
        for name, reply in zip(names, replies):
            if isinstance(reply, RpcError) or not reply or reply == "0x":
                out[name] = None
            else:
                out[name] = abi.decode(table[name][1], reply)[0]
        return out

    def _add_app(self, address: str) -> None:
        code = self.eth.call("eth_getCode", [address, "latest"])
        if not code or code == "0x":
            return  # not deployed on this chain (yet); try again next poll
        info = self._static_calls(address, S_APP)
        consensus = info.get("consensus")
        if not consensus or consensus == ZERO_ADDRESS:
            return
        cinfo = self._static_calls(consensus, S_CONSENSUS)
        if cinfo.get("input_box") is None:
            self._notice("not a Dave app", f"{address} has a consensus without getInputBox; skipped")
            return
        sentries = []
        for sentry_id in range(1, (cinfo.get("sentry_count") or 0) + 1):
            reply = self.eth.eth_call(consensus, abi.encode_call("getSentryById(uint256)", sentry_id))
            sentries.append({"id": sentry_id, "address": abi.decode(["address"], reply)[0]})
        factory = cinfo.get("factory")
        allowance = None
        if factory:
            try:
                reply = self.eth.eth_call(factory, abi.encode_call("tournamentParameters(uint64)", 0))
                allowance = abi.decode(["uint64"] * 5, reply)[4]
            except (RpcError, ValueError):
                allowance = None
        self.apps[address] = {
            "address": address,
            "consensus": consensus.lower(),
            "input_box": cinfo["input_box"].lower(),
            "factory": (factory or "").lower() or None,
            "template_hash": info.get("template_hash"),
            "deploy_block": info.get("deploy_block") or 0,
            "staging_period": cinfo.get("staging_period"),
            "sentry_manager": cinfo.get("sentry_manager"),
            "sentries": sentries,
            "root_allowance": allowance,
            "inputs": {},
            "epochs": {},
            "live": {},
        }
        # Pick up this app's history even if the index already moved past its deployment.
        if self.indexed_to >= 0:
            self._index_range(self.apps[address]["deploy_block"], self.indexed_to, only=address)

    # ---------------------------------------------------------------- indexing

    def _index_range(self, start: int, end: int, only: str | None = None) -> None:
        apps = [self.apps[only]] if only else list(self.apps.values())
        if not apps or start > end:
            return
        by_consensus = {app["consensus"]: app for app in apps}
        by_address = {app["address"]: app for app in apps}
        boxes = sorted({app["input_box"] for app in apps})
        app_topics = ["0x" + a[2:].rjust(64, "0") for a in sorted(by_address)]

        logs = self.eth.get_logs(sorted(by_consensus), [abi.CONSENSUS_EVENTS], start, end)
        logs += self.eth.get_logs(boxes, [abi.TOPIC["InputAdded"], app_topics], start, end)
        known = [t for t, info in self.tournaments.items() if info["app"] in by_address]
        if known:
            logs += self.eth.get_logs(known, [abi.TOURNAMENT_EVENTS], start, end)

        self._log_txs: dict[str, set[str]] = {}
        pending = self._apply(logs, by_consensus, by_address)
        # New tournaments appear mid-range (EpochSealed, NewInnerTournament): fetch their logs too.
        while pending:
            fresh = sorted(pending)
            pending = set()
            first = min(self.tournaments[t]["created_block"] for t in fresh)
            more = self.eth.get_logs(fresh, [abi.TOURNAMENT_EVENTS], max(first, start), end)
            pending = self._apply(more, by_consensus, by_address)
        self._scan_transactions(start, end, apps)

    def _apply(self, logs: list[dict], by_consensus: dict, by_address: dict) -> set[str]:
        created: set[str] = set()
        logs = sorted(logs, key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16)))
        for log in logs:
            # Remember which watched contracts each tx touched, so a tx sent through another
            # contract (a bot's multicall or helper) still lands in the ledger.
            self._log_txs.setdefault(log["transactionHash"], set()).add(log["address"].lower())
            decoded = abi.decode_log(log)
            if not decoded:
                continue
            name, args = decoded["name"], decoded["args"]
            where = log["address"].lower()
            meta = {"block": int(log["blockNumber"], 16), "tx": log["transactionHash"],
                    "log_index": int(log["logIndex"], 16)}
            if where in by_consensus:
                created |= self._consensus_event(by_consensus[where], name, args, meta)
            elif name == "InputAdded":
                app = by_address.get(args["appContract"].lower())
                if app:
                    self._input_event(app, args, meta)
            elif where in self.tournaments:
                created |= self._tournament_event(self.tournaments[where], name, args, meta)
        return created

    def _new_tournament(self, app: str, address: str, epoch: int, level: int, block: int,
                        parent: str | None = None, parent_match: str | None = None) -> bool:
        address = address.lower()
        if address in self.tournaments or int(address, 16) == 0:
            return False
        self.tournaments[address] = {
            "address": address, "app": app, "epoch": epoch, "level": level,
            "parent": parent, "parent_match": parent_match, "created_block": block,
            "commitments": {}, "matches": {}, "children": [], "bonds": [], "refunds": [],
            "events": [], "descriptor": None, "live": {}, "final": False,
        }
        if parent:
            self.tournaments[parent]["children"].append(address)
        return True

    def _consensus_event(self, app: dict, name: str, args: dict, meta: dict) -> set[str]:
        created: set[str] = set()
        epochs = app["epochs"]
        if name == "EpochSealed":
            number = args["epochNumber"]
            tournament = args["tournament"].lower()
            epochs[number] = {
                "number": number, "lower": args["inputIndexLowerBound"],
                "upper": args["inputIndexUpperBound"],
                "initial_state": args["initialMachineStateHash"],
                "outputs_root": args["outputsMerkleRoot"], "tournament": tournament,
                "sealed": meta, "staged": None, "sentry_claims": [], "accepted": None,
            }
            # Sealing epoch n+1 is what accepting epoch n does (except the very first seal).
            previous = epochs.get(number - 1)
            if previous is not None:
                previous["accepted"] = dict(meta, accepted_state=args["initialMachineStateHash"])
            if self._new_tournament(app["address"], tournament, number, 0, meta["block"]):
                created.add(tournament)
        elif name == "EpochStaged":
            epoch = epochs.get(args["epochNumber"])
            if epoch is not None:
                epoch["staged"] = dict(meta, state=args["stagedPostEpochMachineStateHash"],
                                       outputs_root=args["stagedPostEpochOutputsMerkleRoot"])
        elif name == "SentryClaim":
            epoch = epochs.get(args["epochNumber"])
            if epoch is not None:
                epoch["sentry_claims"].append(dict(meta, sentry_id=args["sentryId"],
                                                   sentry=args["sentry"],
                                                   state=args["postEpochMachineStateHash"]))
        return created

    @staticmethod
    def _input_event(app: dict, args: dict, meta: dict) -> None:
        raw = bytes.fromhex(args["input"][2:])
        fields = abi.decode_evm_advance(raw) or {}
        payload = fields.get("payload", b"")
        app["inputs"][args["index"]] = {
            "index": args["index"], **meta,
            "sender": fields.get("sender"),
            "size": len(payload),
            "payload_head": "0x" + payload[:256].hex(),
            "raw": raw,       # kept in memory for per-node comparison; never sent to clients
            "payload": payload,
        }

    def _tournament_event(self, t: dict, name: str, args: dict, meta: dict) -> set[str]:
        created: set[str] = set()
        t["events"].append({"name": name, **meta, "args": {k: v for k, v in args.items()}})
        if name == "CommitmentJoined":
            root = args["commitment"]
            t["commitments"][root] = {"root": root, "final_state": args["finalStateHash"],
                                      "submitter": args["submitter"].lower(), **meta, "live": None}
        elif name == "MatchCreated":
            mid = args["matchIdHash"]
            t["matches"][mid] = {"id": mid, "one": args["one"], "two": args["two"],
                                 "left_of_two": args["leftOfTwo"], "created": meta,
                                 "eliminable_at": args["eliminableAt"], "advances": [],
                                 "sealed": None, "deleted": None, "child": None, "live": None}
        elif name == "MatchAdvanced":
            match = t["matches"].get(args["matchIdHash"])
            if match:
                match["advances"].append(dict(meta, eliminable_at=args["eliminableAt"]))
                match["eliminable_at"] = args["eliminableAt"]
        elif name == "LeafMatchSealed":
            match = t["matches"].get(args["matchIdHash"])
            if match:
                match["sealed"] = meta
                match["eliminable_at"] = args["eliminableAt"]
        elif name == "MatchDeleted":
            match = t["matches"].get(args["matchIdHash"])
            if match is None:
                match = t["matches"][args["matchIdHash"]] = {
                    "id": args["matchIdHash"], "one": args["one"], "two": args["two"],
                    "left_of_two": None, "created": None, "eliminable_at": None,
                    "advances": [], "sealed": None, "deleted": None, "child": None, "live": None}
            match["deleted"] = dict(meta, reason=_pick([], args["reason"], DELETE_REASONS),
                                    winner=_pick([], args["winnerCommitment"], WINNERS))
        elif name == "NewInnerTournament":
            child = args["childTournament"].lower()
            match = t["matches"].get(args["matchIdHash"])
            if match:
                match["child"] = child
                match["sealed"] = match["sealed"] or meta
            if self._new_tournament(t["app"], child, t["epoch"], t["level"] + 1, meta["block"],
                                    parent=t["address"], parent_match=args["matchIdHash"]):
                created.add(child)
        elif name == "BondRecovered":
            t["bonds"].append(dict(meta, commitment=args["commitment"], claimer=args["claimer"].lower(),
                                   payment=str(args["payment"]), burned=str(args["burned"])))
        elif name == "PartialBondRefund":
            t["refunds"].append(dict(meta, recipient=args["recipient"].lower(),
                                     value=str(args["value"]), success=args["success"]))
        return created

    # ------------------------------------------------------------ tx ledger

    def _scan_transactions(self, start: int, end: int, apps: list[dict]) -> None:
        """Record every tx sent to an app's contracts, including reverted ones."""
        watched = set()
        for app in apps:
            watched |= {app["address"], app["consensus"], app["input_box"]}
        watched |= {t for t, info in self.tournaments.items() if info["app"] in {a["address"] for a in apps}}
        contract_app = {}
        for app in apps:
            for addr in (app["address"], app["consensus"]):
                contract_app[addr] = app["address"]
        for t, info in self.tournaments.items():
            contract_app[t] = info["app"]

        for chunk_start in range(start, end + 1, 50):
            numbers = list(range(chunk_start, min(end, chunk_start + 49) + 1))
            blocks = self.eth.batch([("eth_getBlockByNumber", [hex(n), True]) for n in numbers])
            hits = []
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                number = int(block["number"], 16)
                self.hashes[number] = block["hash"]
                for tx in block.get("transactions", []):
                    to = (tx.get("to") or "").lower()
                    if to in watched or tx["hash"] in self._log_txs:
                        hits.append((number, tx, to))
            if not hits:
                continue
            receipts = self.eth.batch([("eth_getTransactionReceipt", [tx["hash"]]) for _, tx, _ in hits])
            for (number, tx, to), receipt in zip(hits, receipts):
                ok = isinstance(receipt, dict) and receipt.get("status") == "0x1"
                self.senders[tx["hash"]] = tx["from"].lower()
                touched = sorted(self._log_txs.get(tx["hash"], set()) - {to})
                app = contract_app.get(to)
                if app is None and to in {a["input_box"] for a in apps}:
                    app = self._input_box_app(tx.get("input") or "")
                if app is None:
                    app = next((contract_app[t] for t in touched if t in contract_app), None)
                entry = {
                    "block": number, "hash": tx["hash"], "from": tx["from"].lower(), "to": to,
                    "app": app, "function": abi.function_name(tx.get("input")),
                    "status": "ok" if ok else "reverted",
                    "gas_used": int(receipt["gasUsed"], 16) if isinstance(receipt, dict) else None,
                    "revert": None,
                    "epoch_arg": None,
                    "via": to not in watched,  # reached the app through another contract
                    "touched": touched,
                }
                if entry["function"] in ("stageTournamentResult", "submitSentryClaim",
                                         "acceptStagedTournamentResult"):
                    data = tx.get("input") or ""
                    if len(data) >= 74:
                        entry["epoch_arg"] = int(data[10:74], 16)
                if not ok:
                    entry["revert"] = self._revert_reason(tx, number)
                self._record(entry)

    def _record(self, entry: dict) -> None:
        # Capped per app, so one app's retry storm cannot push another app's history out.
        key = entry["app"] or ""
        ledger = self.ledgers.setdefault(key, deque(maxlen=self.ledger_limit))
        if len(ledger) == ledger.maxlen:
            self.ledger_dropped[key] = self.ledger_dropped.get(key, 0) + 1
        ledger.append(entry)

    @property
    def ledger(self) -> list[dict]:
        return sorted((e for l in self.ledgers.values() for e in l), key=lambda e: e["block"])

    @staticmethod
    def _input_box_app(calldata: str) -> str | None:
        # addInput(address appContract, bytes payload): the app is the first argument.
        if calldata.startswith("0x1789cd63") and len(calldata) >= 10 + 64:
            return "0x" + calldata[10 + 24:10 + 64].lower()
        return None

    def _revert_reason(self, tx: dict, block: int) -> str | None:
        call = {"from": tx["from"], "to": tx["to"], "data": tx.get("input"),
                "value": tx.get("value", "0x0"), "gas": tx.get("gas")}
        try:
            self.eth.call("eth_call", [call, hex(max(0, block - 1))])
            return "reverted (no reason on replay)"
        except RpcError as exc:
            data = exc.data if isinstance(exc.data, str) else (exc.data or {}).get("data") if isinstance(exc.data, dict) else None
            return abi.decode_revert(data) or str(exc)

    def sender(self, tx_hash: str | None) -> str | None:
        if not tx_hash:
            return None
        if tx_hash not in self.senders:
            try:
                tx = self.eth.call("eth_getTransactionByHash", [tx_hash])
                self.senders[tx_hash] = (tx or {}).get("from", "").lower() or None
            except RpcError:
                return None
        return self.senders[tx_hash]

    # ------------------------------------------------------------- live views

    def _refresh_live(self) -> None:
        calls: list[tuple] = []
        sinks: list[tuple] = []

        def add(to: str, signature: str, types: list[str], sink, *args) -> None:
            calls.append(("eth_call", [{"to": to, "data": abi.encode_call(signature, *args)}, "latest"]))
            sinks.append((types, sink))

        for app in self.apps.values():
            live = app["live"]
            add(app["consensus"], SEALED[0], SEALED[1], lambda v, l=live: l.__setitem__("sealed", v))
            add(app["consensus"], CAN_STAGE[0], CAN_STAGE[1], lambda v, l=live: l.__setitem__("stage", v))
            add(app["consensus"], CAN_ACCEPT[0], CAN_ACCEPT[1], lambda v, l=live: l.__setitem__("accept", v))
            add(app["input_box"], "getNumberOfInputs(address)", ["uint256"],
                lambda v, l=live: l.__setitem__("input_count", v[0]), app["address"])

        for t in self.tournaments.values():
            if t["final"]:
                continue
            tl = t["live"]
            add(t["address"], STANDING[0], STANDING[1], lambda v, l=tl: l.__setitem__("standing", v))
            add(t["address"], BOND_RECOVERY[0], BOND_RECOVERY[1], lambda v, l=tl: l.__setitem__("bond", v))
            if t["descriptor"] is None:
                add(t["address"], DESCRIPTOR[0], DESCRIPTOR[1], lambda v, tt=t: tt.__setitem__("descriptor", v))
                add(t["address"], BOND_VALUE[0], BOND_VALUE[1], lambda v, l=tl: l.__setitem__("bond_value", v[0]))
            for c in t["commitments"].values():
                add(t["address"], COMMITMENT[0], COMMITMENT[1],
                    lambda v, cc=c: cc.__setitem__("live", v), c["root"])
            for m in t["matches"].values():
                if m["deleted"] is None:
                    add(t["address"], BISECTING[0], BISECTING[1],
                        lambda v, mm=m: mm.__setitem__("live", v), m["id"])

        for offset in range(0, len(calls), 200):
            replies = self.eth.batch(calls[offset: offset + 200])
            for (types, sink), reply in zip(sinks[offset: offset + 200], replies):
                if isinstance(reply, RpcError) or not reply or reply == "0x":
                    continue
                try:
                    sink(abi.decode(types, reply))
                except ValueError:
                    continue

        # A tournament is final once its bond is settled and no match is open.
        for t in self.tournaments.values():
            bond = t["live"].get("bond")
            open_matches = any(m["deleted"] is None for m in t["matches"].values())
            if t["level"] == 0 and bond and bond[0] in (1, 3) and not open_matches:
                t["final"] = True
            if t["level"] > 0 and t["parent"] and self.tournaments[t["parent"]]["final"]:
                t["final"] = True
