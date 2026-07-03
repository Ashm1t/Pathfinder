// Pathfinder HUD — monochrome tactical overlay (see docs/UI_SPEC.md §0.1).
//
// Three layers:
//   mode 'core'     — always-on dock: mini tabs wrapped around the orb
//                     (Highlights bar, Current Case, Schedule, Work Tracker,
//                     tall What's Next column). The dock never leaves the
//                     screen except while the app window is open.
//   mode 'expanded' — clicking a mini tab opens the big version of THAT tab
//                     as a single tall panel beside the dock. Clicking the
//                     same tab (or ×/Escape) closes it.
//   mode 'app'      — clicking the ORB opens the dashboard application:
//                     Overview, Case Diaries (study), Workflows (execute,
//                     draft-only), Schedule (calendar).
//
// Agent data arrives via Tauri events (panels-update / health-update /
// agent-status) and the fetch_chronology command. Schedule, Work Tracker and
// workflow metadata are local sample data until the agent grows endpoints
// for them (see docs/UNTESTED_RISKS.md). Falls back to full mock data when
// run outside Tauri (plain browser preview).

const isTauri = "__TAURI__" in window;

const state = {
  mode: "core",
  focus: null,               // which tab's big panel is open in expanded mode
  appView: "overview",       // 'overview' | 'diaries' | 'workflows' | 'schedule'
  cdTab: "chronology",       // Case Diaries detail tab
  panels: { recent_cases: [], major_updates: [], whats_next: [] },
  health: null,
  agentConnected: false,
  selectedCaseId: null,
  chronology: [],
  calCursor: null,           // { y, m } shown month; null = current
  selDay: null,              // 'YYYY-MM-DD' selected calendar day
  workflowDefs: [],          // real definitions from GET /workflows
  wfRuns: {},                // workflow id -> run state (real or simulated)
  notifications: [],         // agent notifications (persisted server-side)
  lastNotifSeen: null,       // newest created_at already toasted
  toasts: [],                // transient toast stack
  authorText: "",            // composer input (survives re-renders)
  authorState: null,         // null | "working" | author result object
  camsOff: { 4: true, 5: true }, // camera n -> powered off (frontend state)
  now: Date.now(),
};

// ── mock / sample data ──────────────────────────────────────────────────────
const MOCK_PANELS = {
  recent_cases: [
    { case_id: "FIR 201-25", title: "State v. Okafor", fir_number: "201-25",
      last_event: "Document on record: statement 14.06.25.pdf", last_updated_ms: Date.now() - 3600e3 },
    { case_id: "FIR 348-26", title: "FIR 348-26", fir_number: "348-26",
      last_event: "Document on record: fir copy 01.02.26.pdf", last_updated_ms: Date.now() - 86400e3 },
  ],
  major_updates: [
    { severity: "urgent", case_id: "FIR 201-25", title: "Court deadline closing · ISP preservation",
      body: "2024/CR/0457 · expires soon", timestamp_ms: Date.now() + 68 * 60e3,
      detected_ms: Date.now() - 4 * 60e3, source_file: "FIR_0457.pdf", source_page: 3 },
    { severity: "warning", case_id: "FIR 201-25", title: "3 exhibits ingested, not yet linked",
      body: "needs officer review", timestamp_ms: 0,
      detected_ms: Date.now() - 3600e3, source_file: "", source_page: 0 },
    { severity: "info", case_id: "FIR 348-26", title: "Case diary summarised · 14 new pages",
      body: "auto-summary ready to study", timestamp_ms: 0,
      detected_ms: Date.now() - 26 * 3600e3, source_file: "case_diary_348.pdf", source_page: 1 },
  ],
  whats_next: [
    { rank: 1, case_id: "FIR 201-25", action: "File ISP preservation request",
      reason: "2024/CR/0457 · State v. Okafor", due_ms: Date.now() + 68 * 60e3,
      source_file: "FIR_0457.pdf", source_page: 3 },
    { rank: 2, case_id: "FIR 201-25", action: "Review chargesheet draft",
      reason: "2024/CR/0419", due_ms: Date.now() + 28 * 3600e3,
      source_file: "draft_chargesheet.docx", source_page: 12 },
    { rank: 3, case_id: "FIR 348-26", action: "Link seized-mobile exhibits to diary",
      reason: "3 exhibits pending · §65B certificate missing", due_ms: Date.now() + 4 * 86400e3,
      source_file: "", source_page: 0 },
  ],
};
const MOCK_HEALTH = {
  running: true, llm: "ollama", llm_available: true,
  watched_folders: ["_sample"], workflows: ["isp_letter"],
};
const MOCK_CHRONOLOGY = [
  { timestamp_ms: Date.now() - 2 * 86400e3, event: "Seizure memo digitised and indexed", source_file: "seizure_memo_12.pdf" },
  { timestamp_ms: Date.now() - 5 * 86400e3, event: "Witness statement recorded · R. Iyer", source_file: "statement_14.06.25.pdf" },
  { timestamp_ms: Date.now() - 9 * 86400e3, event: "FIR registered at PS Kharghar", source_file: "FIR_0457.pdf" },
];

// Schedule sample events, materialised relative to today so previews stay
// current. kind: court | deadline | task | meet.
const SAMPLE_EVENTS = [
  { d: -2, time: "16:00", title: "Seizure memo digitised", where: "Evidence room", kind: "task" },
  { d: 0, time: "14:30", title: "Hearing · State v. Okafor", where: "District Court 3", kind: "court" },
  { d: 0, time: "17:00", title: "Review exhibits with IO", where: "PS Kharghar", kind: "meet" },
  { d: 1, time: "11:00", title: "ISP preservation filing due", where: "2024/CR/0457", kind: "deadline" },
  { d: 3, time: "10:00", title: "Remand hearing · FIR 348-26", where: "District Court 1", kind: "court" },
  { d: 5, time: "15:30", title: "Draft chargesheet review", where: "2024/CR/0419", kind: "task" },
  { d: 8, time: "11:30", title: "Forensics briefing · mobile extraction", where: "FSL Kalina", kind: "meet" },
  { d: 12, time: "10:00", title: "Chargesheet filing · State v. Okafor", where: "District Court 3", kind: "deadline" },
];

// Security camera wall — simulated feeds until real RTSP/webcam sources are
// wired in. Cameras 4/5 start powered off (mirrors the approved reference).
const SAMPLE_CAMERAS = [
  { n: 1, big: true, sig: true, batt: "" },
  { n: 2, big: true, sig: true, batt: "32% (44min)", blur: true, iso: 800, fps: 24 },
  { n: 3, sig: true }, { n: 4 }, { n: 5 }, { n: 6, sig: true },
  { n: 7, sig: true }, { n: 8, sig: true }, { n: 9, sig: true }, { n: 10, sig: true },
];

// Work Tracker sample until the agent reports activity metrics.
const SAMPLE_TRACKER = [
  { name: "Documents processed", done: 23, total: 31 },
  { name: "Actions completed", done: 6, total: 9 },
  { name: "Drafts reviewed", done: 4, total: 5 },
];

// Browser-preview stand-in for GET /workflows — same shape as the real
// blueprint definitions (agent/config/workflows.json). Inside Tauri the real
// list is fetched from the agent at startup and after registrations.
const MOCK_WORKFLOW_DEFS = [
  { id: "isp_letter", name: "ISP Data Request Letter",
    description: "Drafts a Section 91 BNSS notice to an ISP for CDR/IPDR records from case data",
    enabled: true, trigger: { type: "manual", config: {} },
    steps: [{ name: "Gather case" }, { name: "Read FIR copy" },
            { name: "Extract identifiers from FIR" }, { name: "Draft ISP letter body" },
            { name: "Apply letter template" }, { name: "Save draft" }, { name: "Notify completion" }] },
  { id: "chargesheet_check", name: "Chargesheet Deadline Alert",
    description: "When a chargesheet deadline approaches, checks document completeness and drafts a report",
    enabled: true, trigger: { type: "deadline", config: { days_before: 7, type: "ChargesheetDeadline" } },
    steps: [{ name: "Gather case" }, { name: "Scan completeness" },
            { name: "Build report" }, { name: "Save draft" }, { name: "Alert IO" }] },
  { id: "notice_draft", name: "Witness Notice Generator",
    description: "Drafts one Section 179 BNSS notice per witness on record",
    enabled: true, trigger: { type: "manual", config: {} },
    steps: [{ name: "Gather case" }, { name: "One notice per witness" }, { name: "Notify ready" }] },
  { id: "court_compliance", name: "Court Compliance Report",
    description: "Before a court date, assembles a compliance report from the case chronology",
    enabled: true, trigger: { type: "deadline", config: { days_before: 3, type: "CourtDate" } },
    steps: [{ name: "Gather case" }, { name: "Assemble report" },
            { name: "Save draft" }, { name: "Notify ready" }] },
];

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

function dateKey(d) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// Materialised schedule events: SAMPLE_EVENTS pinned to real dates plus
// What's-Next due dates surfaced as deadline chips.
function allEvents() {
  const out = SAMPLE_EVENTS.map((e) => {
    const d = new Date();
    d.setDate(d.getDate() + e.d);
    return { ...e, key: dateKey(d) };
  });
  for (const item of state.panels.whats_next) {
    if (!item.due_ms) continue;
    const d = new Date(item.due_ms);
    out.push({ key: dateKey(d), time: `${pad(d.getHours())}:${pad(d.getMinutes())}`,
      title: item.action, where: item.reason, kind: "deadline" });
  }
  return out.sort((a, b) => (a.key + a.time).localeCompare(b.key + b.time));
}

function eventsOn(key) { return allEvents().filter((e) => e.key === key); }

// ── icons (inline stroke SVG, 16px grid) ────────────────────────────────────
const ICONS = {
  grid: '<rect x="2.5" y="2.5" width="4.5" height="4.5"/><rect x="9" y="2.5" width="4.5" height="4.5"/><rect x="2.5" y="9" width="4.5" height="4.5"/><rect x="9" y="9" width="4.5" height="4.5"/>',
  book: '<path d="M3 3.5A1.5 1.5 0 0 1 4.5 2H13v10.5H4.5A1.5 1.5 0 0 0 3 14z"/><path d="M3 12.5V14"/><path d="M5.5 5h5M5.5 7.5h5"/>',
  flow: '<circle cx="4" cy="4" r="1.8"/><circle cx="12" cy="12" r="1.8"/><path d="M4 5.8V9a2 2 0 0 0 2 2h4.2"/>',
  cal: '<rect x="2.5" y="3.5" width="11" height="10"/><path d="M2.5 6.5h11M5.5 2v2.5M10.5 2v2.5"/>',
  doc: '<rect x="3.5" y="2" width="9" height="12"/><path d="M6 5.5h4M6 8h4M6 10.5h2.5"/>',
  mail: '<rect x="2" y="3.5" width="12" height="9"/><path d="m2.5 4.5 5.5 4 5.5-4"/>',
  search: '<circle cx="7" cy="7" r="4"/><path d="m10.2 10.2 3 3"/>',
  x: '<path d="m4 4 8 8M12 4l-8 8"/>',
  play: '<path d="M5.5 3.8v8.4l7-4.2z"/>',
  chevL: '<path d="M9.5 3.5 5.5 8l4 4.5"/>',
  chevR: '<path d="m6.5 3.5 4 4.5-4 4.5"/>',
  bolt: '<path d="M8.5 2 4 9h3.5L7 14l4.5-7H8z"/>',
  clock: '<circle cx="8" cy="8" r="5.5"/><path d="M8 5v3.2l2.2 1.3"/>',
  file: '<rect x="3.5" y="2" width="9" height="12"/><path d="M6 5.5h4M6 8h4M6 10.5h2.5"/>',
  cam: '<rect x="1.5" y="4.5" width="9" height="7"/><path d="m10.5 7.5 4-2.5v6l-4-2.5z"/>',
  camoff: '<rect x="1.5" y="4.5" width="9" height="7"/><path d="m10.5 7.5 4-2.5v6l-4-2.5z"/><path d="m2 2 12 12"/>',
  pin: '<path d="M6.2 1.8h3.6l-.5 4.4 2.5 2.6H4.2l2.5-2.6z"/><path d="M8 8.8V14"/>',
  zoom: '<circle cx="7" cy="7" r="4"/><path d="m10.2 10.2 3 3M5.5 7h3M7 5.5v3"/>',
  eye: '<path d="M1.8 8s2.2-4 6.2-4 6.2 4 6.2 4-2.2 4-6.2 4S1.8 8 1.8 8z"/><circle cx="8" cy="8" r="1.8"/>',
  signal: '<path d="M3.5 13v-2M6.5 13V8.5M9.5 13V6M12.5 13V3.5"/>',
  battery: '<rect x="1.5" y="5" width="11" height="6"/><path d="M14.5 7v2"/><rect x="3" y="6.5" width="5.5" height="3" fill="currentColor" stroke="none"/>',
};

function icon(name, size = 15) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 16 16" fill="none"
    stroke="currentColor" stroke-width="1.3" stroke-linecap="square">${ICONS[name]}</svg>`;
}

function sourceRef(sourceFile, sourcePage) {
  if (!sourceFile) return "";
  const page = sourcePage ? `·p${sourcePage}` : "";
  return `<span class="src-ref">${icon("file", 10)}${esc(sourceFile)}${page}</span>`;
}

// ── derived data ────────────────────────────────────────────────────────────
function alertCount() {
  return state.panels.major_updates.filter((u) => u.severity === "urgent").length
       + state.panels.whats_next.filter((i) => i.due_ms && i.due_ms - state.now < 48 * 3600e3).length;
}

function currentCase() {
  return state.panels.recent_cases[0] || null;
}

function todayEvents() {
  return eventsOn(dateKey(new Date()));
}

function workflowCatalogue() {
  return state.workflowDefs;
}

function workflowTargetCase() {
  return state.selectedCaseId
      || (currentCase() && currentCase().case_id) || "";
}

function tauriInvoke(cmd, args) {
  return window.__TAURI__.core.invoke(cmd, args);
}

function kindChip(kind) {
  return `<span class="chip k-${kind}">${kind}</span>`;
}

// ── LAYER 1: dock — mini tabs wrapped around the orb ────────────────────────
function renderDock() {
  const { panels, now } = state;
  const c = currentCase();
  const top = panels.whats_next[0];
  const due = top ? fmtDueIn(top.due_ms, now) : null;
  const urgent = panels.major_updates.filter((u) => u.severity === "urgent").length;
  const hlTop = panels.major_updates[0];
  const nowHM = `${pad(new Date().getHours())}:${pad(new Date().getMinutes())}`;
  const nextEvt = todayEvents().filter((e) => e.time >= nowHM)[0];
  const trackerDone = SAMPLE_TRACKER.reduce((s, t) => s + t.done, 0);
  const trackerTotal = SAMPLE_TRACKER.reduce((s, t) => s + t.total, 0);
  const segs = 14;
  const segOn = trackerTotal ? Math.round((trackerDone / trackerTotal) * segs) : 0;
  const onCls = (id) => (state.mode === "expanded" && state.focus === id ? "on" : "");

  return `
    <div id="hud-dock">
      <button class="hud-tab ${urgent ? "live" : ""} ${onCls("highlights")}" id="tab-highlights"
              data-action="focus-tab" data-focus-id="highlights">
        <span class="microlabel">Highlights</span>
        <div class="hud-tab-main">${hlTop ? esc(hlTop.title) : "No updates this week"}</div>
        <span class="hud-tab-value">${pad(urgent)} URGENT</span>
      </button>

      <button class="hud-tab ${onCls("schedule")}" id="tab-schedule"
              data-action="focus-tab" data-focus-id="schedule">
        <div class="hud-tab-head">
          <span class="microlabel">Schedule</span>
          <span class="hud-tab-value">${nextEvt ? nextEvt.time : "CLEAR"}</span>
        </div>
        <div class="hud-tab-main">${nextEvt ? esc(nextEvt.title) : "No more events today"}</div>
        <div class="hud-tab-sub">${todayEvents().length} today · ${esc(nextEvt ? nextEvt.where : "next: tomorrow")}</div>
      </button>

      <button class="hud-tab ${onCls("current")}" id="tab-current"
              data-action="focus-tab" data-focus-id="current">
        <div class="hud-tab-head">
          <span class="microlabel">Current Case</span>
          <span class="hud-tab-value">${c ? esc(c.fir_number || "") : "—"}</span>
        </div>
        <div class="hud-tab-main">${c ? esc(c.title || c.case_id) : "No active case"}</div>
        <div class="hud-tab-sub">${c ? esc(c.last_event || "") : "Waiting for case data"}</div>
      </button>

      <button class="hud-tab ${onCls("tracker")}" id="tab-tracker"
              data-action="focus-tab" data-focus-id="tracker">
        <span class="microlabel">Tracker</span>
        <div class="seg-bar">${Array.from({ length: segs }, (_, i) =>
          `<span class="seg ${i < segOn ? "on" : ""}"></span>`).join("")}</div>
        <span class="hud-tab-value">${trackerDone}/${trackerTotal}</span>
      </button>

      <button class="hud-tab ${due && due.cls === "live" ? "live" : ""} ${onCls("whatsnext")}" id="tab-whatsnext"
              data-action="focus-tab" data-focus-id="whatsnext">
        <span class="microlabel">What's<br>Next</span>
        <div class="wn-big">${pad(panels.whats_next.length)}</div>
        <span class="microlabel">due</span>
        <div class="wn-countdown ${due ? due.cls : ""}">${due ? due.text : "—"}</div>
        <div class="wn-tail">${top ? esc(top.action) : "Nothing queued"}</div>
      </button>

      <button id="orb" class="${state.agentConnected ? "" : "offline"}" data-action="open-app"
              title="Pathfinder · open dashboard">
        <div class="orb-inner">
          <div class="orb-brand">PATHFINDER</div>
          <div class="orb-count">${alertCount()}</div>
          <div class="orb-brand">ALERTS</div>
          <div class="orb-status"><span class="dot ${state.agentConnected ? "" : "off"}"></span>${state.agentConnected ? "ONLINE" : "OFFLINE"}</div>
        </div>
      </button>
    </div>`;
}

// ── LAYER 2: expanded — one big panel for the clicked tab ───────────────────
function panelShell(title, sub, bodyHtml, footHtml) {
  return `
    <div class="panel">
      <div class="panel-head">
        <div>
          <div class="panel-title">${title}</div>
          <div class="panel-sub">${sub}</div>
        </div>
        <button class="btn icon" data-action="minimize" title="Close panel">${icon("x", 13)}</button>
      </div>
      <div class="panel-body scroll">${bodyHtml}</div>
      ${footHtml ? `<div class="panel-foot">${footHtml}</div>` : ""}
    </div>`;
}

function renderWhatsNextBody(items, now) {
  if (!items.length) return `<div class="empty">Nothing due</div>`;
  return items.map((item) => {
    const due = fmtDueIn(item.due_ms, now);
    return `
      <div class="wn-row">
        <div class="wn-rank ${due && due.cls === "live" ? "hot" : ""}">${pad(item.rank)}</div>
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

function renderHighlightsBody(items, now) {
  if (!items.length) return `<div class="empty">No highlights this week</div>`;
  return items.map((u) => `
    <div class="hl-row">
      <div class="hl-main">
        <div class="hl-top">
          <span class="hl-sev ${u.severity}">▮ ${u.severity.toUpperCase()}</span>
          <span class="hl-ago">${fmtAgo(u.detected_ms, now)}</span>
        </div>
        <div class="hl-title">${esc(u.title)}</div>
        <div class="hl-detail">${esc(u.body)}</div>
        ${sourceRef(u.source_file, u.source_page)}
      </div>
    </div>`).join("");
}

function renderCasesBody(cases, now, action = "select-case") {
  if (!cases.length) return `<div class="empty">No active cases</div>`;
  return cases.map((c) => `
    <button class="case-row ${state.selectedCaseId === c.case_id ? "sel" : ""}"
            data-action="${action}" data-case-id="${esc(c.case_id)}">
      <div class="case-top">
        <span class="case-title">${esc(c.title || c.case_id)}</span>
        <span class="case-ago">${fmtAgo(c.last_updated_ms, now)} ago</span>
      </div>
      <div class="case-event">${esc(c.last_event || "No recent activity")}</div>
    </button>`).join("");
}

function renderChronologyBody(entries) {
  if (!entries.length) return `<div class="empty">Select a case to study its diary</div>`;
  return entries.map((ev) => {
    // dd.mm.yy — matches Indian case-file convention
    const d = ev.timestamp_ms ? new Date(ev.timestamp_ms) : null;
    const when = d ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${String(d.getFullYear()).slice(2)}` : "—";
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

function renderScheduleBody(days = 7) {
  const rows = [];
  const today = new Date();
  for (let i = 0; i < days; i++) {
    const d = new Date(today);
    d.setDate(d.getDate() + i);
    const evts = eventsOn(dateKey(d));
    if (!evts.length) continue;
    const label = i === 0 ? "Today" : i === 1 ? "Tomorrow"
      : d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" });
    rows.push(`<div class="microlabel" style="padding:12px 2px 2px">${label}</div>`);
    rows.push(evts.map((e) => `
      <div class="sch-row">
        <div class="sch-time">${e.time}</div>
        <div class="sch-main">
          <div class="sch-title">${esc(e.title)}</div>
          <div class="sch-where">${esc(e.where)}</div>
        </div>
        ${kindChip(e.kind)}
      </div>`).join(""));
  }
  return rows.join("") || `<div class="empty">Nothing scheduled this week</div>`;
}

function renderTrackerBody() {
  const segs = 14;
  return SAMPLE_TRACKER.map((t) => {
    const on = t.total ? Math.round((t.done / t.total) * segs) : 0;
    return `
      <div class="trk-block">
        <div class="trk-top">
          <span class="trk-name">${esc(t.name)}</span>
          <span class="trk-num">${t.done}/${t.total}</span>
        </div>
        <div class="seg-bar">${Array.from({ length: segs }, (_, i) =>
          `<span class="seg ${i < on ? "on" : ""}"></span>`).join("")}</div>
      </div>`;
  }).join("") + renderSystemsRows();
}

function renderSystemsRows() {
  const h = state.health;
  if (!h) return `<div class="sys-row"><span class="sys-label">Agent</span><span class="sys-val bad">UNREACHABLE</span></div>`;
  return `
    <div class="sys-row"><span class="sys-label">LLM adapter</span><span class="sys-val">${esc(h.llm)}</span></div>
    <div class="sys-row"><span class="sys-label">LLM available</span><span class="sys-val ${h.llm_available ? "ok" : "bad"}">${h.llm_available ? "ONLINE" : "OFFLINE"}</span></div>
    <div class="sys-row"><span class="sys-label">Watched folders</span><span class="sys-val">${h.watched_folders.length}</span></div>`;
}

function workflowTriggerChip(w) {
  const t = (w.trigger && w.trigger.type) || "manual";
  const cfg = (w.trigger && w.trigger.config) || {};
  if (t === "deadline") {
    return `<span class="chip faint">AUTO · ${cfg.days_before ?? "?"}D BEFORE ${esc(cfg.type || "DEADLINE")}</span>`;
  }
  if (t === "file_created" || t === "file_modified") {
    return `<span class="chip faint">AUTO · ON FILE CHANGE</span>`;
  }
  return `<span class="chip faint">MANUAL</span>`;
}

function workflowCard(w) {
  const run = state.wfRuns[w.id];
  const manual = !w.trigger || w.trigger.type === "manual";
  const failedStep = run && run.done && !run.ok ? run.failedStep : "";
  const steps = (w.steps || []).map((s, i) => {
    const name = s.name || s.type || "?";
    let cls = "";
    if (run) {
      if (run.running) cls = i <= 0 ? "running" : "";
      else if (run.done && run.ok) cls = "done";
      else if (run.done && !run.ok) cls = name === failedStep ? "running" : "done";
      else cls = i < run.step ? "done" : i === run.step ? "running" : "";
    }
    return `<div class="wf-step ${cls}"><span class="st"></span>${esc(name)}</div>`;
  }).join("");
  const status = run
    ? (run.running || !run.done) ? `<span class="chip mid">RUNNING</span>`
      : run.ok ? `<span class="chip strong">DRAFT READY</span>`
               : `<span class="chip">FAILED</span>`
    : `<span class="chip">READY</span>`;
  const btn = !manual ? ""
    : (run && (run.running || !run.done))
      ? `<button class="btn ghost" disabled>Running…</button>`
      : `<button class="btn primary" data-action="run-workflow" data-wf-id="${esc(w.id)}">${icon("play", 11)} Run</button>`;
  const result = run && run.done
    ? `<div class="wf-result">${esc(run.ok ? (run.message || "Draft ready") : (run.error || "Failed"))}</div>`
    : "";
  const target = manual ? ` · target: ${esc(workflowTargetCase() || "no case")}` : "";
  return `
    <div class="wf-card">
      <div class="wf-head"><div class="wf-name">${esc(w.name)}</div>${status}</div>
      <div class="wf-desc">${esc(w.description || "")}</div>
      <div class="wf-steps">${steps}</div>
      ${result}
      <div class="wf-foot">
        <span class="microlabel">${(w.steps || []).length} steps · draft-only${target}</span>
        <span style="display:inline-flex;align-items:center;gap:8px">${workflowTriggerChip(w)}${btn}</span>
      </div>
    </div>`;
}

function expandedPanel(id) {
  const { panels, now } = state;
  switch (id) {
    case "whatsnext":
      return panelShell("What's Next", `${panels.whats_next.length} actions · ranked by due`,
        renderWhatsNextBody(panels.whats_next, now),
        `DRAFTS PROPOSED · NEVER AUTO-FILED`);
    case "highlights": {
      const urgent = panels.major_updates.filter((u) => u.severity === "urgent").length;
      return panelShell("Highlights · This Week",
        `${panels.major_updates.length} new${urgent ? ` · ${pad(urgent)} urgent` : ""}`,
        renderHighlightsBody(panels.major_updates, now),
        `<span class="legend"><span class="sw" style="background:var(--hi)"></span>URGENT</span>
         <span class="legend"><span class="sw" style="background:var(--mid)"></span>WARNING</span>
         <span class="legend"><span class="sw" style="background:var(--low)"></span>INFO</span>`);
    }
    case "current":
      return panelShell("Current Case", `${panels.recent_cases.length} active · click a case for its chronology`,
        renderCasesBody(panels.recent_cases, now), "");
    case "chronology":
      return panelShell(`Chronology${state.selectedCaseId ? ` · ${esc(state.selectedCaseId)}` : ""}`,
        state.chronology.length ? `${state.chronology.length} events` : "select a case",
        renderChronologyBody(state.chronology), "");
    case "schedule":
      return panelShell("Schedule", "next 7 days", renderScheduleBody(7), "");
    case "tracker":
      return panelShell("Work Tracker", "this week · agent systems", renderTrackerBody(), "");
    default:
      return "";
  }
}

function renderCluster() {
  return `
    <div id="cluster" class="${state.mode === "expanded" ? "on" : ""}">
      ${state.mode === "expanded" ? expandedPanel(state.focus || "whatsnext") : ""}
    </div>`;
}

// ── LAYER 3: dashboard application ──────────────────────────────────────────
const APP_NAV = [
  { group: "Main menu" },
  { id: "overview", name: "Overview", icon: "grid" },
  { group: "Work" },
  { id: "diaries", name: "Case Diaries", icon: "book" },
  { id: "workflows", name: "Workflows", icon: "flow" },
  { id: "schedule", name: "Schedule", icon: "cal" },
  { id: "security", name: "Security", icon: "cam" },
  { group: "Coming soon" },
  { id: "_reports", name: "Reports", icon: "doc", soon: true },
  { id: "_messaging", name: "Messaging", icon: "mail", soon: true },
];
const APP_TITLES = { overview: "Overview", diaries: "Case Diaries", workflows: "Workflows", schedule: "Schedule", security: "Security" };

function renderSidebar() {
  const items = APP_NAV.map((n) => {
    if (n.group) return `<div class="side-group">${n.group}</div>`;
    if (n.soon) return `<div class="side-item soon">${n.name}</div>`;
    return `<button class="side-item ${state.appView === n.id ? "on" : ""}" data-action="app-nav" data-view="${n.id}">
      ${n.name}</button>`;
  }).join("");
  return `
    <div id="app-side">
      <div class="brand">
        <div class="mark"><img src="assets/logo.png" alt="Pathfinder" /></div>
        <div>
          <div class="name">Pathfinder</div>
          <div class="ver">HUD v0.2 · ${state.agentConnected ? "AGENT ONLINE" : "AGENT OFFLINE"}</div>
        </div>
      </div>
      ${items}
      <div class="side-foot">DRAFTS PROPOSED ·<br>NEVER AUTO-FILED</div>
    </div>`;
}

// variant: "" = primary (framed) · "secondary" = top-rule only · "bare"
function appCard(title, sub, bodyHtml, grow = true, variant = "") {
  return `
    <div class="app-card ${variant} ${grow ? "grow" : ""}">
      <div class="app-card-head">
        <span class="app-card-title">${title}</span>
        <span class="app-card-sub">${sub}</span>
      </div>
      <div class="app-card-body scroll">${bodyHtml}</div>
    </div>`;
}

function statCell(label, value, unit, hint) {
  return `
    <div class="stat-cell">
      <div class="stat-label">${label}</div>
      <div class="stat-value">${value}${unit ? ` <span class="unit">${unit}</span>` : ""}</div>
      ${hint ? `<div class="stat-hint">${hint}</div>` : ""}
    </div>`;
}

function renderOverview() {
  const { panels, now } = state;
  const urgent = panels.major_updates.filter((u) => u.severity === "urgent").length;
  const deadlines = allEvents().filter((e) => e.kind === "deadline").length;
  const defs = workflowCatalogue();
  const wfReady = defs.filter((w) => w.enabled !== false).length;
  return `
    <div class="ov-wrap">
      <div class="stat-strip">
        ${statCell("Open cases", panels.recent_cases.length, "", "across watched folders")}
        ${statCell("Actions due", panels.whats_next.length, "", urgent ? `${urgent} urgent` : "none urgent")}
        ${statCell("Deadlines", deadlines, "", "next 14 days")}
        ${statCell("Workflows", wfReady, `/ ${defs.length}`, "enabled")}
      </div>
      <div class="ov-cols">
        <div class="ov-col">
          ${appCard("What's Next", "ranked by due", renderWhatsNextBody(panels.whats_next, now))}
        </div>
        <div class="ov-col">
          ${appCard("Highlights · This Week", `${panels.major_updates.length} new`,
            renderHighlightsBody(panels.major_updates, now), true, "secondary")}
          ${appCard("Schedule", new Date().toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }),
            renderScheduleBody(2), true, "secondary")}
          ${appCard("Systems", state.health && state.health.llm_available ? "nominal" : "degraded",
            renderTrackerBody(), false, "bare")}
        </div>
      </div>
    </div>`;
}

function renderDiaries() {
  const { panels, now } = state;
  const selId = state.selectedCaseId || (panels.recent_cases[0] && panels.recent_cases[0].case_id);
  const sel = panels.recent_cases.find((c) => c.case_id === selId);
  const tabs = ["chronology", "documents", "notes"];
  let detailBody = "";
  if (state.cdTab === "chronology") {
    detailBody = renderChronologyBody(state.chronology.length ? state.chronology : (isTauri ? [] : MOCK_CHRONOLOGY));
  } else if (state.cdTab === "documents") {
    const docs = (state.chronology.length ? state.chronology : (isTauri ? [] : MOCK_CHRONOLOGY))
      .filter((e) => e.source_file);
    detailBody = docs.length
      ? docs.map((e) => `
          <div class="sch-row">
            <div class="sch-main" style="display:flex;align-items:center;gap:9px">${icon("file", 13)}
              <div>
                <div class="sch-title">${esc(e.source_file)}</div>
                <div class="sch-where">${esc(e.event)}</div>
              </div>
            </div>
          </div>`).join("")
      : `<div class="empty">No documents on record</div>`;
  } else {
    detailBody = `<div class="empty">Officer notes land here once the agent supports them</div>`;
  }
  return `
    <div class="cd-grid">
      ${appCard("Cases", `${panels.recent_cases.length} active`, renderCasesBody(panels.recent_cases, now, "app-select-case"))}
      <div class="cd-detail">
        <div class="cd-header">
          <div class="t" style="flex:1">${sel ? esc(sel.title || sel.case_id) : "Select a case"}${sel ? `<span class="fir">${esc(sel.case_id)}</span>` : ""}</div>
          <span class="chip strong">ACTIVE</span>
          <div class="cd-tabs">
            ${tabs.map((t) => `<button class="cd-tab ${state.cdTab === t ? "on" : ""}" data-action="cd-tab" data-tab="${t}">
              ${t[0].toUpperCase() + t.slice(1)}</button>`).join("")}
          </div>
        </div>
        ${appCard(state.cdTab === "chronology" ? "Case chronology" : state.cdTab === "documents" ? "Documents on record" : "Notes",
          sel ? `studying ${esc(sel.case_id)}` : "", detailBody)}
      </div>
    </div>`;
}

// Authoring composer: officer describes a workflow; the agent's LLM writes a
// blueprint definition; the validator vets it; the officer reviews, then
// registers. (The LLM proposes, the schema disposes.)
function renderAuthorResult() {
  const r = state.authorState;
  if (!r || r === "working") return "";
  if (r.ok && r.workflow) {
    const wf = r.workflow;
    return `
      <div class="author-result">
        <div class="wf-head">
          <div class="wf-name">${esc(wf.name || wf.id)}</div>
          <span class="chip strong">VALIDATED</span>
        </div>
        <div class="wf-desc">${esc(wf.description || "")}</div>
        <div class="wf-steps">${(wf.steps || []).map((s) =>
          `<div class="wf-step"><span class="st"></span>${esc(s.name || s.type)}</div>`).join("")}</div>
        <div class="wf-foot">
          <span class="microlabel">${workflowTriggerChip(wf)}</span>
          <span style="display:inline-flex;gap:8px">
            <button class="btn ghost" data-action="wf-discard">Discard</button>
            <button class="btn primary" data-action="wf-register">Register</button>
          </span>
        </div>
      </div>`;
  }
  return `
    <div class="author-result">
      <div class="wf-head"><div class="wf-name">Not accepted</div>
        <span class="chip">REJECTED</span></div>
      <div class="wf-desc">${(r.errors || ["unknown error"]).map(esc).join("<br>")}</div>
      <div class="wf-foot"><span></span>
        <button class="btn ghost" data-action="wf-discard">Dismiss</button></div>
    </div>`;
}

function renderWorkflowsView() {
  const working = state.authorState === "working";
  return `
    <div class="wfv-grid">
      <div class="wfv-note">${icon("bolt", 13)}
        Workflows draft documents for officer review. Nothing is filed automatically.</div>
      <div class="author-card">
        <div class="microlabel">New workflow · describe what you need</div>
        <div class="author-row">
          <input id="author-input" type="text" spellcheck="false"
                 placeholder="e.g. Draft a bank account freeze request letter for each seized account"
                 value="${esc(state.authorText)}" ${working ? "disabled" : ""} />
          <button class="btn primary" data-action="wf-author" ${working ? "disabled" : ""}>
            ${working ? "Authoring…" : "Author"}</button>
        </div>
        ${renderAuthorResult()}
      </div>
      ${workflowCatalogue().map(workflowCard).join("")}
    </div>`;
}

// Security — CCTV wall. Feeds are simulated (noise + gradients) until real
// camera sources exist; on/off state is frontend-only.
function camTile(cam) {
  const off = !!state.camsOff[cam.n];
  const big = cam.big ? "big" : "";
  if (off) {
    return `
      <div class="cam-tile off ${big}">
        <div class="cam-off-body">
          ${icon("camoff", 18)}
          <div class="cam-off-title">Camera ${cam.n} is turned off</div>
          <div class="cam-off-sub">Currently at ${icon("battery", 12)} 70%</div>
          <button class="btn ghost" data-action="cam-on" data-cam="${cam.n}">${icon("eye", 12)} Turn on</button>
        </div>
      </div>`;
  }
  // deterministic per-camera lighting so tiles read as distinct scenes
  const gx = 18 + (cam.n * 37) % 60;
  const gy = 25 + (cam.n * 53) % 45;
  const glow = 0.05 + ((cam.n * 29) % 10) / 100;
  return `
    <div class="cam-tile ${big}">
      <div class="cam-feed ${cam.blur ? "blurred" : ""}"
           style="background:radial-gradient(ellipse at ${gx}% ${gy}%, rgba(255,255,255,${glow + 0.08}), transparent 55%),
                  linear-gradient(${(cam.n * 71) % 180}deg, rgba(255,255,255,${glow}) 0%, transparent 40%, rgba(0,0,0,.35) 100%)">
        <div class="cam-noise"></div>
      </div>
      <div class="cam-scan"></div>
      <div class="cam-name">Camera ${cam.n}</div>
      <div class="cam-stat">${icon("signal", 11)}${icon("battery", 12)}${cam.batt ? ` ${esc(cam.batt)}` : ""}</div>
      ${cam.iso ? `<div class="cam-meta"><span>Iso: ${cam.iso}</span><span>Fps: ${cam.fps}</span></div>` : ""}
      <div class="cam-tools">
        <button class="cam-tool" title="Pin">${icon("pin", 13)}</button>
        <button class="cam-tool" title="Zoom">${icon("zoom", 13)}</button>
        <button class="cam-tool" title="Turn off" data-action="cam-off" data-cam="${cam.n}">${icon("camoff", 13)}</button>
      </div>
    </div>`;
}

function renderSecurityView() {
  return `
    <div class="sec-view">
      <div class="sec-sub">Live status of every connected station camera.</div>
      <div class="cam-grid">${SAMPLE_CAMERAS.map(camTile).join("")}</div>
    </div>`;
}

function calMonth() {
  if (state.calCursor) return state.calCursor;
  const d = new Date();
  return { y: d.getFullYear(), m: d.getMonth() };
}

function renderScheduleView() {
  const { y, m } = calMonth();
  const first = new Date(y, m, 1);
  const startDow = (first.getDay() + 6) % 7; // Monday-first grid
  const gridStart = new Date(y, m, 1 - startDow);
  const todayKey = dateKey(new Date());
  const selKey = state.selDay || todayKey;
  const events = allEvents();
  const byDay = {};
  for (const e of events) (byDay[e.key] = byDay[e.key] || []).push(e);

  const cells = [];
  for (let i = 0; i < 42; i++) {
    const d = new Date(gridStart);
    d.setDate(d.getDate() + i);
    const key = dateKey(d);
    const evts = byDay[key] || [];
    const cls = [
      d.getMonth() !== m ? "dim" : "",
      key === todayKey ? "today" : "",
      key === selKey ? "sel" : "",
    ].join(" ");
    cells.push(`
      <button class="cal-day ${cls}" data-action="cal-day" data-day="${key}">
        <span class="cal-num">${d.getDate()}</span>
        ${evts.slice(0, 2).map((e) => `<span class="cal-evt ${e.kind}">${e.time} ${esc(e.title)}</span>`).join("")}
        ${evts.length > 2 ? `<span class="cal-evt more">+${evts.length - 2} more</span>` : ""}
      </button>`);
  }

  const selEvts = byDay[selKey] || [];
  const selDate = new Date(`${selKey}T00:00:00`);
  const agenda = selEvts.length
    ? selEvts.map((e) => `
        <div class="sch-row">
          <div class="sch-time">${e.time}</div>
          <div class="sch-main">
            <div class="sch-title">${esc(e.title)}</div>
            <div class="sch-where">${esc(e.where)}</div>
          </div>
          ${kindChip(e.kind)}
        </div>`).join("")
    : `<div class="empty">Nothing scheduled</div>`;

  return `
    <div class="cal-grid">
      <div class="app-card grow">
        <div class="cal-head">
          <span class="cal-month">${first.toLocaleDateString("en-GB", { month: "long", year: "numeric" })}</span>
          <div style="display:flex;gap:6px">
            <button class="btn icon" data-action="cal-prev">${icon("chevL", 13)}</button>
            <button class="btn icon" data-action="cal-next">${icon("chevR", 13)}</button>
          </div>
        </div>
        <div class="cal-dow">${["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"].map((d) => `<div>${d}</div>`).join("")}</div>
        <div class="cal-days">${cells.join("")}</div>
      </div>
      <div class="ov-col">
        ${appCard(selDate.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long" }),
          `${selEvts.length} events`, agenda)}
      </div>
    </div>`;
}

function renderApp() {
  const views = {
    overview: renderOverview, diaries: renderDiaries,
    workflows: renderWorkflowsView, schedule: renderScheduleView,
    security: renderSecurityView,
  };
  return `
    <div id="app-layer" class="${state.mode === "app" ? "on" : ""}">
      <div id="app-window">
        ${renderSidebar()}
        <div id="app-main">
          <div id="app-top">
            <div class="where"><span class="crumb">Pathfinder / </span>${APP_TITLES[state.appView]}</div>
            <div id="app-search">${icon("search", 13)} Search FIRs, exhibits, orders…</div>
            <span class="chip ${state.agentConnected ? "strong" : "faint"}">
              <span class="dot ${state.agentConnected ? "" : "off"}"></span>${state.agentConnected ? "ONLINE" : "OFFLINE"}</span>
            <button class="btn icon" data-action="close-app" title="Close dashboard">${icon("x", 14)}</button>
          </div>
          <div id="app-view">${state.mode === "app" ? views[state.appView]() : ""}</div>
        </div>
      </div>
    </div>`;
}

// ── toasts (agent notifications) ────────────────────────────────────────────
let _toastSeq = 0;

function renderToasts() {
  if (!state.toasts.length) return "";
  return `<div id="toast-stack">${state.toasts.map((t) => `
    <div class="toast">
      <span class="hl-sev ${esc(t.severity || "info")}">▮ ${esc((t.severity || "info").toUpperCase())}</span>
      <div class="toast-msg">${esc(t.message)}</div>
      ${t.case_id ? `<div class="toast-case">${esc(t.case_id)}</div>` : ""}
    </div>`).join("")}</div>`;
}

function addToast(n) {
  const id = ++_toastSeq;
  state.toasts.push({ ...n, id });
  render();
  setTimeout(() => {
    state.toasts = state.toasts.filter((t) => t.id !== id);
    render();
  }, 8000);
}

function handleNotifications(list) {
  state.notifications = list;
  const newest = list.length ? Math.max(...list.map((n) => n.created_at || 0)) : 0;
  if (state.lastNotifSeen === null) {
    state.lastNotifSeen = newest;    // don't toast history on startup
    return;
  }
  for (const n of list.filter((n) => (n.created_at || 0) > state.lastNotifSeen)) {
    addToast(n);
  }
  state.lastNotifSeen = Math.max(state.lastNotifSeen, newest);
}

// ── top-level render ────────────────────────────────────────────────────────
function render() {
  // Don't wipe the composer (or any input) out from under the officer:
  // periodic ticks and data-event re-renders wait until typing stops.
  const ae = document.activeElement;
  if (ae && (ae.tagName === "INPUT" || ae.tagName === "TEXTAREA")) return;
  const dock = state.mode === "app" ? "" : renderDock();
  const cluster = state.mode === "expanded" ? renderCluster() : "";
  document.getElementById("root").innerHTML =
    cluster + renderApp() + dock + renderToasts();
}

// ── interaction ─────────────────────────────────────────────────────────────
async function selectCase(caseId) {
  state.selectedCaseId = caseId;
  state.chronology = [];
  render();
  if (!isTauri) {
    state.chronology = MOCK_CHRONOLOGY;
    render();
    return;
  }
  try {
    const { invoke } = window.__TAURI__.core;
    const res = await invoke("fetch_chronology", { caseId });
    state.chronology = res.entries || [];
    render();
  } catch (e) {
    console.error("fetch_chronology failed", e);
  }
}

// Run a workflow through the real agent (POST /workflow/{id}/run via the
// Tauri shell). Outside Tauri, a step-ticking simulation stands in.
async function runWorkflow(id) {
  const existing = state.wfRuns[id];
  if (existing && (existing.running || !existing.done)) return;
  const def = state.workflowDefs.find((w) => w.id === id);
  if (!def) return;

  if (!isTauri) {
    state.wfRuns[id] = { step: 0, done: false, ok: true };
    render();
    const timer = setInterval(() => {
      const run = state.wfRuns[id];
      run.step += 1;
      if (run.step >= def.steps.length) {
        run.done = true;
        run.message = "Draft ready (simulated)";
        clearInterval(timer);
      }
      render();
    }, 900);
    return;
  }

  state.wfRuns[id] = { running: true, done: false };
  render();
  try {
    const res = await tauriInvoke("run_workflow",
      { workflowId: id, caseId: workflowTargetCase() });
    const notif = (res.context && res.context.notification) || null;
    state.wfRuns[id] = {
      running: false, done: true, ok: !!res.ok,
      message: notif ? notif.message : "",
      error: res.error || "",
      failedStep: (res.error || "").replace("Step failed: ", ""),
    };
  } catch (e) {
    state.wfRuns[id] = { running: false, done: true, ok: false,
                         error: String(e), failedStep: "" };
  }
  render();
}

// Authoring flow: author (LLM + validator) -> officer reviews -> register.
async function authorWorkflow() {
  const request = state.authorText.trim();
  if (!request || state.authorState === "working") return;
  state.authorState = "working";
  render();
  if (!isTauri) {
    setTimeout(() => {
      state.authorState = {
        ok: true, registered: false, errors: [],
        workflow: { id: "preview_flow", name: "Preview workflow (mock)",
          description: request, trigger: { type: "manual", config: {} },
          steps: [{ name: "Gather case" }, { name: "Produce draft" },
                  { name: "Save draft" }, { name: "Tell the officer" }] },
      };
      render();
    }, 900);
    return;
  }
  try {
    state.authorState = await tauriInvoke("author_workflow",
      { request, register: false });
  } catch (e) {
    state.authorState = { ok: false, workflow: null, errors: [String(e)] };
  }
  render();
}

async function registerAuthoredWorkflow() {
  const r = state.authorState;
  if (!r || r === "working" || !r.ok || !r.workflow) return;
  if (!isTauri) {
    state.workflowDefs = [...state.workflowDefs, r.workflow];
    state.authorState = null;
    state.authorText = "";
    addToast({ message: `Workflow "${r.workflow.name}" registered`,
               severity: "info", case_id: "" });
    render();
    return;
  }
  try {
    const res = await tauriInvoke("register_workflow", { workflow: r.workflow });
    if (res.ok) {
      const list = await tauriInvoke("fetch_workflows");
      state.workflowDefs = list.workflows || [];
      state.authorState = null;
      state.authorText = "";
      addToast({ message: `Workflow "${r.workflow.name}" registered`,
                 severity: "info", case_id: "" });
    } else {
      state.authorState = { ...r, ok: false, errors: res.errors || ["register failed"] };
    }
  } catch (e) {
    state.authorState = { ...r, ok: false, errors: [String(e)] };
  }
  render();
}

function handleClick(ev) {
  const target = ev.target.closest("[data-action]");
  if (!target || target.disabled) return;
  const action = target.dataset.action;
  if (action === "focus-tab") {
    const id = target.dataset.focusId;
    if (state.mode === "expanded" && state.focus === id) {
      state.mode = "core";           // clicking the open tab closes its panel
      state.focus = null;
    } else {
      state.mode = "expanded";
      state.focus = id;
    }
  } else if (action === "minimize") {
    state.mode = "core";
    state.focus = null;
  } else if (action === "open-app") {
    state.mode = "app";
    state.focus = null;
    if (!state.selectedCaseId && currentCase()) {
      selectCase(currentCase().case_id);
      return;
    }
  } else if (action === "close-app") {
    state.mode = "core";
  } else if (action === "app-nav") {
    state.appView = target.dataset.view;
  } else if (action === "select-case" || action === "app-select-case") {
    if (action === "select-case") state.focus = "chronology"; // big panel jumps to the case's diary
    if (action === "app-select-case") state.appView = "diaries";
    selectCase(target.dataset.caseId);
    return;
  } else if (action === "cd-tab") {
    state.cdTab = target.dataset.tab;
  } else if (action === "run-workflow") {
    runWorkflow(target.dataset.wfId);
    return;
  } else if (action === "wf-author") {
    authorWorkflow();
    return;
  } else if (action === "wf-register") {
    registerAuthoredWorkflow();
    return;
  } else if (action === "wf-discard") {
    state.authorState = null;
  } else if (action === "cal-prev" || action === "cal-next") {
    const { y, m } = calMonth();
    const next = new Date(y, m + (action === "cal-next" ? 1 : -1), 1);
    state.calCursor = { y: next.getFullYear(), m: next.getMonth() };
  } else if (action === "cal-day") {
    state.selDay = target.dataset.day;
  } else if (action === "cam-on") {
    delete state.camsOff[target.dataset.cam];
  } else if (action === "cam-off") {
    state.camsOff[target.dataset.cam] = true;
  }
  render();
}

// Per-region click-through: the window ignores the cursor everywhere except
// over interactive HUD surfaces.
function setupClickThrough() {
  if (!isTauri) return;
  const { invoke } = window.__TAURI__.core;
  let ignoring = null;
  document.addEventListener("mousemove", (ev) => {
    const overUi = !!ev.target.closest(".hud-tab, #orb, #cluster.on, #app-layer.on #app-window");
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
  const root = document.getElementById("root");
  root.addEventListener("click", handleClick);
  // Composer input survives re-renders: value lives in state, and Enter
  // submits (the render guard skips re-renders while an input has focus).
  root.addEventListener("input", (ev) => {
    if (ev.target.id === "author-input") state.authorText = ev.target.value;
  });
  root.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && ev.target.id === "author-input") {
      ev.target.blur();
      authorWorkflow();
    }
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && state.mode !== "core") {
      state.mode = "core";
      state.focus = null;
      render();
    }
  });
  setupClickThrough();
  setInterval(tick, 1000);

  if (!isTauri) {
    console.warn("[hud] running outside Tauri — using mock data for preview");
    state.panels = MOCK_PANELS;
    state.health = MOCK_HEALTH;
    state.workflowDefs = MOCK_WORKFLOW_DEFS;
    state.agentConnected = true;
    render();
    return;
  }

  const { listen } = window.__TAURI__.event;
  await listen("panels-update", (e) => { state.panels = e.payload; render(); });
  await listen("agent-status", (e) => { state.agentConnected = e.payload === "connected"; render(); });
  await listen("health-update", (e) => { state.health = e.payload; render(); });
  await listen("notifications-update", (e) => {
    handleNotifications((e.payload && e.payload.notifications) || []);
  });
  try {
    const res = await tauriInvoke("fetch_workflows");
    state.workflowDefs = res.workflows || [];
  } catch (e) {
    console.error("fetch_workflows failed", e);
  }
  render();
}

main();

// Debug/preview handle (used by headless screenshot tooling; harmless in prod).
window.__pf = { state, render };
