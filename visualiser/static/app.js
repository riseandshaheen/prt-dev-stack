// PRT Dev Stack visualiser. Every node is built with textContent: chain data never becomes markup.
"use strict";

const main = document.getElementById("main");
const state = { overview: null, version: -1, lastOk: 0, connected: false, pending: false };

/* ------------------------------------------------------------ DOM helpers */

function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = value;
    else if (key === "style") el.style.cssText = value;  // CSSOM, allowed by the CSP
    else if (key === "vars") for (const [k, v] of Object.entries(value)) el.style.setProperty(k, v);
    else if (key.startsWith("on")) el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children);
  return el;
}
function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}
const svg = (tag, attrs, ...children) => {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs || {})) if (v !== null && v !== undefined) el.setAttribute(k, String(v));
  for (const c of children) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
};

const short = (v, keep = 6) => (v && v.length > keep * 2 + 4 ? `${v.slice(0, keep + 2)}…${v.slice(-4)}` : v || "");
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;
const fmtEth = (wei) => {
  if (wei == null) return "—";
  const v = BigInt(wei);
  if (v !== 0n && v < 10n ** 15n) return `${(Number(v) / 1e9).toLocaleString(undefined, { maximumFractionDigits: 2 })} gwei`;
  return `${(Number(v / 10n ** 12n) / 1e6).toLocaleString(undefined, { maximumFractionDigits: 4 })} ETH`;
};
const ago = (t) => {
  if (!t) return "never";
  const s = Math.max(0, Math.round(Date.now() / 1000 - t));
  return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
};

function copy(text, el) {
  const done = () => { el.classList.add("copied"); setTimeout(() => el.classList.remove("copied"), 900); };
  if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done); return; }
  const area = h("textarea", { style: "position:fixed;opacity:0" }, text);
  document.body.append(area); area.select(); document.execCommand("copy"); area.remove(); done();
}
function hash(value, keep = 6) {
  if (!value) return h("span", { class: "faint" }, "—");
  const el = h("button", { class: "hash", type: "button", title: `${value}\nClick to copy` }, short(value, keep));
  el.addEventListener("click", () => copy(value, el));
  return el;
}
function who(entity) {
  if (!entity || !entity.address) return h("span", { class: "faint" }, "—");
  return entity.label ? h("span", { class: "who" }, entity.label, h("small", {}, hash(entity.address, 4))) : hash(entity.address);
}
const block = (n) => (n === null || n === undefined ? h("span", { class: "faint" }, "—") : h("span", { class: "mono" }, n));

/* ------------------------------------------------------------ vocabulary */

const CHAIN_STATUS = {
  open: ["Collecting inputs", "s-open"],
  sealed: ["Sealed, awaiting claims", "s-sealed"],
  disputed: ["In dispute", "s-disputed"],
  staged: ["Result staged", "s-staged"],
  accepted: ["Accepted", "s-accepted"],
  decided: ["Winner decided", "s-work"],
  failed: ["No winner", "s-failed"],
  unknown: ["Unknown", "s-idle"],
};
function nodeClass(status) {
  if (!status) return "s-missing";
  if (["settled", "claim accepted"].includes(status)) return "s-ok";
  if (["computed", "claim computed", "claim submitted", "claim staged"].includes(status)) return "s-work";
  if (status === "not seen") return "s-missing";
  if (["claim rejected", "claim foreclosed", "failed"].includes(status)) return "s-bad";
  return "s-idle";
}
const chip = (label, cls) => h("span", { class: "chip" }, h("i", { class: cls }), label);
const statusChip = (s) => chip(...(CHAIN_STATUS[s] || [s, "s-idle"]));
const verdict = (v) => h("span", { class: `verdict ${v}` },
  v === "agree" ? "✓ Agree" : v === "diverge" ? "✕ Disagree" : "Pending");

function nodeMeta(id) {
  const nodes = (state.overview && state.overview.nodes) || [];
  const node = nodes.find((n) => n.id === id);
  const slings = nodes.filter((n) => n.kind === "sling").map((n) => n.id);
  const refs = nodes.filter((n) => n.kind === "reference").map((n) => n.id);
  const extra = ["var(--node-3)", "var(--node-4)"];
  let color = "var(--idle)";
  if (node && node.kind === "sling") color = slings.indexOf(id) === 0 ? "var(--sling)" : extra[slings.indexOf(id) % 2];
  if (node && node.kind === "reference") color = refs.indexOf(id) === 0 ? "var(--reference)" : extra[(refs.indexOf(id) + 1) % 2];
  return { label: node ? node.label : id, color, kind: node ? node.kind : "" };
}
const sourceLabel = (key) => (key === "chain" ? "Chain" : nodeMeta(key).label);
const sourceColor = (key) => (key === "chain" ? "var(--ink)" : nodeMeta(key).color);

/* ------------------------------------------------------------ data + routing */

async function api(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path} answered ${response.status}`);
  return response.json();
}

function route() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean);
  if (parts[0] === "app" && /^0x[0-9a-fA-F]{40}$/.test(parts[1] || "")) {
    if (parts[2] === "epoch" && /^\d+$/.test(parts[3] || "")) {
      return { view: "epoch", app: parts[1].toLowerCase(), epoch: Number(parts[3]), tab: parts[4] || null };
    }
    return { view: "app", app: parts[1].toLowerCase(), tab: parts[2] || "epochs" };
  }
  return { view: "overview" };
}

async function refresh() {
  if (state.pending) return;
  // Never yank the page from under someone copying a hash.
  if (String(window.getSelection && window.getSelection()).length) { setTimeout(refresh, 1000); return; }
  state.pending = true;
  const r = route();
  try {
    const overview = await api("/api/v1/overview");
    state.overview = overview;
    let page;
    if (r.view === "overview") page = viewOverview(overview);
    else if (r.view === "app") {
      const [app, ledger] = await Promise.all([api(`/api/v1/apps/${r.app}`),
        r.tab === "transactions" ? api(`/api/v1/apps/${r.app}/transactions`) : null]);
      page = viewApp(app, ledger, r.tab);
    } else {
      page = viewEpoch(await api(`/api/v1/apps/${r.app}/epochs/${r.epoch}`), r);
    }
    const y = window.scrollY;
    renderBar(overview);
    main.replaceChildren(page);
    window.scrollTo(0, y);
    state.lastOk = Date.now();
    document.title = titleFor(r, overview);
  } catch (err) {
    main.replaceChildren(h("div", { class: "empty" }, h("p", {}, `Could not load this view: ${err.message}.`),
      h("p", { class: "muted" }, "The visualiser keeps retrying. Check that the server is running.")));
  } finally {
    state.pending = false;
    staleCheck();
  }
}

function titleFor(r, ov) {
  const chain = ov && ov.chain ? `chain ${ov.chain.id}` : "PRT Dev Stack";
  if (r.view === "epoch") return `Epoch ${r.epoch} · ${chain}`;
  if (r.view === "app") return `${short(r.app, 4)} · ${chain}`;
  return `PRT Dev Stack · ${chain}`;
}

/* ------------------------------------------------------------ top bar */

function renderBar(ov) {
  const read = document.getElementById("chainread");
  const c = ov.chain;
  read.replaceChildren(
    h("span", {}, "Chain ", h("b", {}, c.id ?? "—")),
    h("span", {}, "Head ", h("b", {}, c.head)),
    h("span", {}, "Finalized ", h("b", {}, c.finalized)),
  );
  const sources = document.getElementById("sources");
  const chainOk = c.status && c.status.ok;
  const pills = [h("span", { class: `pill ${chainOk ? "live" : "down"}`, title: c.status && c.status.error ? c.status.error : c.rpc },
    h("i"), chainOk ? "Chain" : "Chain unreachable")];
  for (const n of ov.nodes) {
    const text = n.status === "down" ? `${n.label} down` : n.status === "off" ? `${n.label} off`
      : n.status === "lagging" ? `${n.label} ${n.lag} behind` : n.label;
    pills.push(h("span", { class: `pill ${n.status}`, title: n.error || `${n.endpoint}\nlast answer ${ago(n.last_ok_at)}` }, h("i"), text));
  }
  sources.replaceChildren(...pills);
}

function staleCheck() {
  const banner = document.getElementById("stale");
  const age = (Date.now() - state.lastOk) / 1000;
  const stale = !state.connected || age > 20;
  banner.classList.toggle("show", stale && state.lastOk > 0);
  banner.textContent = !state.connected
    ? "Live updates are disconnected. What you see may be out of date; reconnecting."
    : `No fresh data for ${Math.round(age)} seconds. The visualiser server may be stuck.`;
}

/* ------------------------------------------------------------ overview */

function viewOverview(ov) {
  const critical = ov.alerts.filter((a) => a.level === "critical").length;
  const warnings = ov.alerts.filter((a) => a.level === "warning").length;
  // Only say something under the heading when there is something to act on.
  const lead = ov.apps.length === 0
    ? "No applications found yet. Register one with a node, or list it under apps in the config."
    : critical || warnings
      ? [critical ? plural(critical, "critical problem") : null, warnings ? plural(warnings, "warning") : null]
          .filter(Boolean).join(", ") + "."
      : null;
  return h("div", {},
    h("div", { class: "head" }, h("h1", {}, `Applications (${ov.apps.length})`)),
    lead ? h("p", { class: critical ? "bad" : "muted", style: "margin:-8px 0 18px" }, lead) : null,
    ov.alerts.length ? h("ul", { class: "alerts" }, ov.alerts.slice(0, 12).map(alertItem)) : null,
    h("section", {}, h("div", { class: "apps" }, ov.apps.map((app) => appRibbon(app, ov))), legend()),
    h("section", {}, h("div", { class: "head" }, h("h2", {}, "Nodes")), nodesTable(ov.nodes)),
    requirements(ov),
  );
}

function requirements(ov) {
  const endpoint = (kind) => ov.nodes.filter((n) => n.kind === kind).map((n) => h("code", {}, n.endpoint)).flatMap((c, i) => i ? [", ", c] : [c]);
  const item = (title, ...body) => h("li", {}, h("b", {}, title), " ", ...body);
  return h("section", { class: "requirements" },
    h("h2", {}, "What this visualiser expects"),
    h("p", { class: "muted" }, "It is built for the prt-dev-stack setup, not as a general explorer. It works when:"),
    h("ul", {},
      item("One Anvil chain:", "the Dave v3 devnet loaded from ", h("code", {}, "anvil/state.json"), ", reached at ", h("code", {}, ov.chain.rpc || "—"),
        ". Contracts, the Dave app factory and the account labels (Anvil 0, 6, 7) come from that dump."),
      item("Sling:", "cartesi-rollups-prt-node from dave v3.0.0-alpha.5, whose SQLite state is read through ", endpoint("sling").length ? endpoint("sling") : "a configured db path or container",
        ". On the host this needs the docker CLI; the compose service mounts the prt-sling_sling-state volume instead."),
      item("Reference node:", "the rollups-node feature/contracts-bump snapshot, read over its JSON-RPC at ", endpoint("reference").length ? endpoint("reference") : "a configured rpc URL", "."),
      item("Matching contract ABI:", "events and errors come from ", h("code", {}, "vis/abi.json"),
        ", generated from the rollups-node bindings. Regenerate it with ", h("code", {}, "tools/gen_abi.py"), " when the contracts change."),
      item("Python 3.10 or later", "on the host, with nothing else to install.")),
    h("p", { class: "faint" }, "A chain reset or redeployed stack is detected and every cache is rebuilt, but a different chain, other node versions or other contracts may show wrong data rather than an error."));
}

function alertItem(a) {
  const link = a.app ? h("a", { href: a.epoch !== undefined ? `#/app/${a.app}/epoch/${a.epoch}` : `#/app/${a.app}` },
    a.epoch !== undefined ? `Open epoch ${a.epoch}` : "Open app") : h("span");
  return h("li", { class: a.level }, h("span", {}, a.text, a.at ? h("span", { class: "faint" }, ` · ${ago(a.at)}`) : null), link);
}

// A node is behind on an epoch when the chain has moved past what the node reports:
// it failed, it has not seen a sealed epoch, or it has not settled an accepted one.
function nodeBehind(e, status) {
  const cls = nodeClass(status);
  if (cls === "s-bad") return "failed";
  if (e.status === "open") return null;
  if (cls === "s-missing") return "behind";
  if (e.status === "accepted" && cls !== "s-ok") return "behind";
  return null;
}

function nodeSummary(app, ov) {
  return h("p", { class: "nodeline" }, app.nodes.map((id) => {
    const meta = nodeMeta(id);
    const node = (ov.nodes || []).find((n) => n.id === id) || {};
    const first = app.epochs.find((e) => nodeBehind(e, e.nodes[id]));
    const how = first && nodeBehind(first, first.nodes[id]);
    const text = node.status === "down" ? "down" : node.status === "off" ? "off"
      : first ? (how === "failed" ? `failed at epoch ${first.number}` : `behind from epoch ${first.number}`)
      : node.status === "lagging" ? `${node.lag} blocks behind` : "✓";
    return h("span", { vars: { "--node": meta.color }, class: text === "✓" || text === "off" ? null : "lagging" }, meta.label, " ", text);
  }));
}

function appRibbon(app, ov) {
  const nodes = app.nodes;
  const lanes = h("div", { class: "lanes" }, h("span", {}, "Chain"));
  const cols = h("div", { class: "cols" }, app.epochs.map((e) => {
    const [label, cls] = CHAIN_STATUS[e.status] || [e.status, "s-idle"];
    const behind = nodes.filter((id) => nodeBehind(e, e.nodes[id]));
    const tip = [`Epoch ${e.number}: ${label}`, plural(e.input_count, "input"),
      e.commitment_count > 1 ? `${e.commitment_count} commitments` : null,
      ...nodes.map((id) => `${nodeMeta(id).label}: ${e.nodes[id] || "not seen"}`),
      e.agreement === "diverge" ? "Nodes disagree" : null].filter(Boolean).join("\n");
    return h("a", {
      class: `col${e.current ? " current" : ""}${e.agreement === "diverge" ? " diverge" : ""}`,
      href: `#/app/${app.address}/epoch/${e.number}`, title: tip, "aria-label": tip,
    }, h("span", { class: "n" }, e.number), h("span", { class: `cell ${cls}` }),
    behind.length ? h("span", { class: "behind" }) : null);
  }));
  const hidden = app.epoch_total - app.epochs.length;
  return h("article", { class: "app" },
    h("header", {},
      h("a", { href: `#/app/${app.address}` }, app.name || "Unnamed app"),
      hash(app.address, 4),
      h("span", { class: "facts" },
        h("span", {}, `Epoch ${app.current_epoch ?? "—"} sealed`),
        h("span", {}, plural(app.input_count ?? 0, "input")),
        nodes.length ? null : h("span", { class: "bad" }, "No node follows this app"))),
    h("div", { class: "ribbon" }, lanes, cols),
    nodes.length ? nodeSummary(app, ov) : null,
    hidden > 0 ? h("p", { class: "faint", style: "margin-top:8px;font-size:var(--step--1)" },
      `Showing the latest ${app.epochs.length} of ${app.epoch_total} epochs. `, h("a", { href: `#/app/${app.address}` }, "See all")) : null,
  );
}

function legend() {
  const row = (title, items) => h("div", { class: "legend" }, h("b", {}, title),
    items.map(([t, c]) => h("span", {}, h("i", { class: c }), t)));
  return h("div", { class: "legends" },
    row("Chain lane", [["Collecting inputs", "s-open"], ["Sealed", "s-sealed"], ["In dispute", "s-disputed"],
      ["Winner decided", "s-work"], ["Staged", "s-staged"], ["Accepted", "s-accepted"], ["No winner", "s-failed"]]),
    h("div", { class: "legend" }, h("span", {}, h("i", { style: "box-shadow:inset 0 0 0 1px var(--bad)" }),
      "Chain and nodes disagree"),
      h("span", {}, h("i", { class: "dot" }), "A node is behind the chain")));
}

function nodesTable(nodes) {
  if (!nodes.length) return h("div", { class: "empty" }, "No nodes are configured. Add them under nodes in the config file.");
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Node", "Status", "Processed block", "Behind finalized", "Version", "Last answer", "Source"].map((t, i) =>
      h("th", { class: i === 2 || i === 3 ? "num" : null }, t)))),
    h("tbody", {}, nodes.map((n) => h("tr", { class: n.status === "down" ? "flag" : null },
      h("td", {}, h("span", { style: `border-left:3px solid ${nodeMeta(n.id).color};padding-left:8px` }, n.label),
        h("div", { class: "faint" }, n.kind === "sling" ? "Sling (dave)" : "Reference (rollups-node)")),
      h("td", {}, n.status === "down" ? h("span", { class: "bad" }, "Down: ", n.error) :
        n.status === "lagging" ? h("span", { style: "color:var(--dispute)" }, "Lagging") : h("span", { class: "ok" }, "Live")),
      h("td", { class: "num" }, n.processed_block ?? "—"),
      h("td", { class: "num" }, n.lag ?? "—"),
      h("td", {}, n.version || "—"),
      h("td", {}, ago(n.last_ok_at), n.poll_ms != null ? h("div", { class: "faint" }, `${n.poll_ms} ms to read`) : null),
      h("td", {}, h("code", {}, n.endpoint || "—"))))),
  ));
}

/* ------------------------------------------------------------ app page */

function viewApp(app, ledger, tab) {
  const current = app.epochs.find((e) => e.current);
  return h("div", {},
    h("nav", { class: "crumbs" }, h("a", { href: "#/" }, "Overview"), h("span", {}, app.name || short(app.address, 4))),
    h("div", { class: "head" }, h("h1", {}, app.name || "Unnamed app"), hash(app.address, 8)),
    settlementLine(app, current),
    h("section", {}, facts([
      ["Consensus", hash(app.consensus)], ["Input box", hash(app.input_box)],
      ["Template hash", hash(app.template_hash)], ["Deployed at block", block(app.deploy_block)],
      ["Claim staging period", `${app.staging_period} blocks`], ["Root join window", app.root_allowance ? `${app.root_allowance} blocks` : "—"],
      ["Sentries", app.sentries.length ? h("span", {}, app.sentries.map((s) => who(s.who))) : "None"],
      ["Sentry manager", app.sentry_manager ? who(app.sentry_manager) : "None, so sentries cannot rotate"],
      ["Inputs on chain", app.input_count ?? "—"],
    ])),
    nodeFacts(app),
    h("nav", { class: "tabs" },
      h("a", { href: `#/app/${app.address}`, "aria-current": tab !== "transactions" ? "page" : null }, "Epochs", h("span", { class: "count" }, app.epochs.length)),
      h("a", { href: `#/app/${app.address}/transactions`, "aria-current": tab === "transactions" ? "page" : null }, "Transactions")),
    tab === "transactions" ? ledgerTable(ledger || [], true) : epochsTable(app),
  );
}

function settlementLine(app, e) {
  if (!e) return null;
  const parts = [];
  const head = app.head;
  if (e.status === "staged") {
    const left = e.staging_blocks_left;
    parts.push(`Epoch ${e.number}'s result is staged. It can be accepted `,
      app.accept && app.accept.sentries_agree ? "now: the sentries agree." :
        left > 0 ? `in ${plural(left, "block")} (block ${e.staging_ends_at}), or sooner if every sentry agrees.` : "now.");
  } else if (e.status === "decided") {
    parts.push(`Epoch ${e.number}'s tournament has a winner. The result can be staged.`);
  } else if (e.status === "disputed" || e.status === "sealed") {
    const close = e.join_window_closes_at;
    parts.push(`Epoch ${e.number} is ${e.status === "disputed" ? "in dispute" : "sealed"}. `,
      close ? (close > head ? `Joins close at block ${close}, ${plural(close - head, "block")} from now.` : `Joins closed at block ${close}.`) : "",
      app.stage && app.stage.finished ? (app.stage.failed ? " The tournament ended with no winner." : " The tournament has a winner and can be staged.") : "");
  } else if (e.status === "failed") {
    parts.push(`Epoch ${e.number}'s tournament ended with no winner.`);
  }
  return parts.length ? h("p", { class: "muted", style: "margin:-6px 0 22px" }, parts) : null;
}

function facts(rows) {
  return h("dl", { class: "facts-grid" }, rows.map(([k, v]) => h("div", {}, h("dt", {}, k), h("dd", {}, v))));
}

function nodeFacts(app) {
  const rows = [];
  for (const s of app.slings) rows.push([s.id, [
    ["Claimant", who(s.claimant)], ["Version", s.node_version || "—"], ["Emulator", s.emulator || "—"],
    ["Template", s.template_hash && s.template_hash === app.template_hash ? h("span", { class: "ok" }, "Matches chain")
      : h("span", { class: "bad" }, "Differs: ", hash(s.template_hash))],
    ["Next epoch to finish", s.next_epoch ?? "—"],
  ]]);
  for (const r of app.references) rows.push([r.id, [
    ["State", r.state === "ENABLED" || !r.state ? h("span", { class: "ok" }, r.state || "—") : h("span", { class: "bad" }, r.state)],
    ["Reason", r.reason || "—"], ["Registered as", r.name || "—"], ["Processed inputs", r.processed_inputs ?? "—"],
    ["Checked up to block", `inputs ${r.last_input_check_block ?? "—"}, epochs ${r.last_epoch_check_block ?? "—"}, tournaments ${r.last_tournament_check_block ?? "—"}`],
  ]]);
  if (!rows.length) return h("section", {}, h("div", { class: "empty" }, "No configured node follows this application."));
  return h("section", {}, h("div", { class: "head" }, h("h2", {}, "How each node sees it")),
    h("div", { class: "checks" }, rows.map(([id, items]) => h("div", { class: "check", vars: { "--node": nodeMeta(id).color }, style: `border-left-color:${nodeMeta(id).color}` },
      h("h3", {}, nodeMeta(id).label), h("dl", {}, items.map(([k, v]) => [h("dt", { style: "border:0;padding:0" }, k), h("dd", {}, v)]))))));
}

function epochsTable(app) {
  const nodeIds = [...app.slings.map((s) => s.id), ...app.references.map((r) => r.id)];
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Epoch"), h("th", {}, "Status"), h("th", { class: "num" }, "Inputs"),
      h("th", { class: "num" }, "Sealed"), h("th", { class: "num" }, "Staged"), h("th", { class: "num" }, "Accepted"),
      h("th", {}, "Dispute"), nodeIds.map((id) => h("th", { class: "node", vars: { "--node": nodeMeta(id).color } }, nodeMeta(id).label)),
      h("th", {}, "Agreement"))),
    h("tbody", {}, app.epochs.map((e) => h("tr", { class: e.agreement === "diverge" ? "flag" : null },
      h("td", { class: "num" }, h("a", { href: `#/app/${app.address}/epoch/${e.number}` }, e.number)),
      h("td", {}, statusChip(e.status), e.staging_blocks_left ? h("div", { class: "faint" }, `${e.staging_blocks_left} blocks left`) : null),
      h("td", { class: "num" }, e.upper === null ? `${e.input_count}+` : e.input_count),
      h("td", { class: "num" }, e.sealed ? e.sealed.block : "—"),
      h("td", { class: "num" }, e.staged ? e.staged.block : "—"),
      h("td", { class: "num" }, e.accepted ? e.accepted.block : "—"),
      h("td", {}, e.commitment_count ? `${plural(e.commitment_count, "commitment")}, ${plural(e.match_count, "match")}`.replace("matchs", "matches") : h("span", { class: "faint" }, "—")),
      nodeIds.map((id) => h("td", {}, e.nodes[id] ? chip(e.nodes[id].status, nodeClass(e.nodes[id].status)) : h("span", { class: "faint" }, "—"))),
      h("td", {}, verdict(e.agreement)))))));
}

/* ------------------------------------------------------------ epoch page */

function viewEpoch(data, r) {
  const e = data.epoch;
  const tabs = [];
  if (data.dispute) tabs.push(["dispute", "Dispute", data.dispute.commitments.length]);
  tabs.push(["inputs", "Inputs", data.inputs.length]);
  if (e.checks.length) tabs.push(["agreement", "Agreement", null]);
  tabs.push(["transactions", "Transactions", data.transactions.length]);
  const tab = tabs.some(([k]) => k === r.tab) ? r.tab : (e.disputed ? "dispute" : e.agreement === "diverge" ? "agreement" : "inputs");
  const base = `#/app/${data.app.address}/epoch/${e.number}`;
  let body;
  if (tab === "dispute") body = tournament(data.dispute, data.head, true);
  else if (tab === "agreement") body = checksView(e);
  else if (tab === "transactions") body = ledgerTable(data.transactions, false);
  else body = inputsTable(data.inputs, e);
  return h("div", {},
    h("nav", { class: "crumbs" }, h("a", { href: "#/" }, "Overview"),
      h("span", {}, h("a", { href: `#/app/${data.app.address}` }, short(data.app.address, 4))), h("span", {}, `Epoch ${e.number}`)),
    h("div", { class: "head" }, h("h1", {}, `Epoch ${e.number}`), statusChip(e.status), e.checks.length ? verdict(e.agreement) : null),
    epochSummary(e, data),
    h("nav", { class: "tabs" }, tabs.map(([key, label, count]) => h("a", { href: `${base}/${key}`, "aria-current": key === tab ? "page" : null },
      label, count !== null ? h("span", { class: "count" }, count) : null))),
    body,
  );
}

function epochSummary(e, data) {
  const range = e.upper === null ? `from ${e.lower}, still open`
    : e.input_count === 0 ? "None" : `${e.lower} to ${e.upper - 1} (${plural(e.input_count, "input")})`;
  const rows = [["Inputs", range]];
  if (e.sealed) rows.push(["Sealed", h("span", {}, `block ${e.sealed.block} by `, who(e.sealed.by))]);
  if (e.join_window_closes_at) rows.push(["Joins close", `block ${e.join_window_closes_at}${e.join_window_closes_at > data.head ? `, in ${plural(e.join_window_closes_at - data.head, "block")}` : ""}`]);
  if (e.staged) rows.push(["Staged", h("span", {}, `block ${e.staged.block} by `, who(e.staged.by))]);
  if (e.staging_ends_at && !e.accepted) rows.push(["Staging period ends", `block ${e.staging_ends_at}, in ${plural(e.staging_blocks_left, "block")}`]);
  if (e.accepted) rows.push(["Accepted", h("span", {}, `block ${e.accepted.block} by `, who(e.accepted.by))]);
  for (const c of e.sentry_claims) rows.push([c.sentry_id != null ? `Sentry ${c.sentry_id} claim` : "Sentry claim", h("span", {}, who(c.sentry), " ", hash(c.state),
    c.agrees === false ? h("span", { class: "bad" }, " disagrees with the staged result") : c.agrees ? h("span", { class: "ok" }, " agrees") : "")]);
  if (e.tournament) rows.push(["Root tournament", hash(e.tournament)]);
  return facts(rows);
}

function checksView(e) {
  return h("div", { class: "checks" }, e.checks.map((c) => {
    const values = Object.entries(c.values);
    const counts = {};
    for (const [, v] of values) if (v) counts[String(v).toLowerCase()] = (counts[String(v).toLowerCase()] || 0) + 1;
    // Only single out a value when a strict majority disagrees with it; a 1-1 split accuses nobody.
    const ranked = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    const majority = ranked.length > 1 && ranked[0][1] > ranked[1][1] ? ranked[0] : null;
    return h("div", { class: `check ${c.verdict}` },
      h("h3", {}, c.name, verdict(c.verdict)),
      h("dl", {}, values.map(([k, v]) => [
        h("dt", { vars: { "--node": sourceColor(k) } }, sourceLabel(k)),
        h("dd", { class: c.verdict === "diverge" && v && majority && String(v).toLowerCase() !== majority[0] ? "odd" : null },
          v ? (String(v).startsWith("0x") ? hash(v, 10) : v) : h("span", { class: "faint" }, "not known yet"))])));
  }));
}

function inputsTable(rows, e) {
  if (!rows.length) return h("div", { class: "empty" }, e.status === "open" ? "No inputs yet. Inputs sent now land in this epoch." : "This epoch has no inputs.");
  const nodeIds = Object.keys(rows[0].nodes);
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Input"), h("th", { class: "num" }, "In epoch"), h("th", { class: "num" }, "Block"),
      h("th", {}, "Sender"), h("th", {}, "Payload"), h("th", { class: "num" }, "Bytes"),
      nodeIds.map((id) => h("th", { class: "node", vars: { "--node": nodeMeta(id).color } }, nodeMeta(id).label)))),
    h("tbody", {}, rows.map((row) => {
      const bad = Object.values(row.nodes).some((n) => n.status === "differs" || n.status === "missing" || n.wrong_epoch !== undefined);
      return h("tr", { class: bad && e.status !== "open" ? "flag" : null },
        h("td", { class: "num" }, row.index), h("td", { class: "num" }, row.index_in_epoch),
        h("td", { class: "num" }, h("span", { title: row.tx }, row.block)),
        h("td", {}, who(row.sender)),
        h("td", {}, row.text ? h("span", { title: row.text }, row.text.length > 80 ? row.text.slice(0, 80) + "…" : row.text) : hash(row.hex, 12)),
        h("td", { class: "num" }, row.size),
        nodeIds.map((id) => {
          const n = row.nodes[id];
          const cls = ["stored", "accepted"].includes(n.status) ? "s-ok" : ["differs", "missing"].includes(n.status) ? "s-bad" : n.status === "none" ? "s-idle" : "s-work";
          const label = n.status === "none" ? "not processed" : n.status;
          return h("td", {}, chip(label, cls),
            n.outputs !== undefined && (n.outputs || n.reports) ? h("div", { class: "faint" }, `${plural(n.outputs, "output")}, ${plural(n.reports, "report")}`) : null,
            n.wrong_epoch !== undefined ? h("div", { class: "bad" }, `placed in epoch ${n.wrong_epoch}`) : null);
        }));
    }))));
}

/* ------------------------------------------------------------ dispute */

function tournament(t, head, isRoot) {
  if (!t) return h("div", { class: "empty" }, "No tournament for this epoch yet.");
  const honest = t.commitments.filter((c) => c.side === "honest").length;
  const lead = isRoot ? disputeLead(t, head, honest) : null;
  const w = t.window;
  return h("article", { class: "tourney" },
    h("header", {},
      h("h2", {}, isRoot ? "Root tournament" : `Inner tournament, level ${t.level}`),
      h("span", { class: "facts" },
        h("span", {}, t.standing ? t.standing[0].toUpperCase() + t.standing.slice(1) : "Standing unknown"),
        h("span", {}, t.accepts_joins ? "Accepting joins" : "Joins closed"),
        w ? h("span", {}, `Window ${w.start}–${w.close}, height ${w.height}, ${w.kind}`) : null,
        t.bond && t.bond.value ? h("span", {}, `Bond ${fmtEth(t.bond.value)}, ${t.bond.disposition}`) : null,
        hash(t.address, 4))),
    lead,
    track(t, head),
    h("h3", { style: "margin:6px 0 10px" }, plural(t.commitments.length, "commitment")),
    t.commitments.length ? h("div", { class: "commitments" }, t.commitments.map((c) => commitment(c))) : h("p", { class: "muted" }, "Nobody has joined yet."),
    t.matches.length ? h("h3", { style: "margin:18px 0 4px" }, plural(t.matches.length, "match").replace("matchs", "matches")) : null,
    t.matches.map((m, i) => match(m, i, t, head)),
    t.bonds.length || t.refunds.length ? h("div", { style: "margin-top:14px" }, bonds(t)) : null,
  );
}

function disputeLead(t, head, honest) {
  if (t.standing === "root winner" && t.candidate) {
    const w = t.commitments.find((c) => c.root === t.candidate);
    const name = w && w.submitter ? (w.submitter.label || short(w.submitter.address, 4)) : short(t.candidate);
    return w && w.side === "rival"
      ? h("p", { class: "bad", style: "margin-bottom:10px;font-weight:600" }, `${name} won, and its commitment matches no node. The honest commitment lost.`)
      : h("p", { class: "ok", style: "margin-bottom:10px" }, `${name} won${w && w.side === "honest" ? ", and it matches what the nodes computed" : ""}.`);
  }
  if (t.commitments.length <= 1) return h("p", { class: "muted", style: "margin-bottom:10px" },
    t.commitments.length ? "One commitment and no challenger, so no matches are needed." : "Waiting for the first commitment.");
  const active = t.matches.filter((m) => !m.deleted).length;
  const text = [`${plural(t.commitments.length, "commitment")} joined; ${honest ? `${honest} match what the nodes computed` : "none match what the nodes computed yet"}.`,
    active ? ` ${plural(active, "match")} still running.`.replace("matchs", "matches") : " All matches have ended."];
  return h("p", { style: "margin-bottom:10px" }, text);
}

function track(t, head) {
  const rows = t.matches;
  const w = t.window;
  const starts = [t.created_block, w ? w.start : null].filter((x) => x !== null);
  const ends = [head, w ? w.close : null, ...rows.map((m) => m.eliminable_at || 0), ...rows.map((m) => (m.deleted ? m.deleted.block : 0))];
  const x0 = Math.min(...starts);
  const x1 = Math.max(...ends) + 2;
  const width = 1000, left = 64, right = 16, rowH = 22, top = 34;
  const height = top + Math.max(1, rows.length) * rowH + 18;
  const X = (b) => left + ((b - x0) / Math.max(1, x1 - x0)) * (width - left - right);
  const g = svg("svg", { viewBox: `0 0 ${width} ${height}`, role: "img", "aria-label": `Tournament timeline from block ${x0} to ${x1}` });
  if (w) g.append(svg("rect", { class: "window", x: X(w.start), y: top - 6, width: Math.max(1, X(w.close) - X(w.start)), height: height - top - 6 }));
  g.append(svg("line", { class: "axis", x1: left, x2: width - right, y1: height - 12, y2: height - 12 }));
  const step = Math.max(1, Math.ceil((x1 - x0) / 6 / 10) * 10);
  for (let b = Math.ceil(x0 / step) * step; b <= x1; b += step) {
    g.append(svg("text", { x: X(b), y: height - 1, "text-anchor": "middle" }, b));
  }
  const clash = w && Math.abs(X(w.close) - X(head)) < 150;   // labels would overlap: put "now" on its own line
  if (w) g.append(svg("text", { x: X(w.close), y: 11, "text-anchor": "end" }, `joins close ${w.close}`));
  rows.forEach((m, i) => {
    const y = top + i * rowH;
    const end = m.deleted ? m.deleted.block : head;
    g.append(svg("text", { x: 4, y: y + 12 }, `match ${i + 1}`));
    g.append(svg("rect", { class: `bar${m.deleted ? " ended" : ""}`, x: X(m.created_block ?? x0), y, width: Math.max(2, X(end) - X(m.created_block ?? x0)), height: 14, rx: 2 }));
    for (const a of m.advances) g.append(svg("line", { class: "tick", x1: X(a), x2: X(a), y1: y, y2: y + 14 }));
    if (!m.deleted && m.eliminable_at) {
      const x = X(m.eliminable_at);
      g.append(svg("path", { class: "deadline", d: `M${x} ${y + 1} l6 6 l-6 6 l-6 -6 z` }, svg("title", {}, `eliminable at block ${m.eliminable_at}`)));
    }
  });
  g.append(svg("line", { class: "now", x1: X(head), x2: X(head), y1: top - 10, y2: height - 12 }));
  g.append(svg("text", { x: X(head) + 4, y: clash ? 23 : 11 }, `now ${head}`));
  return h("div", { class: "track" }, g);
}

function commitment(c) {
  const side = c.side === "honest" ? `Honest, matches ${c.matches_nodes.map((id) => nodeMeta(id).label).join(" and ")}`
    : c.side === "rival" ? "Rival, matches no node" : "Not classified";
  const clock = c.clock
    ? c.clock.running
      ? c.clock.blocks_left <= 0
        ? h("span", { class: "clock bad" }, `Clock ran out at block ${c.clock.deadline}`)
        : h("span", { class: "clock running" }, "Clock running, ", h("b", {}, c.clock.blocks_left), c.clock.blocks_left === 1 ? " block left" : " blocks left")
      : h("span", { class: "clock" }, "Clock paused, ", h("b", {}, c.clock.blocks_left), c.clock.blocks_left === 1 ? " block banked" : " blocks banked")
    : h("span", { class: "clock faint" }, "Clock unknown");
  return h("div", { class: `commit ${c.side}${c.eliminated_at ? " out" : ""}` },
    h("div", {}, h("div", { class: "side" }, side), h("div", { class: "faint" }, "root ", hash(c.root))),
    h("div", {}, h("div", { class: "faint" }, "Joined by"), who(c.submitter)),
    h("div", {}, h("div", { class: "faint" }, "Final state"), hash(c.final_state)),
    h("div", {}, c.eliminated_at ? h("span", { class: "bad" }, `Eliminated at block ${c.eliminated_at}`) : clock,
      h("div", { class: "faint" }, `joined at block ${c.block}`)));
}

function match(m, i, t, head) {
  const byRoot = Object.fromEntries(t.commitments.map((c) => [c.root, c]));
  const name = (root) => {
    const c = byRoot[root];
    return h("span", { class: c ? (c.side === "honest" ? "ok" : c.side === "rival" ? "bad" : "") : "" }, c && c.submitter ? (c.submitter.label || short(c.submitter.address, 4)) : hash(root));
  };
  let status;
  if (m.deleted) {
    const winner = m.deleted.winner === "none" ? "neither side" : name(m.deleted.winner_root);
    status = h("span", {}, `Ended by ${m.deleted.reason} at block ${m.deleted.block}; `, winner, " won");
  } else if (m.phase === "bisecting" && m.height_left !== null && m.tree_height) {
    const done = m.tree_height - m.height_left;
    status = h("span", {}, `Bisecting: ${done} of ${m.tree_height} levels done`,
      m.blocks_to_eliminable !== null ? `; ${m.blocks_to_eliminable > 0 ? `eliminable in ${plural(m.blocks_to_eliminable, "block")}` : "eliminable now"}` : "");
  } else {
    status = h("span", {}, m.phase ? m.phase[0].toUpperCase() + m.phase.slice(1) : "Phase unknown");
  }
  const meter = !m.deleted && m.tree_height ? h("div", { class: "meter", title: "Bisection progress" },
    Array.from({ length: Math.min(m.tree_height, 64) }, (_, k) => h("span", { class: k < Math.round(((m.tree_height - (m.height_left ?? m.tree_height)) / m.tree_height) * Math.min(m.tree_height, 64)) ? "done" : "" }))) : null;
  return h("div", { class: "match" },
    h("div", { class: "pair" }, h("b", {}, `Match ${i + 1}`), name(m.one), h("span", { class: "faint" }, "against"), name(m.two),
      h("span", { class: "faint" }, `created at block ${m.created_block ?? "?"}, ${plural(m.advances.length, "bisection")}`)),
    h("div", {}, status, m.waiting_on ? h("span", { class: m.waiting_on.side === "honest" ? "turn honest" : "turn" },
      " · waiting on ", m.waiting_on.who ? (m.waiting_on.who.label || short(m.waiting_on.who.address, 4)) : short(m.waiting_on.root, 4),
      m.waiting_on.side === "honest" ? " (honest)" : m.waiting_on.side === "rival" ? " (rival)" : "",
      `, ${plural(m.waiting_on.blocks_left, "block")} left`) : null), meter,
    m.child ? tournament(m.child, head, false) : null);
}

function bonds(t) {
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Block", "Kind", "To", "Amount", "Detail"].map((x) => h("th", {}, x)))),
    h("tbody", {},
      t.bonds.map((b) => h("tr", {}, h("td", { class: "num" }, b.block), h("td", {}, "Bond recovered"), h("td", {}, who(b.claimer)),
        h("td", {}, fmtEth(b.payment)), h("td", {}, `burned ${fmtEth(b.burned)}`))),
      t.refunds.map((r) => h("tr", {}, h("td", { class: "num" }, r.block), h("td", {}, "Gas refund"), h("td", {}, who(r.recipient)),
        h("td", {}, fmtEth(r.value)), h("td", {}, r.success ? "paid" : h("span", { class: "bad" }, "failed")))))));
}

/* ------------------------------------------------------------ ledger */

function ledgerTable(rows, showEpoch) {
  if (!rows.length) return h("div", { class: "empty" }, "No transactions to these contracts yet.");
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", { class: "num" }, "Block"), h("th", {}, "From"), h("th", {}, "Call"), h("th", {}, "To"),
      h("th", {}, "Result"), h("th", { class: "num" }, "Gas"), h("th", {}, "Transaction"))),
    h("tbody", {}, rows.map((t) => h("tr", { class: t.status === "reverted" ? "flag" : null },
      h("td", { class: "num" }, t.block), h("td", {}, who(t.sender)),
      h("td", {}, h("code", {}, t.function || "unknown"), t.epoch_arg !== null && t.epoch_arg !== undefined ? h("span", { class: "faint" }, ` epoch ${t.epoch_arg}`) : null),
      h("td", {}, t.target || hash(t.to, 4),
        t.via ? h("div", { class: "faint", title: "Sent to another contract, which called the app's contracts" }, "via contract call") : null),
      h("td", {}, t.status === "ok" ? h("span", { class: "ok" }, "Succeeded") : h("span", { class: "bad" }, "Reverted: ", t.revert || "no reason")),
      h("td", { class: "num" }, t.gas_used ?? "—"), h("td", {}, hash(t.hash, 4)))))));
}

/* ------------------------------------------------------------ live updates */

function connect() {
  if (!window.EventSource) { setInterval(refresh, 4000); state.connected = true; return; }
  const source = new EventSource("/api/v1/stream");
  source.onopen = () => { state.connected = true; staleCheck(); };
  source.onmessage = (msg) => {
    const { version } = JSON.parse(msg.data);
    if (version !== state.version) { state.version = version; refresh(); }
  };
  source.onerror = () => { state.connected = false; staleCheck(); };
}

window.addEventListener("hashchange", () => { refresh(); window.scrollTo(0, 0); });
setInterval(staleCheck, 5000);
connect();
refresh();
