// Pathfinder HUD — orb-centric interaction model ported from the approved
// design prototype (Pathfinder_HUD.html). See docs/UI_SPEC.md section 0.
//
// State machine: mode 'core' (orb only) <-> 'expanded' (panel cluster).
// In expanded mode, `focus === null` shows the default pair (What's Next +
// Major Updates); focus === <id> shows exactly one panel full-width — this
// is how Recent Cases / Chronology / Systems are reached (click their
// mini-tab, or a case row to jump into its Chronology).
//
// Falls back to mock data when run outside Tauri (plain browser preview).

const isTauri = "__TAURI__" in window;
const DEFAULT_FOCUS_SET = ["whatsnext", "updates"];

const state = {
  mode: "core",
  focus: null,
  panels: { recent_cases: [], major_updates: [], whats_next: [] },
  health: null,
  agentConnected: false,
  selectedCaseId: null,
  chronology: [],
  now: Date.now(),
};

const MOCK_PANELS = {
  recent_cases: [
    { case_id: "FIR 201-25", title: "State v. Okafor", fir_number: "201-25",
      last_event: "Document on record: statement 14.06.25.pdf", last_updated_ms: Date.now() - 3600e3 },
    { case_id: "FIR 348-26", title: "FIR 348-26", fir_number: "348-26",
      last_event: "Document on record: fir copy 01.02.26.pdf", last_updated_ms: Date.now() - 86400e3 },
  ],
  major_updates: [
    { severity: "urgent", case_id: "FIR 201-25", title: "Court deadline closing — ISP preservation",
      body: "2024/CR/0457 · expires soon", timestamp_ms: Date.now() + 68 * 60e3,
      detected_ms: Date.now() - 4 * 60e3, source_file: "FIR_0457.pdf", source_page: 3 },
    { severity: "warning", case_id: "FIR 201-25", title: "3 exhibits ingested — not yet linked",
      body: "needs officer review", timestamp_ms: 0,
      detected_ms: Date.now() - 3600e3, source_file: "", source_page: 0 },
  ],
  whats_next: [
    { rank: 1, case_id: "FIR 201-25", action: "File ISP preservation request",
      reason: "2024/CR/0457 · State v. Okafor", due_ms: Date.now() + 68 * 60e3,
      source_file: "FIR_0457.pdf", source_page: 3 },
    { rank: 2, case_id: "FIR 201-25", action: "Review chargesheet draft",
      reason: "2024/CR/0419", due_ms: Date.now() + 28 * 3600e3,
      source_file: "draft_chargesheet.docx", source_page: 12 },
  ],
};
const MOCK_HEALTH = {
  running: true, llm: "ollama", llm_available: true,
  watched_folders: ["_sample"], workflows: ["isp_letter"],
};

// ── formatting helpers ──────────────────────────────────────────────────────
const pad = (n) => String(n).padStart(2, "0");

function fmtDueIn(dueMs, now) {
  if (!dueMs) return null;
  const diff = dueMs - now;
  if (diff <= 0) return { text: "OVERDUE", cls: "live" };
  const totalSec = Math.floor(diff / 1000);
  const days = Math.floor(totalSec / 86400);
  if (days < 2) {
    const h = Math.floor((totalSec % 86400) / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    return { text: `T-${pad(h)}:${pad(m)}:${pad(s)}`, cls: "live" };
  }
  const remH = Math.floor((totalSec % 86400) / 3600);
  return { text: remH ? `${days}d ${pad(remH)}h` : `${days}d`, cls: days <= 3 ? "soon" : "later" };
}

function fmtAgo(ms, now) {
  if (!ms) return "";
  const diffMin = Math.max(0, Math.floor((now - ms) / 60000));
  if (diffMin < 1) return "now";
  if (diffMin < 60) return `${diffMin}m`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h`;
  return `${Math.floor(diffH / 24)}d`;
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function fileIcon() {
  return `<svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.2"><rect x="2.5" y="1.5" width="7" height="9" rx="1"></rect><path d="M4 4h4M4 6h4M4 8h2.5"></path></svg>`;
}

function sourceRef(sourceFile, sourcePage) {
  if (!sourceFile) return "";
  const page = sourcePage ? `·p${sourcePage}` : "";
  return `<span class="wn-src">${fileIcon()}${esc(sourceFile)}${page}</span>`;
}

// ── panel body renderers ────────────────────────────────────────────────────
function corners() {
  return `<div class="pf-corner tl"></div><div class="pf-corner tr"></div>
           <div class="pf-corner bl"></div><div class="pf-corner br"></div>`;
}

function panelShell(id, title, sub, bodyHtml, footHtml) {
  return `
    <div class="pf-panel">
      ${corners()}
      <div class="pf-panel-head">
        <div>
          <div class="pf-panel-title">${title}</div>
          <div class="pf-panel-sub">${sub}</div>
        </div>
        <button class="pf-min-btn" data-action="minimize" title="Minimize">
          <svg width="11" height="11" viewBox="0 0 12 12" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 6h8"></path></svg>
        </button>
      </div>
      <div class="pf-body pf-scroll">${bodyHtml}</div>
      ${footHtml ? `<div class="pf-foot">${footHtml}</div>` : ""}
    </div>`;
}

function renderWhatsNextBody(items, now) {
  if (!items.length) return `<div class="pf-empty">Nothing due</div>`;
  return items.map((item) => {
    const due = fmtDueIn(item.due_ms, now);
    return `
      <div class="wn-row">
        <div class="wn-rank ${due && due.cls === "live" ? "due-soon" : ""}">${pad(item.rank)}</div>
        <div class="wn-main">
          <div class="wn-top">
            <div class="wn-action">${esc(item.action)}</div>
            ${due ? `<div class="wn-due"><div class="wn-due-label">DUE IN</div><div class="wn-due-value ${due.cls}">${due.text}</div></div>` : ""}
          </div>
          <div class="wn-reason">${esc(item.reason)}</div>
          ${sourceRef(item.source_file, item.source_page)}
        </div>
      </div>`;
  }).join("");
}

function renderMajorUpdatesBody(items, now) {
  if (!items.length) return `<div class="pf-empty">No updates</div>`;
  return items.map((u) => {
    const sevLabel = u.severity === "urgent" ? "◤ URGENT" : u.severity === "warning" ? "◤ WARNING" : "◦ INFO";
    return `
      <div class="mu-row">
        <div class="mu-bar ${u.severity}"></div>
        <div class="mu-main">
          <div class="mu-top">
            <span class="mu-sev ${u.severity}">${sevLabel}</span>
            <span class="mu-ago">${fmtAgo(u.detected_ms, now)}</span>
          </div>
          <div class="mu-title">${esc(u.title)}</div>
          <div class="mu-detail">${esc(u.body)}</div>
          ${sourceRef(u.source_file, u.source_page)}
        </div>
      </div>`;
  }).join("");
}

function majorUpdatesSub(items) {
  const urgent = items.filter((u) => u.severity === "urgent").length;
  return `${items.length} NEW${urgent ? ` · <span class="n">${pad(urgent)} URGENT</span>` : ""}`;
}

function renderRecentCasesBody(cases, now) {
  if (!cases.length) return `<div class="pf-empty">No active cases</div>`;
  return cases.map((c) => `
    <div class="rc-row" data-action="select-case" data-case-id="${esc(c.case_id)}">
      <div class="rc-top">
        <span class="rc-title">${esc(c.title || c.case_id)}</span>
        <span class="rc-fir">${fmtAgo(c.last_updated_ms, now)} ago</span>
      </div>
      <div class="rc-event">${esc(c.last_event || "No recent activity")}</div>
    </div>`).join("");
}

function renderChronologyBody(entries) {
  if (!entries.length) return `<div class="pf-empty">Nothing to show</div>`;
  return entries.map((ev) => {
    const when = ev.timestamp_ms ? new Date(ev.timestamp_ms).toISOString().slice(0, 10) : "—";
    return `
      <div class="ch-row">
        <div class="ch-date">${when}</div>
        <div>
          <div class="ch-event">${esc(ev.event)}</div>
          ${ev.source_file ? `<div class="ch-src">${esc(ev.source_file)}</div>` : ""}
        </div>
      </div>`;
  }).join("");
}

function renderSystemsBody(health) {
  if (!health) return `<div class="pf-empty">Agent unreachable</div>`;
  return `
    <div class="sys-row"><span class="sys-label">LLM adapter</span><span class="sys-val">${esc(health.llm)}</span></div>
    <div class="sys-row"><span class="sys-label">LLM available</span><span class="sys-val ${health.llm_available ? "ok" : "bad"}">${health.llm_available ? "ONLINE" : "OFFLINE"}</span></div>
    <div class="sys-row"><span class="sys-label">Watched folders</span><span class="sys-val">${health.watched_folders.length}</span></div>
    <div class="sys-row"><span class="sys-label">Workflows loaded</span><span class="sys-val">${health.workflows.length}</span></div>`;
}

// ── mini-tabs (core mode) ───────────────────────────────────────────────────
const MINI_TAB_WIDTH = 340;
const MINI_TAB_HEIGHT = 64;
const MINI_TAB_GAP = 10;
const MINI_TAB_RIGHT = 172; // clears the 148px orb (right:6) plus margin

function miniTab(id, bottomIndex, cls, edgeCls, name, nameCls, count, countCls, preview) {
  const bottom = 18 + bottomIndex * (MINI_TAB_HEIGHT + MINI_TAB_GAP);
  const style = `right:${MINI_TAB_RIGHT}px;bottom:${bottom}px;width:${MINI_TAB_WIDTH}px;height:${MINI_TAB_HEIGHT}px;`;
  return `
    <button class="mini-tab ${cls}" style="${style}" data-action="focus" data-focus-id="${id}">
      <div class="mini-tab-edge ${edgeCls}"></div>
      <div class="mini-tab-row">
        <span class="mini-tab-name ${nameCls}">${name}</span>
        <span class="mini-tab-count ${countCls}">${count}</span>
      </div>
      ${preview ? `<div class="mini-tab-preview">${esc(preview)}</div>` : ""}
    </button>`;
}

function renderMiniTabs(panels, health) {
  const cases = panels.recent_cases;
  const urgent = panels.major_updates.filter((u) => u.severity === "urgent").length;
  const casePreview = cases[0] ? `${cases[0].title || cases[0].case_id} · ${cases.length} active` : "No active cases";

  // Bottom-most (index 0, nearest the orb) = most actionable.
  return [
    miniTab("whatsnext", 0, "", "accent", "▸ WHAT'S NEXT", "", `${pad(panels.whats_next.length)} DUE`, "", ""),
    miniTab("updates", 1, urgent ? "warn" : "", urgent ? "urgent" : "accent", "⚠ MAJOR UPDATES",
      urgent ? "warn" : "", `${pad(urgent)} URGENT`, urgent ? "urgent" : "", ""),
    miniTab("systems", 2, "", health && health.llm_available ? "accent" : "urgent", "∿ SYSTEMS",
      "", health && health.llm_available ? "NOMINAL" : "DEGRADED", health && health.llm_available ? "ok" : "urgent", ""),
    miniTab("recent", 3, "", "accent", "▣ RECENT CASES", "", `${pad(cases.length)} ACTIVE`, "", casePreview),
  ].join("");
}

// ── top-level render ────────────────────────────────────────────────────────
function alertCount(panels) {
  return panels.major_updates.filter((u) => u.severity === "urgent").length
       + panels.whats_next.filter((i) => i.due_ms && i.due_ms - state.now < 48 * 3600e3).length;
}

function render() {
  const { panels, health, now } = state;
  const focusSet = state.focus === null ? DEFAULT_FOCUS_SET : [state.focus];
  const slot = (id, html) => `<div class="pf-slot ${focusSet.includes(id) ? "on" : ""}">${html}</div>`;

  const cluster = `
    <div id="cluster" class="${state.mode === "expanded" ? "on" : ""}">
      <div id="cluster-row">
        ${slot("whatsnext", panelShell("whatsnext", "WHAT'S NEXT",
          `${state.panels.whats_next.length} ACTIONS · RANKED BY DUE`,
          renderWhatsNextBody(panels.whats_next, now),
          `<span class="pf-legend-dot"><span class="sw" style="background:var(--ok);border-radius:50%"></span></span>DRAFTS PROPOSED — NEVER AUTO-FILED`))}
        ${slot("updates", panelShell("updates", "MAJOR UPDATES", majorUpdatesSub(panels.major_updates),
          renderMajorUpdatesBody(panels.major_updates, now),
          `<span class="pf-legend-dot"><span class="sw" style="background:var(--urgent)"></span>URGENT</span>
           <span class="pf-legend-dot"><span class="sw" style="background:var(--urgent-soft)"></span>WARNING</span>
           <span class="pf-legend-dot"><span class="sw" style="background:var(--info)"></span>INFO</span>`))}
        ${slot("recent", panelShell("recent", "RECENT CASES", `${panels.recent_cases.length} ACTIVE`,
          renderRecentCasesBody(panels.recent_cases, now), ""))}
        ${slot("chronology", panelShell("chronology",
          `CHRONOLOGY${state.selectedCaseId ? ` — ${esc(state.selectedCaseId)}` : ""}`,
          state.chronology.length ? `${state.chronology.length} EVENTS` : "SELECT A CASE",
          renderChronologyBody(state.chronology), ""))}
        ${slot("systems", panelShell("systems", "SYSTEMS", health && health.llm_available ? "NOMINAL" : "DEGRADED",
          renderSystemsBody(health), ""))}
      </div>
    </div>`;

  const core = `
    <div id="core-wrap" class="${state.mode === "expanded" ? "expanded" : ""}">
      <div id="appendage">${renderMiniTabs(panels, health)}</div>
      <button id="orb" data-action="toggle-core" title="Pathfinder — click to open">
        <div class="orb-glow"></div>
        <div class="orb-ring"></div>
        <div class="orb-arc1"></div>
        <div class="orb-arc2"></div>
        <div class="orb-readout">
          <div class="orb-label">PATHFINDER</div>
          <div class="orb-count">${alertCount(panels)}</div>
          <div class="orb-label">ALERTS</div>
          <div class="orb-status"><span class="dot ${state.agentConnected ? "" : "off"}"></span>${state.agentConnected ? "ONLINE" : "OFFLINE"}</div>
        </div>
      </button>
    </div>`;

  document.getElementById("root").innerHTML = cluster + core;
}

// ── interaction ──────────────────────────────────────────────────────────
async function selectCase(caseId) {
  state.selectedCaseId = caseId;
  state.focus = "chronology";
  state.chronology = [];
  render();
  if (!isTauri) return;
  try {
    const { invoke } = window.__TAURI__.core;
    const res = await invoke("fetch_chronology", { caseId });
    state.chronology = res.entries || [];
    render();
  } catch (e) {
    console.error("fetch_chronology failed", e);
  }
}

function handleClick(ev) {
  const target = ev.target.closest("[data-action]");
  if (!target) return;
  const action = target.dataset.action;
  if (action === "toggle-core") {
    state.mode = state.mode === "expanded" ? "core" : "expanded";
    state.focus = null;
    render();
  } else if (action === "focus") {
    state.mode = "expanded";
    state.focus = target.dataset.focusId;
    render();
  } else if (action === "minimize") {
    state.mode = "core";
    state.focus = null;
    render();
  } else if (action === "select-case") {
    selectCase(target.dataset.caseId);
  }
}

// Per-region click-through: window is click-through everywhere except when
// the cursor is over an interactive HUD element.
function setupClickThrough() {
  if (!isTauri) return;
  const { invoke } = window.__TAURI__.core;
  let ignoring = null;
  document.addEventListener("mousemove", (ev) => {
    const overUi = !!ev.target.closest("#cluster.on, #core-wrap");
    const shouldIgnore = !overUi;
    if (shouldIgnore !== ignoring) {
      ignoring = shouldIgnore;
      invoke("set_click_through", { ignore: shouldIgnore }).catch(console.error);
    }
  });
}

function tick() {
  state.now = Date.now();
  render();
}

async function main() {
  document.getElementById("root").addEventListener("click", handleClick);
  setupClickThrough();
  setInterval(tick, 1000);

  if (!isTauri) {
    console.warn("[hud] running outside Tauri — using mock data for preview");
    state.panels = MOCK_PANELS;
    state.health = MOCK_HEALTH;
    state.agentConnected = true;
    render();
    return;
  }

  const { listen } = window.__TAURI__.event;
  await listen("panels-update", (e) => { state.panels = e.payload; render(); });
  await listen("agent-status", (e) => { state.agentConnected = e.payload === "connected"; render(); });
  await listen("health-update", (e) => { state.health = e.payload; render(); });
  await listen("notifications-update", () => {}); // reserved for toast rendering
  render();
}

main();
