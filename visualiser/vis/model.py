"""Merge chain truth with every node's view into the JSON the API serves.

Rules:
  * Epoch status comes from chain events only.
  * Each node's status is reported as that node reports it.
  * A field is "agree" only when at least two sources have it and all are equal.
  * A commitment is attributed to a node when its root equals that node's
    computed commitment, or its final state equals the node's final state;
    who sent the join does not matter.
"""

from __future__ import annotations

import hashlib
import time

from .chain import BOND, PHASES, STANDINGS, ChainIndex

ZERO32 = "0x" + "0" * 64
LAG_WARN = 10  # blocks behind finalized before a node is called lagging
TESTED_VERSIONS = {"sling": {"2.0.0"}, "reference": {"2.0.0-alpha.13"}}  # node versions this build reads correctly
NOTICE_TTL = 30 * 60  # chain notices (reorgs, rewinds) drop off the overview after this many seconds


PROVISIONAL_REFERENCE = {"OPEN", "CLOSED", "INPUTS_PROCESSED"}


def _nz(value):
    return None if value in (None, ZERO32, "0x" + "0" * 40) else value


def _preview(payload: bytes, limit: int = 160) -> str:
    text = payload[:limit].decode("utf-8", "replace")
    printable = sum(ch.isprintable() for ch in text)
    if text and printable / len(text) > 0.85:
        return text + ("…" if len(payload) > limit else "")
    return ""


def verdict(values: dict) -> str:
    present = {k: v for k, v in values.items() if v is not None}
    if len(present) < 2:
        return "pending"
    return "agree" if len({str(v).lower() for v in present.values()}) == 1 else "diverge"


class Model:
    def __init__(self, cfg, chain: ChainIndex, sources: list):
        self.cfg = cfg
        self.chain = chain
        self.sources = sources
        self._names: dict[str, str] = {}   # last app name any node reported

    # ---------------------------------------------------------------- helpers

    def label(self, address: str | None) -> str | None:
        if not address:
            return None
        address = address.lower()
        known = self.cfg.label(address)
        if known:
            return known
        for app in self.chain.apps.values():
            if address == app["address"]:
                return "application"
            if address == app["consensus"]:
                return "consensus"
            if address == app["input_box"]:
                return "input box"
        t = self.chain.tournaments.get(address)
        if t:
            return f"tournament, epoch {t['epoch']} level {t['level']}"
        return None

    def who(self, address: str | None) -> dict | None:
        return {"address": address, "label": self.label(address)} if address else None

    def _slings(self, app: str):
        return [s for s in self.sources if s.kind == "sling" and s.state.get("app") == app]

    def _refs(self, app: str):
        return [(s, s.state.get("apps", {}).get(app)) for s in self.sources
                if s.kind == "reference" and s.state.get("apps", {}).get(app)]

    # -------------------------------------------------------------- health

    def node_health(self, source, health: dict) -> dict:
        finalized = self.chain.finalized
        state = source.state or {}
        if source.kind == "sling":
            processed = state.get("latest_processed_block")
            version = state.get("node_version")
            apps = [state["app"]] if state.get("app") else []
        else:
            checks = [a.get("last_input_check_block") for a in state.get("apps", {}).values()
                      if a.get("last_input_check_block") is not None]
            processed = min(checks) if checks else None
            version = state.get("version")
            apps = list(state.get("apps", {}))
        lag = finalized - processed if processed is not None and finalized else None
        if not health.get("ok"):
            status = "off" if source.cfg.optional else "down"
        elif lag is not None and lag > LAG_WARN:
            status = "lagging"
        else:
            status = "live"
        return {
            "id": source.cfg.id, "kind": source.kind, "label": source.cfg.label,
            "status": status, "error": health.get("error"), "last_ok_at": health.get("last_ok_at"),
            "poll_ms": health.get("poll_ms"), "version": version, "processed_block": processed,
            "lag": lag, "apps": apps, "accounts": [self.who(a) for a in source.cfg.accounts],
            "endpoint": source.cfg.rpc or source.cfg.db or (f"docker cp {source.cfg.container}" if source.cfg.container else None),
        }

    # ------------------------------------------------------------- epochs

    def epoch_views(self, app: dict) -> list[dict]:
        chain_epochs = app["epochs"]
        live = app["live"]
        sealed = live.get("sealed")
        current = sealed[0] if sealed else (max(chain_epochs) if chain_epochs else None)
        input_count = live.get("input_count", len(app["inputs"]))
        numbers = set(chain_epochs)
        if current is not None:
            numbers.add(current + 1)  # the epoch collecting inputs
        slings = self._slings(app["address"])
        refs = self._refs(app["address"])
        for _, ref_app in refs:
            numbers |= set(ref_app.get("epochs", {}))
        views = []
        for n in sorted(numbers):
            views.append(self._epoch_view(app, n, current, input_count, slings, refs))
        return views

    def _epoch_view(self, app, n, current, input_count, slings, refs) -> dict:
        e = app["epochs"].get(n)
        t = self.chain.tournaments.get(e["tournament"]) if e else None
        if e:
            lower, upper = e["lower"], e["upper"]
        else:
            prev = app["epochs"].get(n - 1)
            lower, upper = (prev["upper"] if prev else 0), None
        input_total = (upper if upper is not None else input_count) - lower

        # Chain lifecycle, from events only.
        if e is None:
            status = "open" if current is not None and n == current + 1 else "unknown"
        elif e["accepted"]:
            status = "accepted"
        elif e["staged"]:
            status = "staged"
        else:
            standing = (t or {}).get("live", {}).get("standing")
            joined = len((t or {}).get("commitments", {}))
            if standing and standing[0] == 3:
                status = "failed"
            elif standing and standing[0] == 2:
                status = "decided"
            elif joined > 1:
                status = "disputed"
            else:
                status = "sealed"
        disputed = bool(t and len(t["commitments"]) > 1)

        # What each node computed.
        nodes = {}
        for s in slings:
            st = s.state
            known = next((x for x in st.get("epochs", []) if x["number"] == n), None)
            settle = st.get("settlements", {}).get(n)
            if known is None:
                node_status = "collecting" if status == "open" else "not seen"
            elif st.get("next_epoch") is not None and st["next_epoch"] > n:
                node_status = "settled"
            elif settle:
                node_status = "computed"
            else:
                node_status = "sealed"
            nodes[s.cfg.id] = {
                "status": node_status,
                "lower": known["lower"] if known else None, "upper": known["upper"] if known else None,
                "final_state": settle["final_state"] if settle else None,
                "commitment": settle["commitment"] if settle else None,
                "events_seen": st.get("tournament_events", {}).get((e or {}).get("tournament") or "", None),
                "tree_nodes": st.get("tree_nodes", {}).get(n),
            }
        for s, ref_app in refs:
            ep = ref_app.get("epochs", {}).get(n)
            # Before the node closes an epoch its bounds and machine hash track inputs processed
            # so far. Those are provisional: show the status, but do not compare them.
            if ep and (ep["status"] or "").upper() in PROVISIONAL_REFERENCE:
                ep = dict(ep, lower=None, upper=None, machine_hash=None, commitment=None)
            nodes[s.cfg.id] = {
                "status": (ep["status"].replace("_", " ").lower() if ep else
                           ("collecting" if status == "open" else "not seen")),
                "lower": ep["lower"] if ep else None, "upper": ep["upper"] if ep else None,
                "final_state": _nz(ep["machine_hash"]) if ep else None,
                "commitment": _nz(ep["commitment"]) if ep else None,
                "staged_at_block": ep["staged_at_block"] if ep else None,
            }

        # Three-way checks.
        chain_final = None
        chain_commitment = None
        if e:
            if e["accepted"]:
                chain_final = e["accepted"]["accepted_state"]
            elif e["staged"]:
                chain_final = e["staged"]["state"]
            standing = (t or {}).get("live", {}).get("standing")
            if standing and standing[0] == 2:  # ROOT_WINNER
                chain_commitment = _nz(standing[3])
                chain_final = chain_final or _nz(standing[4])
        checks = []
        if e and status != "open":
            checks.append(self._check("Input range", {"chain": f"{lower}–{upper}"},
                                      {k: (f"{v['lower']}–{v['upper']}" if v["upper"] is not None else None)
                                       for k, v in nodes.items()}))
            checks.append(self._check("Final state", {"chain": chain_final},
                                      {k: v["final_state"] for k, v in nodes.items()}))
            checks.append(self._check("Commitment", {"chain": chain_commitment},
                                      {k: v["commitment"] for k, v in nodes.items()}))
        agreement = "diverge" if any(c["verdict"] == "diverge" for c in checks) else (
            "agree" if checks and all(c["verdict"] == "agree" for c in checks) else "pending")

        view = {
            "number": n, "status": status, "disputed": disputed, "current": n == current,
            "lower": lower, "upper": upper, "input_count": max(0, input_total),
            "tournament": e["tournament"] if e else None,
            "commitment_count": len(t["commitments"]) if t else 0,
            "match_count": len(t["matches"]) if t else 0,
            "nodes": nodes, "checks": checks, "agreement": agreement,
            "sealed": self._stamp(e["sealed"]) if e else None,
            "staged": self._stamp(e["staged"], state=e["staged"]["state"]) if e and e["staged"] else None,
            "accepted": self._stamp(e["accepted"]) if e and e["accepted"] else None,
            "sentry_claims": [dict(self._stamp(c), sentry=self.who(c["sentry"]), state=c["state"],
                                   agrees=(c["state"] == e["staged"]["state"]) if e and e["staged"] else None)
                              for c in (e["sentry_claims"] if e else [])],
        }
        if e and e["staged"] and not e["accepted"]:
            period = app["staging_period"] or 0
            view["staging_ends_at"] = e["staged"]["block"] + period
            view["staging_blocks_left"] = max(0, e["staged"]["block"] + period - self.chain.head)
        if t and t["descriptor"]:
            d = t["descriptor"]
            view["join_window_closes_at"] = d[6] + d[7]
        return view

    def _check(self, name: str, chain: dict, nodes: dict) -> dict:
        values = dict(chain)
        values.update(nodes)
        return {"name": name, "values": values, "verdict": verdict(values)}

    def _stamp(self, meta: dict | None, **extra) -> dict | None:
        if not meta:
            return None
        return dict({"block": meta["block"], "tx": meta["tx"], "by": self.who(self.chain.sender(meta["tx"]))}, **extra)

    # ------------------------------------------------------------- inputs

    def epoch_inputs(self, app: dict, view: dict) -> list[dict]:
        lower = view["lower"]
        upper = view["upper"] if view["upper"] is not None else max(app["inputs"], default=-1) + 1
        slings = self._slings(app["address"])
        refs = self._refs(app["address"])
        rows = []
        for index in range(lower, upper):
            item = app["inputs"].get(index)
            if item is None:
                continue
            raw_hash = hashlib.sha256(item["raw"]).hexdigest()
            payload_hash = hashlib.sha256(item["payload"]).hexdigest()
            per_node = {}
            for s in slings:
                digest = s.input_digest(view["number"], index - lower)
                if digest is None:
                    per_node[s.cfg.id] = {"status": "missing"}
                elif digest["sha256"] in (raw_hash, payload_hash):
                    per_node[s.cfg.id] = {"status": "stored"}
                else:
                    per_node[s.cfg.id] = {"status": "differs", "size": digest["size"]}
            for s, ref_app in refs:
                r = ref_app.get("inputs", {}).get(index)
                if r is None:
                    per_node[s.cfg.id] = {"status": "missing"}
                    continue
                outs = ref_app.get("output_counts", {}).get(index, [0, 0])
                entry = {"status": (r["status"] or "NONE").lower(), "outputs": outs[0], "reports": outs[1]}
                if r["epoch"] is not None and r["epoch"] != view["number"]:
                    entry["wrong_epoch"] = r["epoch"]
                per_node[s.cfg.id] = entry
            rows.append({
                "index": index, "index_in_epoch": index - lower, "block": item["block"], "tx": item["tx"],
                "sender": self.who(item["sender"]), "size": item["size"],
                "text": _preview(item["payload"]), "hex": item["payload_head"],
                "nodes": per_node,
            })
        return rows

    # ------------------------------------------------------------ disputes

    def dispute(self, app: dict, view: dict) -> dict | None:
        root = self.chain.tournaments.get(view["tournament"] or "")
        if not root:
            return None
        owners: dict[str, list[str]] = {}
        finals: dict[str, list[str]] = {}
        for node_id, node in view["nodes"].items():
            if node.get("commitment"):
                owners.setdefault(node["commitment"].lower(), []).append(node_id)
            if node.get("final_state"):
                finals.setdefault(node["final_state"].lower(), []).append(node_id)
        return self._tournament(root, owners, finals)

    def _tournament(self, t: dict, owners: dict, finals: dict) -> dict:
        head = self.chain.head
        live = t["live"]
        standing = live.get("standing")
        bond = live.get("bond")
        d = t["descriptor"]
        losers = {}
        for m in t["matches"].values():
            if m["deleted"]:
                w = m["deleted"]["winner"]
                if w in ("two", "none"):
                    losers[m["one"]] = m["deleted"]["block"]
                if w in ("one", "none"):
                    losers[m["two"]] = m["deleted"]["block"]

        commitments = []
        for c in sorted(t["commitments"].values(), key=lambda x: (x["block"], x["log_index"])):
            by_root = owners.get(c["root"].lower(), [])
            # A sybil can end on the honest final state with a different root (it diverges
            # mid-computation), so the root decides. The final state only stands in while no
            # node has reported a commitment yet.
            by_final = finals.get(c["final_state"].lower(), []) if t["level"] == 0 and not owners else []
            matched = sorted(set(by_root) | set(by_final))
            if matched:
                side = "honest"
            elif t["level"] == 0 and finals:
                side = "rival"
            else:
                side = "unknown"
            clock = None
            if c["live"]:
                joined, _, claimer, running, deadline, allowance = c["live"]
                clock = {"running": running, "deadline": deadline if running else None,
                         "blocks_left": (deadline - head) if running else allowance,
                         "claimer": self.who(_nz(claimer))}
            commitments.append({
                "root": c["root"], "final_state": c["final_state"], "submitter": self.who(c["submitter"]),
                "block": c["block"], "tx": c["tx"], "side": side, "matches_nodes": matched,
                "eliminated_at": losers.get(c["root"]), "clock": clock,
            })

        by_root = {c["root"]: c for c in commitments}
        matches = []
        height = d[3] if d else None
        for m in sorted(t["matches"].values(), key=lambda x: (x["created"] or {}).get("block", 0)):
            phase = height_left = None
            if m["live"]:
                phase = PHASES[m["live"][0]] if m["live"][0] < len(PHASES) else str(m["live"][0])
                height_left = m["live"][6] if m["live"][0] == 1 else None
            child = self.chain.tournaments.get(m["child"]) if m["child"] else None
            winner_root = None
            if m["deleted"] and m["deleted"]["winner"] in ("one", "two"):
                winner_root = m[m["deleted"]["winner"]]
            matches.append({
                "id": m["id"], "one": m["one"], "two": m["two"],
                "created_block": (m["created"] or {}).get("block"),
                "eliminable_at": m["eliminable_at"],
                "blocks_to_eliminable": (m["eliminable_at"] - head) if m["eliminable_at"] and not m["deleted"] else None,
                "advances": [a["block"] for a in m["advances"]],
                "phase": "ended" if m["deleted"] else phase,
                "height_left": height_left, "tree_height": height,
                "sealed_block": (m["sealed"] or {}).get("block"),
                "deleted": {"block": m["deleted"]["block"], "reason": m["deleted"]["reason"],
                            "winner": m["deleted"]["winner"], "winner_root": winner_root,
                            "tx": m["deleted"]["tx"], "by": self.who(self.chain.sender(m["deleted"]["tx"]))}
                if m["deleted"] else None,
                "child": self._tournament(child, owners, finals) if child else None,
                "waiting_on": next(({"root": r, "side": by_root[r]["side"], "who": by_root[r]["submitter"],
                                     "nodes": by_root[r]["matches_nodes"],
                                     "blocks_left": by_root[r]["clock"]["blocks_left"]}
                                    for r in (m["one"], m["two"])
                                    if not m["deleted"] and r in by_root and (by_root[r]["clock"] or {}).get("running")),
                                   None),
            })

        events = []
        for ev in t["events"]:
            events.append({"block": ev["block"], "tx": ev["tx"], "name": ev["name"],
                           "by": self.who(self.chain.sender(ev["tx"])), "summary": self._summary(ev)})

        return {
            "address": t["address"], "level": t["level"], "created_block": t["created_block"],
            "standing": (STANDINGS[standing[0]] if standing and standing[0] < len(STANDINGS) else None),
            "accepts_joins": standing[1] if standing else None,
            "candidate": _nz(standing[3]) if standing and standing[2] else None,
            "finished_at": standing[6] if standing and standing[6] else None,
            "window": {"start": d[6], "close": d[6] + d[7], "allowance": d[7], "height": d[3],
                       "log2_stride": d[2], "kind": "leaf" if d[5] == 0 else "non-leaf"} if d else None,
            "bond": {"value": str(live["bond_value"]) if live.get("bond_value") is not None else None,
                     "disposition": BOND[bond[0]] if bond and bond[0] < len(BOND) else None,
                     "claimer": self.who(_nz(bond[1])) if bond else None,
                     "payment": str(bond[2]) if bond else None},
            "bonds": [{"block": b["block"], "claimer": self.who(b["claimer"]), "payment": b["payment"],
                       "burned": b["burned"], "commitment": b["commitment"]} for b in t["bonds"]],
            "refunds": [{"block": r["block"], "recipient": self.who(r["recipient"]), "value": r["value"],
                         "success": r["success"]} for r in t["refunds"]],
            "commitments": commitments, "matches": matches, "events": events,
        }

    @staticmethod
    def _summary(ev: dict) -> str:
        a = ev["args"]
        short = lambda h: (h[:10] + "…") if isinstance(h, str) and len(h) > 12 else h  # noqa: E731
        name = ev["name"]
        if name == "CommitmentJoined":
            return f"Joined with commitment {short(a['commitment'])}"
        if name == "MatchCreated":
            return f"Match {short(a['matchIdHash'])} created: {short(a['one'])} against {short(a['two'])}"
        if name == "MatchAdvanced":
            return f"Match {short(a['matchIdHash'])} bisected; eliminable at block {a['eliminableAt']}"
        if name == "LeafMatchSealed":
            return f"Leaf match {short(a['matchIdHash'])} sealed"
        if name == "MatchDeleted":
            reason = ["step", "timeout", "child tournament"][a["reason"]] if a["reason"] < 3 else a["reason"]
            winner = ["neither side", "commitment one", "commitment two"][a["winnerCommitment"]] if a["winnerCommitment"] < 3 else a["winnerCommitment"]
            return f"Match {short(a['matchIdHash'])} ended by {reason}; {winner} won"
        if name == "NewInnerTournament":
            return f"Inner tournament {short(a['childTournament'])} opened for match {short(a['matchIdHash'])}"
        if name == "BondRecovered":
            return f"Bond recovered for {short(a['commitment'])}"
        if name == "PartialBondRefund":
            return f"Gas refund of {a['value']} wei ({'paid' if a['success'] else 'failed'})"
        return name

    # --------------------------------------------------------------- views

    def overview(self, health: dict) -> dict:
        chain = self.chain
        nodes = [self.node_health(s, health.get(s.cfg.id, {})) for s in self.sources]
        apps = []
        alerts = []
        for app in chain.apps.values():
            views = self.epoch_views(app)
            recent = views[-24:]
            name = None
            for s, ref_app in self._refs(app["address"]):
                name = name or ref_app.get("name")
            if name:
                self._names[app["address"]] = name
            name = name or self._names.get(app["address"]) or self.cfg.app_names.get(app["address"])
            for s, ref_app in self._refs(app["address"]):
                if ref_app.get("state") not in (None, "ENABLED", "OK") or ref_app.get("reason"):
                    alerts.append({"level": "critical", "app": app["address"], "node": s.cfg.id,
                                   "text": f"{s.cfg.label} reports this app as {ref_app.get('state')}: "
                                           f"{ref_app.get('reason') or 'no reason given'}"})
            for s in self._slings(app["address"]):
                if (s.state.get("template_hash") or "").lower() != (app["template_hash"] or "").lower():
                    alerts.append({"level": "critical", "app": app["address"], "node": s.cfg.id,
                                   "text": f"{s.cfg.label} runs template {s.state.get('template_hash')}, "
                                           f"but the chain expects {app['template_hash']}"})
            for v in views:
                bad = [c["name"].lower() for c in v["checks"] if c["verdict"] == "diverge"]
                if bad:
                    alerts.append({"level": "critical", "app": app["address"], "epoch": v["number"],
                                   "text": f"Epoch {v['number']}: the chain and nodes disagree on {', '.join(bad)}"})
                if v["status"] in ("decided", "staged", "accepted") and v["disputed"]:
                    tree = self.dispute(app, v)
                    winner = next((c for c in tree["commitments"] if c["root"] == tree.get("candidate")), None) if tree else None
                    if winner and winner["side"] == "rival":
                        alerts.append({"level": "critical", "app": app["address"], "epoch": v["number"],
                                       "text": f"Epoch {v['number']}: the tournament winner matches no node; "
                                               "the honest commitment lost"})
                if v["status"] in ("disputed", "sealed") and v["match_count"]:
                    self._burning_alerts(app, v, nodes, alerts)
                if v["status"] == "disputed":
                    alerts.append({"level": "info", "app": app["address"], "epoch": v["number"],
                                   "text": f"Epoch {v['number']} is in dispute: "
                                           f"{v['commitment_count']} commitments, {v['match_count']} match{'' if v['match_count'] == 1 else 'es'}"})
            apps.append({
                "address": app["address"], "name": name, "consensus": app["consensus"],
                "template_hash": app["template_hash"], "input_count": app["live"].get("input_count"),
                "current_epoch": next((v["number"] for v in views if v["current"]), None),
                "epochs": [{k: v[k] for k in ("number", "status", "disputed", "agreement", "input_count",
                                              "current", "commitment_count")}
                           | {"nodes": {nid: n["status"] for nid, n in v["nodes"].items()}} for v in recent],
                "epoch_total": len(views),
                "nodes": [s.cfg.id for s in self.sources
                          if s in self._slings(app["address"]) or any(s is r for r, _ in self._refs(app["address"]))],
            })
        for n in nodes:
            tested = TESTED_VERSIONS.get(n["kind"], set())
            if n["version"] and tested and n["version"] not in tested:
                alerts.append({"level": "warning", "node": n["id"],
                               "text": f"{n['label']} runs version {n['version']}; this visualiser is tested with "
                                       f"{', '.join(sorted(tested))}. Some panels may be empty or wrong."})
            if n["status"] == "off":
                continue
            if n["status"] == "down":
                alerts.append({"level": "critical", "node": n["id"], "text": f"{n['label']} is unreachable: {n['error']}"})
            elif n["status"] == "lagging":
                alerts.append({"level": "warning", "node": n["id"],
                               "text": f"{n['label']} is {n['lag']} blocks behind finalized"})
        now = time.time()
        for notice in [x for x in chain.notices if now - x["at"] < NOTICE_TTL][:5]:
            alerts.append({"level": "warning", "at": notice["at"],
                           "text": f"{notice['kind'].capitalize()} at block {notice['block']}: {notice['detail']}"})
        order = {"critical": 0, "warning": 1, "info": 2}
        alerts.sort(key=lambda a: order[a["level"]])
        return {
            "chain": {"id": chain.chain_id, "rpc": chain.rpc_url, "head": chain.head,
                      "finalized": chain.finalized, "status": health.get("chain", {})},
            "nodes": nodes, "apps": apps, "alerts": alerts,
            "labels": dict(self.cfg.labels),
            "node_order": [s.cfg.id for s in self.sources],
            "generated_at": time.time(),
        }

    def _burning_alerts(self, app: dict, view: dict, nodes: list[dict], alerts: list[dict]) -> None:
        """Critical: a live match is waiting on the honest commitment while every node behind it is
        down or lagging. The honest side is losing clock with nobody able to move."""
        tree = self.dispute(app, view)
        health = {n["id"]: n for n in nodes}

        def walk(t):
            for m in (t or {}).get("matches", []):
                w = m.get("waiting_on")
                if w and w["side"] == "honest":
                    defenders = [health[i] for i in w["nodes"] if i in health]
                    if defenders and all(d["status"] in ("down", "off", "lagging") for d in defenders):
                        state = ", ".join(f"{d['label']} {'is down' if d['status'] in ('down', 'off') else 'is ' + str(d['lag']) + ' blocks behind'}"
                                          for d in defenders)
                        alerts.append({"level": "critical", "app": app["address"], "epoch": view["number"],
                                       "text": f"Epoch {view['number']}: the honest commitment's clock is running "
                                               f"({w['blocks_left']} blocks left) while {state}"})
                walk(m.get("child"))
        walk(tree)

    def app_detail(self, address: str) -> dict | None:
        app = self.chain.apps.get(address.lower())
        if not app:
            return None
        views = self.epoch_views(app)
        name = next((r.get("name") for _, r in self._refs(app["address"]) if r.get("name")), None)
        live = app["live"]
        stage = live.get("stage")
        accept = live.get("accept")
        slings = [{"id": s.cfg.id, "claimant": self.who(s.state.get("claimant")),
                   "node_version": s.state.get("node_version"), "emulator": s.state.get("emulator_version"),
                   "template_hash": s.state.get("template_hash"), "spans": s.state.get("spans"),
                   "next_epoch": s.state.get("next_epoch")} for s in self._slings(app["address"])]
        refs = [{"id": s.cfg.id, "name": r.get("name"), "state": r.get("state"), "reason": r.get("reason"),
                 "consensus_type": r.get("consensus_type"), "processed_inputs": r.get("processed_inputs"),
                 "last_input_check_block": r.get("last_input_check_block"),
                 "last_epoch_check_block": r.get("last_epoch_check_block"),
                 "last_tournament_check_block": r.get("last_tournament_check_block")}
                for s, r in self._refs(app["address"])]
        return {
            "address": app["address"], "name": name, "consensus": app["consensus"], "input_box": app["input_box"],
            "factory": app["factory"], "template_hash": app["template_hash"], "deploy_block": app["deploy_block"],
            "staging_period": app["staging_period"], "root_allowance": app["root_allowance"],
            "sentry_manager": self.who(_nz(app["sentry_manager"])),
            "sentries": [dict(s, who=self.who(s["address"])) for s in app["sentries"]],
            "input_count": live.get("input_count"),
            "stage": {"finished": stage[0], "failed": stage[1], "staged": stage[2], "epoch": stage[3],
                      "winner": _nz(stage[4])} if stage else None,
            "accept": {"staged": accept[0], "sentries_agree": accept[1], "period_over": accept[2],
                       "epoch": accept[3]} if accept else None,
            "epochs": [dict(v, checks=[{"name": c["name"], "verdict": c["verdict"]} for c in v["checks"]])
                       for v in reversed(views)],
            "slings": slings, "references": refs,
            "head": self.chain.head, "finalized": self.chain.finalized,
        }

    def epoch_detail(self, address: str, number: int) -> dict | None:
        app = self.chain.apps.get(address.lower())
        if not app:
            return None
        view = next((v for v in self.epoch_views(app) if v["number"] == number), None)
        if not view:
            return None
        tournaments = set()
        root = self.chain.tournaments.get(view["tournament"] or "")
        stack = [root] if root else []
        while stack:
            t = stack.pop()
            tournaments.add(t["address"])
            stack.extend(self.chain.tournaments[c] for c in t["children"])
        input_txs = {row["tx"] for row in self.epoch_inputs(app, view)}
        txs = []
        for entry in self.chain.ledgers.get(app["address"], ()):
            if (entry["to"] in tournaments or tournaments.intersection(entry.get("touched", ())) or self._consensus_epoch(entry) == number
                    or entry["hash"] in input_txs):
                txs.append(dict(entry, sender=self.who(entry["from"]), target=self.label(entry["to"])))
        return {
            "app": {"address": app["address"], "staging_period": app["staging_period"]},
            "epoch": view, "inputs": self.epoch_inputs(app, view), "dispute": self.dispute(app, view),
            "transactions": list(reversed(txs)), "head": self.chain.head, "finalized": self.chain.finalized,
        }

    def _consensus_epoch(self, entry: dict) -> int | None:
        # stageTournamentResult, submitSentryClaim and acceptStagedTournamentResult take the epoch first.
        if entry["function"] not in ("stageTournamentResult", "submitSentryClaim", "acceptStagedTournamentResult"):
            return None
        return entry.get("epoch_arg")

    def ledger(self, address: str) -> list[dict]:
        return [dict(e, sender=self.who(e["from"]), target=self.label(e["to"]))
                for e in reversed(self.chain.ledgers.get(address.lower(), ()))]
