// Contagion UI. Vanilla JS, no build step. All scraped text is escaped before rendering.

const view = document.getElementById("view");
const state = { config: null, token: sessionStorageGet("contagion_token") || "", es: null, tab: "spread", filter: "all", probe: null, mode: "watch" };

const STAGES_DEFEND = [["discovery", "Discovery"], ["analysis", "Analysis"], ["cross_reference", "Cross-referencing"], ["synthesis", "Synthesis"]];
const STAGES_COUNTER = [["inventory", "Inventory"], ["listening", "Listening"], ["reality", "Reality check"], ["drafting", "Match & draft"]];
const stagesFor = (c) => (c && c.mode === "counter" ? STAGES_COUNTER : STAGES_DEFEND);
const KIND_LABEL = { fact_sheet: "Fact sheet", site_fix: "Site fix", correction_request: "Correction request", platform_flag: "Platform review flag", ad_package: "Ad package" };
const GRADE_LABELS = ["Contained", "Spreading", "Mainstream", "Contaminated"];

// ---------- utils ----------
function sessionStorageGet(k) { try { return sessionStorage.getItem(k); } catch { return null; } }
function sessionStorageSet(k, v) { try { sessionStorage.setItem(k, v); } catch { /* private mode */ } }
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const safeHref = (u) => (/^https?:\/\//i.test(u || "") ? esc(u) : "#");
const host = (u) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u || ""; } };
const day = (iso) => (iso ? iso.slice(0, 10) : "undated");
const plural = (n, w) => `${n} ${w}${n === 1 ? "" : "s"}`;
function fmtLag(h) {
  if (h === null || h === undefined) return "n/a";
  if (Math.abs(h) < 48) return `${h} h`;
  return `${(h / 24).toFixed(1)} d`;
}

async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  if (state.token) headers["X-Access-Token"] = state.token;
  const res = await fetch(path, { ...opts, headers });
  if (res.status === 401) { await askToken(); return api(path, opts); }
  if (!res.ok) {
    let msg = `${res.status}`;
    try { const j = await res.json(); msg = j.detail ? (typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail)) : msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return res.headers.get("content-type")?.includes("json") ? res.json() : res.text();
}

function askToken() {
  return new Promise((resolve) => {
    const dlg = document.getElementById("token-dialog");
    const form = document.getElementById("token-form");
    form.onsubmit = () => {
      state.token = document.getElementById("token-input").value.trim();
      sessionStorageSet("contagion_token", state.token);
      resolve();
    };
    dlg.showModal();
  });
}

// ---------- router ----------
window.addEventListener("hashchange", route);
document.querySelectorAll("dialog [data-close]").forEach((b) => b.addEventListener("click", () => b.closest("dialog").close()));
document.getElementById("nav-status").addEventListener("click", showStatus);

async function route() {
  if (state.es) { state.es.close(); state.es = null; }
  stopWatchTimers();
  const m = location.hash.match(/^#\/case\/([\w-]+)/);
  const mw = location.hash.match(/^#\/watch\/([\w-]+)/);
  try {
    if (!state.config) state.config = await api("/api/config");
    if (mw) await renderWatch(mw[1]);
    else if (m) await renderCase(m[1], true);
    else await renderHome();
  } catch (err) {
    view.innerHTML = `<div class="error-box">Couldn't load: ${esc(err.message)}</div>`;
  }
}

// ---------- home ----------
async function renderHome() {
  const { cases, replays } = await api("/api/cases");
  const presets = state.config.presets || [];
  view.innerHTML = `
    <div class="home">
      <section class="intro">
        <div class="seg" role="tablist" aria-label="Mode">
          <button type="button" role="tab" data-mode="watch" aria-selected="${state.mode === "watch"}">Watch a brand</button>
          <button type="button" role="tab" data-mode="defend" aria-selected="${state.mode === "defend"}">Trace a specific rumor</button>
          <button type="button" role="tab" data-mode="counter" aria-selected="${state.mode === "counter"}">Counter: competitor gap x inventory</button>
        </div>
        ${state.mode === "watch" ? watchForm() : state.mode === "counter" ? counterForm() : `
        <h1>Trace a rumor and see whether AI engines now repeat it.</h1>
        <p>Contagion finds every public instance of a claim about your brand, maps how it moved across platforms,
        asks the AI engines what they say about it, and drafts the response. A person approves every action. Nothing is sent automatically.</p>
        <div class="presets"><span>Load a resolved public case:</span>
          ${presets.map((p) => `<button type="button" data-preset="${esc(p.key)}">${esc(p.label)}</button>`).join("")}
        </div>
        <form id="case-form" autocomplete="off">
          <div class="field"><label for="f-brand">Brand</label><input id="f-brand" name="brand" required maxlength="120"></div>
          <div class="field"><label for="f-claim">The rumor <span class="hint">as people repeat it</span></label>
            <textarea id="f-claim" name="claim" required minlength="5" maxlength="600" rows="2"></textarea></div>
          <div class="field"><label for="f-truth">What is true <span class="hint">the brand's position; drafts may only use these facts</span></label>
            <textarea id="f-truth" name="truth" required minlength="5" maxlength="2000" rows="3"></textarea></div>
          <div class="field"><label for="f-url">Official statement URL <span class="hint">optional</span></label><input id="f-url" name="truth_url" type="url" maxlength="500"></div>
          <div class="field"><label for="f-kw">Extra search phrases <span class="hint">comma separated, optional</span></label><input id="f-kw" name="keywords"></div>
          <div class="row">
            <div class="field"><label for="f-since">From <span class="hint">optional</span></label><input id="f-since" name="since" type="date"></div>
            <div class="field"><label for="f-until">To <span class="hint">optional</span></label><input id="f-until" name="until" type="date"></div>
          </div>
          <div id="form-error"></div>
          <div class="row-end"><button class="btn" type="submit">Start trace</button></div>
        </form>`}
      </section>
      <aside>
        <div class="side-section">
          <h3>Recent cases</h3>
          <div class="list">${cases.length ? cases.map(caseRow).join("") : `<div class="empty">No cases yet.</div>`}</div>
        </div>
        <div class="side-section">
          <h3>Recorded runs</h3>
          <div class="list">${replays.length ? replays.map((r) => `
            <a class="list-item" href="#" data-replay="${esc(r.name)}" data-rmode="${esc(r.mode)}">
              <div class="li-top"><span>${esc(r.brand)}</span><span class="mono">${esc(day(r.recorded_at))}</span></div>
              <div class="li-claim">${esc(r.claim)}</div></a>`).join("") : `<div class="empty">Save a finished live run as a replay to have a backup for the demo.</div>`}</div>
        </div>
      </aside>
    </div>`;

  view.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => fillPreset(presets.find((p) => p.key === b.dataset.preset))));
  view.querySelectorAll("[data-replay]").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    const { id } = await api(`/api/replays/${encodeURIComponent(a.dataset.replay)}`, { method: "POST" });
    location.hash = a.dataset.rmode === "watch" ? `#/watch/${id}` : `#/case/${id}`;
  }));
  view.querySelectorAll("[data-mode]").forEach((b) => b.addEventListener("click", () => { state.mode = b.dataset.mode; renderHome(); }));
  if (state.mode === "watch") wireWatchForm();
  else if (state.mode === "counter") wireCounterForm();
  else document.getElementById("case-form").addEventListener("submit", submitCase);
}

function caseRow(c) {
  if (c.mode === "watch") {
    return `<a class="list-item" href="#/watch/${esc(c.id)}">
      <div class="li-top"><span>Watch · ${esc(c.brand)}${c.replay ? " · recorded" : ""}</span><span class="mono">${esc(c.status)}</span></div>
      <div class="li-claim">${esc(c.claim)}</div></a>`;
  }
  const g = c.mode === "counter"
    ? (c.status === "done" ? `${c.summary?.ad_packages ?? 0} ad drafts` : c.status)
    : (c.grade && c.grade.level !== undefined ? `Grade ${c.grade.level} · ${c.grade.label}` : c.status);
  return `<a class="list-item" href="#/case/${esc(c.id)}">
    <div class="li-top"><span>${c.mode === "counter" ? "Counter · " : ""}${esc(c.brand)}${c.replay ? " · recorded" : ""}</span><span class="mono">${esc(g)}</span></div>
    <div class="li-claim">${esc(c.claim)}</div></a>`;
}

function fillPreset(p) {
  if (!p) return;
  const set = (id, v) => { document.getElementById(id).value = v || ""; };
  set("f-brand", p.brand); set("f-claim", p.claim); set("f-truth", p.truth); set("f-url", p.truth_url);
  set("f-kw", (p.keywords || []).join(", ")); set("f-since", p.since); set("f-until", p.until);
}

async function submitCase(e) {
  e.preventDefault();
  const f = new FormData(e.target);
  const body = Object.fromEntries(f.entries());
  body.keywords = (body.keywords || "").split(",").map((s) => s.trim()).filter(Boolean);
  const btn = e.target.querySelector("button[type=submit]");
  btn.disabled = true; btn.textContent = "Starting...";
  try {
    const { id } = await api("/api/cases", { method: "POST", body: JSON.stringify(body) });
    location.hash = `#/case/${id}`;
  } catch (err) {
    document.getElementById("form-error").innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
    btn.disabled = false; btn.textContent = "Start trace";
  }
}

// ---------- case ----------
let current = null;
let refreshTimer = null;

async function renderCase(id, subscribe) {
  current = await api(`/api/cases/${id}`);
  drawCase();
  if (subscribe && (current.status === "running" || current.status === "queued")) listen(id);
}

function listen(id) {
  const url = `/api/cases/${id}/events${state.token ? `?token=${encodeURIComponent(state.token)}` : ""}`;
  state.es = new EventSource(url);
  state.es.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.type === "log" && current) {
      current.log.push(ev.line);
      current.stage = ev.stage || current.stage;
      if (state.tab === "log") drawTabBody();
      drawStepper();
    }
    // Throttled full refresh so counts and charts fill in while the run progresses.
    clearTimeout(refreshTimer);
    refreshTimer = setTimeout(() => renderCase(id, false), ev.type === "done" ? 0 : 700);
    if (ev.type === "done") { state.es.close(); state.es = null; }
  };
}

function drawCase() {
  if (current.mode === "counter") return drawCounter();
  const c = current;
  const g = c.grade || {};
  const s = c.spread || {};
  const ct = s.contamination || {};
  const pending = c.actions.filter((a) => a.status === "pending").length;
  const platforms = Object.entries(s.platforms || {}).filter(([, v]) => v.amplifies > 0).length;
  const running = c.status === "running" || c.status === "queued";

  view.innerHTML = `
    ${(state.config.presets || []).some((p) => p.brand === c.input.brand) ? `<div class="banner">Demo on a public, already-resolved case. Contagion is not affiliated with ${esc(c.input.brand)}; drafts are examples and are never sent.</div>` : ""}
    ${c.replay ? `<div class="banner">Recorded run, opened as a replay. Numbers reflect the original run time; no live calls were made.</div>` : ""}
    ${c.status === "failed" ? `<div class="error-box" style="margin-bottom:16px">This run failed. See the log tab for the reason.</div>` : ""}
    <div class="case-head">
      <div>
        <div class="case-brand">${esc(c.input.brand)} · case ${esc(c.id)} · ${esc(day(c.created_at))}${c.elapsed_seconds ? ` · ran in ${c.elapsed_seconds}s` : ""}</div>
        <h1 class="case-claim">${esc(c.input.claim)}</h1>
        <div class="case-truth"><b>What's true:</b> ${esc(c.input.truth)}</div>
      </div>
      ${gradeBlock(g, running)}
    </div>
    <div class="stepper" id="stepper"></div>
    <div class="kpis">
      ${kpi(s.total_relevant ?? "–", "public instances about the claim")}
      ${kpi(s.amplifiers ?? "–", "spreading it as true", (s.amplifiers || 0) > 0)}
      ${kpi(platforms || (running ? "–" : 0), "platforms carrying it")}
      ${kpi(s.correction_lag_hours != null ? fmtLag(s.correction_lag_hours) : "–", "until the first correction")}
      ${kpi(ct.answers ? `${ct.repeats}/${ct.answers}` : "–", "AI answers repeating it", (ct.repeats || 0) > 0)}
    </div>
    <div class="tabs" role="tablist">
      ${tabBtn("spread", "Spread")}
      ${tabBtn("ai", "AI engines", ct.repeats ? `<span class="count attn">${ct.repeats}</span>` : "")}
      ${tabBtn("actions", "Actions", c.actions.length ? `<span class="count ${pending ? "attn" : ""}">${pending} pending</span>` : "")}
      ${tabBtn("evidence", "Evidence", `<span class="count">${c.items.filter((i) => i.stance !== "unrelated").length}</span>`)}
      ${tabBtn("log", "Log")}
    </div>
    <div id="tab-body"></div>`;
  drawStepper();
  view.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => { state.tab = t.dataset.tab; drawCase(); }));
  drawTabBody();
}

function kpi(v, k, hot) { return `<div class="kpi ${hot ? "hot" : ""}"><div class="v">${esc(v)}</div><div class="k">${esc(k)}</div></div>`; }
function tabBtn(key, label, extra = "") { return `<button class="tab" role="tab" data-tab="${key}" aria-selected="${state.tab === key}">${label}${extra}</button>`; }

function gradeBlock(g, running) {
  if (g.level === undefined) {
    return `<div class="grade"><div class="grade-level">Escalation grade</div><div class="grade-label">${running ? "Running" : "Pending"}</div>
      <div class="grade-why">${running ? "The grade appears after the AI engines are checked." : ""}</div></div>`;
  }
  const parts = g.score_parts || {};
  return `<div class="grade" data-level="${g.level}">
    <div class="grade-top"><span class="grade-level">Grade ${g.level} of 4</span></div>
    <div class="grade-label">${esc(g.label)}</div>
    <div class="grade-why">${esc(g.why)}</div>
    <div class="ladder">${[1, 2, 3, 4].map((n) => `<span class="${n <= g.level ? "on" : ""}"></span>`).join("")}</div>
    <div class="ladder-labels">${GRADE_LABELS.map((l) => `<span>${l}</span>`).join("")}</div>
    <div class="risk" title="Heuristic: spread ${parts.spread} + platforms ${parts.platforms} + news ${parts.mainstream} + AI contamination ${parts.ai_contamination}">
      <span>Risk score <span class="muted">(heuristic)</span></span><span class="mono">${g.risk_score}/100</span></div>
  </div>`;
}

function drawStepper() {
  const el = document.getElementById("stepper");
  if (!el || !current) return;
  const stages = stagesFor(current);
  const idx = stages.findIndex(([k]) => k === current.stage);
  const done = current.status === "done";
  el.innerHTML = stages.map(([k, label], i) => {
    const cls = done || i < idx ? "done" : i === idx && current.status === "running" ? "active" : "";
    return `<div class="step ${cls}"><span class="n">0${i + 1}</span>${label}</div>`;
  }).join("");
}

function drawTabBody() {
  const el = document.getElementById("tab-body");
  if (!el) return;
  const fn = { spread: tabSpread, ai: tabAI, actions: tabActions, evidence: tabEvidence, log: tabLog,
    opps: tabOpps, inventory: tabInventory, complaints: tabComplaints }[state.tab] || tabLog;
  el.innerHTML = fn();
  wireTab(el);
}

// ---------- spread tab ----------
function tabSpread() {
  const c = current, s = c.spread || {};
  const byId = Object.fromEntries(c.items.map((i) => [i.id, i]));
  const first = byId[s.earliest_amplifier_id];
  const debunk = byId[s.first_debunk_id];
  if (!c.items.length) return `<p class="muted">${c.status === "done" ? "No public items found. Check the Sources panel for skipped or failing sources." : "Collecting public items..."}</p>`;
  const rows = Object.entries(s.platforms || {}).sort((a, b) => (a[1].first_seen || "z").localeCompare(b[1].first_seen || "z"));
  return `
    <div class="panel"><h3>Timeline by platform</h3>
      <div class="card">${timeline(c.items)}
        <div class="legend"><span><i class="dot amplifies"></i>spreading</span><span><i class="dot reports"></i>neutral</span><span><i class="dot debunks"></i>correcting</span>
        ${s.undated ? `<span>${s.undated} undated item(s) not plotted</span>` : ""}</div>
      </div>
    </div>
    <div class="grid-2">
      <div class="panel"><h3>How it moved</h3>
        ${s.hops?.length ? `<ol class="hops">${s.hops.map((h, i) => `
          <li><span class="idx">${i + 1}</span><span><b>${esc(h.platform)}</b> <span class="muted small">${esc(day(h.at))}</span></span>
          <span class="lag">${i === 0 ? "first seen" : `+${fmtLag(h.lag_hours)}`}</span></li>`).join("")}</ol>` : `<p class="muted small">Not enough dated items.</p>`}
        ${s.edges?.length ? `<p class="small muted" style="margin-top:10px">${plural(s.edges.length, "direct link")} between traced pages (one page linking to another).</p>` : ""}
      </div>
      <div class="panel"><h3>Earliest public instance found</h3>
        ${first ? `<div class="card first">
          <div class="meta">${esc(first.platform)} · ${esc(day(first.published_at))} · date from ${esc(first.date_source)}</div>
          <div><a href="${safeHref(first.url)}" target="_blank" rel="noopener">${esc(first.title || first.text.slice(0, 140) || first.url)}</a></div>
          <div class="small muted" style="margin-top:4px">${esc(first.author || first.domain)}</div>
        </div>
        <p class="small muted">Earliest we could find in public sources, not proof of origin.</p>` : `<p class="muted small">No dated spreading item yet.</p>`}
        ${debunk ? `<h3 style="margin-top:18px">First correction</h3><div class="card first"><div class="meta">${esc(debunk.platform)} · ${esc(day(debunk.published_at))}</div>
          <a href="${safeHref(debunk.url)}" target="_blank" rel="noopener">${esc(debunk.title || debunk.url)}</a></div>` : ""}
      </div>
    </div>
    <div class="panel"><h3>By platform</h3>
      <table><thead><tr><th>Platform</th><th>First seen</th><th class="num">Spreading</th><th class="num">Neutral</th><th class="num">Correcting</th><th class="num">Engagement on spreading posts</th></tr></thead>
      <tbody>${rows.map(([p, v]) => `<tr><td>${esc(p)}</td><td class="mono">${esc(day(v.first_seen))}</td><td class="num">${v.amplifies}</td><td class="num">${v.reports}</td><td class="num">${v.debunks}</td><td class="num">${v.engagement.toLocaleString()}</td></tr>`).join("")}</tbody></table>
    </div>`;
}

function tickLabel(ts, span) {
  const iso = new Date(ts).toISOString();
  return span < 4 * 864e5 ? `${iso.slice(5, 10)} ${iso.slice(11, 16)}` : iso.slice(5, 10);
}

function timeline(items) {
  const pts = items.filter((i) => i.published_at && ["amplifies", "debunks", "reports"].includes(i.stance));
  if (!pts.length) return `<p class="muted small">No dated items to plot.</p>`;
  const lanes = [...new Set(pts.sort((a, b) => a.published_at.localeCompare(b.published_at)).map((i) => i.platform))];
  const t = pts.map((i) => Date.parse(i.published_at));
  let t0 = Math.min(...t), t1 = Math.max(...t);
  if (t1 - t0 < 864e5) { t0 -= 432e5; t1 += 432e5; }
  const W = 1000, L = 120, R = 16, laneH = 30, top = 10, H = top + lanes.length * laneH + 26;
  const x = (ts) => L + ((ts - t0) / (t1 - t0)) * (W - L - R);
  const ticks = 5;
  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Timeline of public items by platform">`;
  for (let k = 0; k <= ticks; k++) {
    const ts = t0 + ((t1 - t0) * k) / ticks;
    svg += `<line class="grid" x1="${x(ts)}" x2="${x(ts)}" y1="${top}" y2="${H - 20}"/><text x="${x(ts)}" y="${H - 6}" text-anchor="middle">${tickLabel(ts, t1 - t0)}</text>`;
  }
  lanes.forEach((ln, li) => {
    const y = top + li * laneH + laneH / 2;
    svg += `<line class="grid" x1="${L}" x2="${W - R}" y1="${y}" y2="${y}" stroke-dasharray="2 4"/><text class="lane-label" x="0" y="${y + 4}">${esc(ln)}</text>`;
  });
  const color = { amplifies: "var(--accent)", debunks: "var(--ok)", reports: "var(--neutral)" };
  pts.forEach((p) => {
    const y = top + lanes.indexOf(p.platform) * laneH + laneH / 2;
    const r = Math.min(9, 3.5 + Math.log10(1 + (p.engagement || 0)));
    svg += `<a href="${safeHref(p.url)}" target="_blank" rel="noopener"><circle cx="${x(Date.parse(p.published_at)).toFixed(1)}" cy="${y}" r="${r.toFixed(1)}" fill="${color[p.stance]}" fill-opacity="0.8">
      <title>${esc(p.platform)} · ${esc(day(p.published_at))} · ${esc(p.stance)}\n${esc((p.title || p.text).slice(0, 120))}</title></circle></a>`;
  });
  return svg + "</svg>";
}

// ---------- AI tab ----------
function tabAI() {
  const c = current, ct = (c.spread || {}).contamination || {};
  if (!c.probes.length) return `<p class="muted">${c.status === "running" ? "AI engines are checked after the trace..." : "No AI engines were asked. Check the Sources panel."}</p>`;
  const engines = [...new Set(c.probes.map((p) => `${p.engine} (${p.mode})`))];
  const questions = [...new Set(c.probes.map((p) => p.question))];
  const byKey = Object.fromEntries(c.probes.map((p) => [`${p.question}||${p.engine} (${p.mode})`, p]));
  const sel = c.probes.find((p) => p.id === state.probe) || c.probes.find((p) => p.verdict === "repeats") || c.probes[0];
  const itemsById = Object.fromEntries(c.items.map((i) => [i.id, i]));
  const prof = (c.spread || {}).profound || {};
  return `
    <div class="panel"><h3>Same neutral questions, every engine</h3>
      <table class="matrix"><thead><tr><th>Question</th>${engines.map((e) => `<th>${esc(e)}</th>`).join("")}</tr></thead>
      <tbody>${questions.map((q) => `<tr><td class="q">${esc(q)}</td>${engines.map((e) => {
        const p = byKey[`${q}||${e}`];
        if (!p) return "<td></td>";
        const v = p.verdict === "repeats" && !p.confirmed ? `<span class="chip unconfirmed">repeats?</span>` : `<span class="chip ${p.verdict}">${p.verdict}</span>`;
        return `<td><button class="cellbtn" data-probe="${p.id}" aria-label="Show answer">${v}</button></td>`;
      }).join("")}</tr>`).join("")}</tbody></table>
      ${c.probes.some((p) => p.verdict === "repeats" && !p.confirmed) ? `<p class="small muted">"repeats?" means the first reviewer flagged it but the skeptic pass didn't confirm, so it does not raise the grade.</p>` : ""}
    </div>
    ${sel ? `<div class="panel answer"><h3>${esc(sel.engine)} · ${esc(sel.mode)}</h3>
      <div class="small"><b>Q:</b> ${esc(sel.question)}</div>
      <blockquote>${esc(sel.error ? `Error: ${sel.error}` : sel.answer || "(empty answer)")}</blockquote>
      <div class="small"><span class="chip ${sel.verdict}">${sel.verdict}</span> <span class="muted">${esc(sel.verdict_reason)}</span></div>
      ${sel.citations.length ? `<h3 style="margin-top:14px">Sources this answer used</h3><ul class="cites">${sel.citations.map((ci) => {
        const traced = sel.cited_item_ids.map((id) => itemsById[id]).find((it) => it && (host(it.url) === host(ci.url) || it.domain === host(ci.url) || it.domain === (ci.title || "").toLowerCase()));
        return `<li><a href="${safeHref(ci.url)}" target="_blank" rel="noopener">${esc(ci.title || host(ci.url))}</a>${traced ? `<span class="traced">in trace · ${esc(traced.stance)}</span>` : ""}</li>`;
      }).join("")}</ul>` : ""}
    </div>` : ""}
    <div class="panel"><h3>Pages feeding AI answers that repeat the rumor</h3>
      ${ct.pages_feeding_ai?.length ? `<table><thead><tr><th>Page</th><th>Cited by</th></tr></thead><tbody>${ct.pages_feeding_ai.map((p) => `
        <tr><td><a href="${safeHref(p.url)}" target="_blank" rel="noopener">${esc(p.title || host(p.url))}</a></td><td class="small">${esc(p.engines.join(", "))}</td></tr>`).join("")}</tbody></table>`
        : `<p class="small muted">None. No confirmed answer repeated the rumor.</p>`}
    </div>
    <div class="panel"><h3>Profound citation tracking</h3>
      ${prof.rows?.length ? `<table><thead><tr><th>Domain</th><th>Model</th><th class="num">Citations</th><th>First cited</th></tr></thead><tbody>${prof.rows.map((r) => `
        <tr><td>${esc(r.hostname)}</td><td>${esc(r.model)}</td><td class="num">${esc(r.count)}</td><td class="mono">${esc(day(r.first_cited_at))}</td></tr>`).join("")}</tbody></table>`
        : `<p class="small muted">${esc(prof.status || "not run")}</p>`}
    </div>`;
}

// ---------- actions tab ----------
function tabActions() {
  const c = current;
  if (!c.actions.length) return `<p class="muted">${c.status === "done" ? "No actions drafted." : "Actions are drafted in the Synthesis stage."}</p>`;
  const approved = c.actions.filter((a) => a.status === "approved").length;
  const order = ["ad_package", "site_fix", "fact_sheet", "correction_request", "platform_flag"];
  const sorted = [...c.actions].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  return `
    <div class="actions-bar">
      <div class="gate-note">${c.mode === "counter"
        ? "Every ad package is a draft. Approve, edit or reject each one. Approved packages export as a bulk-import starting point with status PAUSED. Nothing is published and no money is spent from this tool."
        : "Every action is a draft. Approve, edit or reject each one. Only approved actions are exported, and nothing is sent from this tool."}</div>
      <a class="btn secondary small" href="/api/cases/${esc(c.id)}/export.csv${state.token ? `?token=${encodeURIComponent(state.token)}` : ""}" ${approved ? "" : `aria-disabled="true" style="pointer-events:none;opacity:.4"`}>Export ${approved} approved (CSV)</a>
    </div>
    ${sorted.map(actionCard).join("")}`;
}

function actionCard(a) {
  const locked = a.status !== "pending";
  const text = a.status === "approved" ? a.final_text : a.draft;
  const mailto = a.kind === "correction_request" && a.status === "approved"
    ? `<a class="btn secondary small" href="mailto:?subject=${encodeURIComponent((text.match(/^Subject: (.*)$/m) || [])[1] || "Correction request")}&body=${encodeURIComponent(text.replace(/^Subject: .*\n+/m, ""))}">Open in email</a>` : "";
  return `<div class="action" data-status="${a.status}" data-id="${a.id}">
    <div class="action-head"><span class="action-kind">${esc(KIND_LABEL[a.kind] || a.kind)}</span>
      <span class="action-title">${esc(a.title)}</span><span class="chip ${a.status}">${a.status}</span></div>
    <div class="action-body">
      ${a.target_url ? `<div class="why"><a href="${safeHref(a.target_url)}" target="_blank" rel="noopener">${esc(host(a.target_url))}</a>${a.rationale ? ` · ${esc(a.rationale)}` : ""}</div>` : a.rationale ? `<div class="why">${esc(a.rationale)}</div>` : ""}
      ${a.kind === "ad_package" ? adMeta(a.meta) : ""}
      <textarea ${locked ? "readonly" : ""} aria-label="Draft text">${esc(text)}</textarea>
      ${a.guardrail_flags.length ? `<ul class="flags">${a.guardrail_flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
      <div class="row-end">
        ${locked ? `${mailto}<button class="btn secondary small" data-decide="reset">Undo</button>`
          : `<button class="btn danger small" data-decide="reject">Reject</button><button class="btn small" data-decide="approve">Approve${a.guardrail_flags.length ? " anyway" : ""}</button>`}
      </div>
    </div></div>`;
}

// ---------- evidence tab ----------
function tabEvidence() {
  const c = current;
  const items = c.items.filter((i) => (state.filter === "all" ? i.stance !== "unrelated" : i.stance === state.filter))
    .sort((a, b) => (a.published_at || "9").localeCompare(b.published_at || "9"));
  const f = (k, label) => `<button data-filter="${k}" aria-pressed="${state.filter === k}">${label}</button>`;
  return `<div class="filters">${f("all", "All relevant")}${f("amplifies", "Spreading")}${f("reports", "Neutral")}${f("debunks", "Correcting")}${f("unrelated", "Unrelated")}</div>
    <table><thead><tr><th>Date</th><th>Platform</th><th>Item</th><th>Account / outlet</th><th>Label</th><th class="num">Engagement</th></tr></thead>
    <tbody>${items.map((i) => `<tr>
      <td class="mono" title="date from ${esc(i.date_source)}">${esc(day(i.published_at))}${i.date_source === "model-reported" ? "*" : ""}</td>
      <td>${esc(i.platform)}</td>
      <td><a href="${safeHref(i.url)}" target="_blank" rel="noopener">${esc((i.title || i.text || i.url).slice(0, 140))}</a>
        <div class="small muted">${esc(i.stance_reason)}</div></td>
      <td class="small">${esc(i.author || i.domain)}</td>
      <td><span class="chip ${i.stance}">${esc(i.stance)}</span>${i.stance === "amplifies" && !i.confirmed ? ` <span class="chip unconfirmed">unreviewed</span>` : ""}</td>
      <td class="num">${i.engagement ? i.engagement.toLocaleString() : ""}</td></tr>`).join("")}</tbody></table>
    <p class="small muted">* date reported by the search model, not by the platform; not used for "earliest instance".</p>`;
}

// ---------- log tab ----------
function tabLog() {
  const c = current;
  const lines = c.log.map((l) => `<div class="${l.level}"><span class="t">${esc(l.at.slice(11, 19))}</span><span class="s">${esc(l.stage)}</span>${esc(l.message)}</div>`).join("");
  const srcs = Object.entries(c.sources_status || {});
  return `<div class="grid-2">
    <div class="panel"><h3>Run log</h3><div class="log">${lines || "Waiting..."}</div></div>
    <div class="panel"><h3>Sources this run</h3>
      <table class="sources-table"><tbody>${srcs.map(([k, v]) => `<tr><td>${esc(k)}</td><td class="${v.startsWith("ok") ? "status-ok" : "status-bad"}">${esc(v)}</td></tr>`).join("")}</tbody></table>
      <div class="row-end" style="justify-content:flex-start;flex-wrap:wrap">
        ${c.status === "done" && !c.replay ? `<button class="btn secondary small" data-recheck>Re-check AI engines</button><button class="btn secondary small" data-save-replay>Save as replay</button>` : ""}
        <a class="btn secondary small" href="/api/cases/${esc(c.id)}/export.json${state.token ? `?token=${encodeURIComponent(state.token)}` : ""}">Download case JSON</a>
      </div>
      ${c.history?.length > 1 ? `<h3 style="margin-top:20px">Grade history</h3><table><tbody>${c.history.map((h) => `<tr><td class="mono">${esc(h.at.slice(0, 16).replace("T", " "))}</td><td>${esc(h.kind)}</td><td>Grade ${h.level}</td><td class="num">${h.repeats}/${h.answers} repeating</td></tr>`).join("")}</tbody></table>` : ""}
    </div></div>`;
}

// ---------- wiring ----------
function wireTab(el) {
  el.querySelectorAll("[data-probe]").forEach((b) => b.addEventListener("click", () => { state.probe = b.dataset.probe; drawTabBody(); }));
  el.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => { state.filter = b.dataset.filter; drawTabBody(); }));
  el.querySelectorAll("[data-decide]").forEach((b) => b.addEventListener("click", async () => {
    const card = b.closest(".action");
    const decision = b.dataset.decide;
    b.disabled = true;
    try {
      const updated = await api(`/api/cases/${current.id}/actions/${card.dataset.id}`, {
        method: "POST", body: JSON.stringify({ decision, text: decision === "approve" ? card.querySelector("textarea").value : null }),
      });
      current.actions = current.actions.map((a) => (a.id === updated.id ? updated : a));
      drawCase();
    } catch (err) {
      // Server may have refreshed the flags (e.g. trademark block); reload them, then show why.
      try { current = await api(`/api/cases/${current.id}`); drawCase(); } catch { /* keep current view */ }
      const fresh = document.querySelector(`.action[data-id="${card.dataset.id}"]`) || card;
      alertInline(fresh, err.message);
    }
  }));
  el.querySelector("[data-recheck]")?.addEventListener("click", async () => {
    await api(`/api/cases/${current.id}/recheck`, { method: "POST" });
    current.status = "running"; state.tab = "log"; drawCase(); listen(current.id);
  });
  el.querySelector("[data-save-replay]")?.addEventListener("click", async (e) => {
    const name = `${current.input.brand}-${current.id}`;
    const { name: saved } = await api(`/api/cases/${current.id}/save-replay`, { method: "POST", body: JSON.stringify({ name }) });
    e.target.textContent = `Saved as ${saved}`; e.target.disabled = true;
  });
}

function alertInline(node, msg) {
  const d = document.createElement("div");
  d.className = "error-box"; d.style.marginTop = "8px"; d.textContent = msg;
  node.querySelector(".action-body").appendChild(d);
}

async function showStatus() {
  const dlg = document.getElementById("status-dialog");
  const cfg = state.config || (state.config = await api("/api/config"));
  const row = (r) => `<tr><td>${esc(r.name)}</td><td class="${r.enabled ? "status-ok" : "status-bad"}">${r.enabled ? "ready" : esc(r.note)}</td></tr>`;
  document.getElementById("status-body").innerHTML = `
    <h3>Where we search</h3><table class="sources-table"><tbody>${cfg.sources.map(row).join("")}</tbody></table>
    <h3 style="margin-top:16px">AI engines we ask</h3><table class="sources-table"><tbody>${cfg.engines.map(row).join("")}</tbody></table>
    <h3 style="margin-top:16px">Profound</h3><p class="small ${cfg.profound.enabled ? "status-ok" : "status-bad"}">${cfg.profound.enabled ? "ready" : esc(cfg.profound.note)}</p>
    <p class="small muted">Model: <span class="mono">${esc(cfg.model)}</span>. "Ready" means configured, not tested; each run reports real status in its Log tab.</p>`;
  dlg.showModal();
}

// ================= Counter mode =================
function counterForm() {
  const shop = state.config.shopify || {};
  return `
    <h1>Point your surplus stock at a competitor's real weak spot.</h1>
    <p>Counter reads your inventory, listens for complaints about a competitor's product, keeps only complaints that are first-hand and on several platforms,
    then drafts ads for overstocked SKUs whose own product copy answers that complaint. Drafts only: you approve, export as paused, and launch from the ad platform yourself.</p>
    <form id="counter-form" autocomplete="off">
      <div class="row">
        <div class="field"><label for="c-ours">Our brand</label><input id="c-ours" name="our_brand" required maxlength="120"></div>
        <div class="field"><label for="c-cat">Category <span class="hint">optional</span></label><input id="c-cat" name="category" maxlength="120" placeholder="waterproof running shoes"></div>
      </div>
      <div class="row">
        <div class="field"><label for="c-comp">Competitor brand</label><input id="c-comp" name="competitor" required maxlength="120"></div>
        <div class="field"><label for="c-prod">Competitor product <span class="hint">optional</span></label><input id="c-prod" name="competitor_product" maxlength="200"></div>
      </div>
      <div class="field"><label>Inventory source</label>
        <div class="radio-row">
          <label class="radio"><input type="radio" name="inventory_source" value="shopify" ${shop.enabled ? "checked" : ""}> Shopify
            <span class="hint">${shop.enabled ? "connected" : esc(shop.note || "not configured")}</span></label>
          <label class="radio"><input type="radio" name="inventory_source" value="csv" ${shop.enabled ? "" : "checked"}> CSV</label>
        </div>
      </div>
      <div class="field" id="csv-field" ${shop.enabled ? "hidden" : ""}>
        <label for="c-csv">Inventory CSV <span class="hint">${esc((state.config.csv_fields || []).join(", "))}</span></label>
        <textarea id="c-csv" name="csv_text" rows="5" class="mono"></textarea>
        <div class="small" style="margin-top:6px"><input type="file" id="c-file" accept=".csv,text/csv"> <button type="button" class="linkish" id="c-sample">Load fictional sample (Northpace)</button></div>
      </div>
      <details class="rules"><summary>Business rules</summary>
        <div class="row">
          <div class="field"><label for="c-dir">Surplus when supply exceeds <span class="hint">days</span></label><input id="c-dir" name="dir_threshold_days" type="number" min="1" value="60"></div>
          <div class="field"><label for="c-mm">Minimum gross margin <span class="hint">%</span></label><input id="c-mm" name="min_margin_pct" type="number" min="0" max="100" value="30"></div>
        </div>
        <div class="row">
          <div class="field"><label for="c-cac">Max CAC <span class="hint">% of unit gross profit</span></label><input id="c-cac" name="cac_share_of_profit" type="number" min="1" max="100" value="50"></div>
          <div class="field"><label for="c-days">Campaign length <span class="hint">days</span></label><input id="c-days" name="campaign_days" type="number" min="1" value="30"></div>
        </div>
        <div class="row">
          <div class="field"><label for="c-hold">Holding cost <span class="hint">% of unit cost per year (assumption)</span></label><input id="c-hold" name="holding_cost_pct_year" type="number" min="0" max="100" value="25"></div>
          <div class="field"><label for="c-so">Stockout guard <span class="hint">never advertise under N days of supply</span></label><input id="c-so" name="stockout_days" type="number" min="0" value="14"></div>
        </div>
        <div class="field"><label for="c-land">Fallback landing URL <span class="hint">optional</span></label><input id="c-land" name="landing_url" type="url"></div>
      </details>
      <details class="rules"><summary>Verified competitor facts <span class="hint">optional, for price or spec comparisons</span></summary>
        <p class="small muted" style="margin:0 0 10px">Only facts with a source and a check date from the last 14 days can appear in ad copy, and only with the exact value you enter. Leave empty for no comparisons.</p>
        <div id="facts"></div>
        <button type="button" class="btn secondary small" id="add-fact">Add fact</button>
      </details>
      <div id="form-error"></div>
      <div class="row-end"><button class="btn" type="submit">Find openings</button></div>
    </form>`;
}

function wireCounterForm() {
  const form = document.getElementById("counter-form");
  const csvField = document.getElementById("csv-field");
  form.querySelectorAll("input[name=inventory_source]").forEach((r) => r.addEventListener("change", () => { csvField.hidden = form.inventory_source.value !== "csv"; }));
  document.getElementById("c-file").addEventListener("change", async (e) => {
    const f = e.target.files[0];
    if (f) document.getElementById("c-csv").value = await f.text();
  });
  document.getElementById("c-sample").addEventListener("click", async () => {
    document.getElementById("c-csv").value = await api("/api/samples/inventory.csv");
    if (!document.getElementById("c-ours").value) document.getElementById("c-ours").value = "Northpace";
    if (!document.getElementById("c-cat").value) document.getElementById("c-cat").value = "running shoes";
  });
  const factsEl = document.getElementById("facts");
  const today = new Date().toISOString().slice(0, 10);
  document.getElementById("add-fact").addEventListener("click", () => {
    const row = document.createElement("div");
    row.className = "fact-row";
    row.innerHTML = `<input placeholder="Fact: list price" data-k="fact" maxlength="200">
      <input placeholder="Value: $180" data-k="value" maxlength="120">
      <input placeholder="Source URL" data-k="source_url" type="url">
      <input type="date" data-k="checked_on" value="${today}">
      <button type="button" class="linkish" aria-label="Remove fact">Remove</button>`;
    row.querySelector("button").addEventListener("click", () => row.remove());
    factsEl.appendChild(row);
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries([...new FormData(form).entries()].filter(([k]) => !k.startsWith("fact")));
    for (const k of ["dir_threshold_days", "min_margin_pct", "cac_share_of_profit", "campaign_days", "holding_cost_pct_year", "stockout_days"]) body[k] = Number(body[k]);
    body.competitor_facts = [...factsEl.querySelectorAll(".fact-row")].map((r) => Object.fromEntries([...r.querySelectorAll("input")].map((i) => [i.dataset.k, i.value.trim()])))
      .filter((f) => f.fact || f.value || f.source_url);
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Starting...";
    try {
      const { id } = await api("/api/counter", { method: "POST", body: JSON.stringify(body) });
      state.tab = "opps";
      location.hash = `#/case/${id}`;
    } catch (err) {
      document.getElementById("form-error").innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
      btn.disabled = false; btn.textContent = "Find openings";
    }
  });
}

const money0 = (v) => (v === null || v === undefined ? "–" : `$${Math.round(Number(v)).toLocaleString()}`);
const money = (v) => (v === null || v === undefined ? "–" : `$${Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);

function drawCounter() {
  const c = current, sm = c.summary || {};
  if (!["opps", "inventory", "complaints", "actions", "log"].includes(state.tab)) state.tab = "opps";
  const pending = c.actions.filter((a) => a.status === "pending").length;
  const ci = c.input;
  view.innerHTML = `
    ${c.replay ? `<div class="banner">Recorded run, opened as a replay. No live calls were made.</div>` : ""}
    ${c.status === "failed" ? `<div class="error-box" style="margin-bottom:16px">${esc(c.log.at(-1)?.message || "Run failed")}</div>` : ""}
    <div class="case-head">
      <div>
        <div class="case-brand">Counter · ${esc(ci.our_brand)} · case ${esc(c.id)} · ${esc(day(c.created_at))}${c.elapsed_seconds ? ` · ran in ${c.elapsed_seconds}s` : ""}</div>
        <h1 class="case-claim">Where is ${esc(`${ci.competitor} ${ci.competitor_product}`.trim())} letting customers down, and what do we have in stock that fixes it?</h1>
        <div class="case-truth">Rules: surplus over <b>${ci.dir_threshold_days} days</b> of supply, margin at least <b>${ci.min_margin_pct}%</b>, max CAC <b>${ci.cac_share_of_profit}%</b> of unit profit. Inventory from <b>${ci.inventory_source === "shopify" ? "Shopify" : "CSV"}</b>.</div>
      </div>
      <div class="grade"><div class="grade-level">Openings</div>
        <div class="grade-label">${c.status === "done" ? `${sm.ad_packages || 0} ad draft${sm.ad_packages === 1 ? "" : "s"}` : c.status === "failed" ? "Failed" : "Running"}</div>
        <div class="grade-why">${c.status === "done" ? `${sm.verified_clusters || 0} verified pain point(s) x ${sm.surplus_skus || 0} surplus SKU(s)` : "Drafts appear after the reality check."}</div>
        <div class="risk"><span>Approval</span><span class="mono">${pending} pending</span></div>
      </div>
    </div>
    <div class="stepper" id="stepper"></div>
    <div class="kpis">
      ${kpi(sm.surplus_units != null ? sm.surplus_units.toLocaleString() : "–", `surplus units in ${sm.surplus_skus ?? "–"} of ${sm.skus ?? "–"} SKUs`)}
      ${kpi(sm.impact ? money0(sm.impact.cash_tied) : "–", "cash tied up in surplus", (sm.impact?.cash_tied || 0) > 0)}
      ${kpi(sm.impact ? money0(sm.impact.holding_cost_month) : "–", `holding cost / month (${sm.impact?.holding_pct_assumption ?? 25}%/yr assumed)`)}
      ${kpi(sm.verified_clusters ?? "–", "verified pain points", (sm.verified_clusters || 0) > 0)}
      ${kpi(sm.ad_packages ?? "–", "ad drafts to review")}
    </div>
    <div class="tabs" role="tablist">
      ${tabBtn("opps", "Openings")}
      ${tabBtn("actions", "Ad drafts", c.actions.length ? `<span class="count ${pending ? "attn" : ""}">${pending} pending</span>` : "")}
      ${tabBtn("inventory", "Inventory", `<span class="count">${c.skus.length}</span>`)}
      ${tabBtn("complaints", "Complaints", `<span class="count">${c.items.filter((i) => i.stance === "amplifies").length}</span>`)}
      ${tabBtn("log", "Log")}
    </div>
    <div id="tab-body"></div>`;
  drawStepper();
  view.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => { state.tab = t.dataset.tab; drawCounter(); }));
  drawTabBody();
}

function impactPanel(c) {
  const imp = c.summary?.impact;
  if (!imp) return "";
  const matched = new Set(c.matches.map((m) => m.sku));
  const addressable = c.skus.filter((s) => matched.has(s.sku));
  const sum = (k) => addressable.reduce((a, s) => a + (s[k] || 0), 0);
  return `<div class="panel"><h3>What the surplus costs, and what these drafts could move</h3>
    <div class="impact">
      <div><div class="v">${money0(imp.cash_tied)}</div><div class="k">cash tied up in all surplus</div></div>
      <div><div class="v">${money0(imp.holding_cost_month)}</div><div class="k">holding cost per month</div></div>
      <div><div class="v">${addressable.length ? money0(sum("revenue_unlocked")) : "–"}</div><div class="k">revenue if matched surplus sells</div></div>
      <div><div class="v">${addressable.length ? money0(sum("contribution_after_cac")) : "–"}</div><div class="k">gross profit after paying max CAC</div></div>
    </div>
    <p class="small muted">Inventory arithmetic. Holding cost uses your ${imp.holding_pct_assumption}%/yr assumption. "Matched" means SKUs with an ad draft; selling it all is the ceiling, not a forecast.</p>
    ${imp.stockout_guard.length ? `<div class="guard"><b>Stockout guard:</b> ${imp.stockout_guard.map((g) => `${esc(g.name)} <span class="mono">(${g.days_of_inventory} d)</span>`).join(", ")} excluded from ads: they're already selling fast, and ads would push them into a stockout.</div>` : ""}
    ${(c.summary.facts_refused || []).length ? `<div class="guard warn"><b>Competitor facts refused:</b> ${c.summary.facts_refused.map(esc).join("; ")}</div>` : ""}
  </div>`;
}

function tabOpps() {
  const c = current;
  if (!c.clusters.length) return impactPanel(c) + `<p class="muted">${c.status === "done" ? "No complaints about this product were found in public sources." : "Listening..."}</p>`;
  const skuBy = Object.fromEntries(c.skus.map((s) => [s.sku, s]));
  return impactPanel(c) + `<h3 style="margin:8px 0 10px">Competitor pain points</h3>` + c.clusters.map((cl) => {
    const matches = c.matches.filter((m) => m.cluster_id === cl.id);
    const trend = cl.recent_7d || cl.prior_7d ? `${cl.recent_7d} in last 7 d vs ${cl.prior_7d} prior` : "no recent dated posts";
    return `<div class="opp card">
      <div class="opp-head">
        <div><h2>${esc(cl.friction)}</h2><div class="small muted">${cl.mentions} mention(s) · ${cl.first_hand} first-hand · ${esc(cl.platforms.join(", "))} · ${esc(trend)} · ${cl.baseline_week}/wk baseline${cl.comment_to_view != null ? ` · YouTube comment-to-view ${(cl.comment_to_view * 100).toFixed(2)}%` : ""}</div></div>
        <span>${cl.spiking ? `<span class="chip amplifies" title="last 7 days vs weekly baseline (days 8 to 35)">spiking ${cl.spike}x</span> ` : cl.new_signal ? `<span class="chip amplifies" title="3+ mentions this week, none in the prior 4 weeks">new this week</span> ` : ""}<span class="chip ${cl.reality === "verified" ? "debunks" : cl.reality === "disputed" ? "amplifies" : "unknown"}">${esc(cl.reality)}</span></span>
      </div>
      <div class="small muted" style="margin:6px 0 10px">${esc(cl.reality_reason)}</div>
      <ul class="quotes">${cl.quotes.map((q) => `<li><span>"${esc(q.text)}"</span> <a href="${safeHref(q.url)}" target="_blank" rel="noopener">${esc(q.platform)}</a>${q.first_hand ? "" : ` <span class="muted small">(second-hand)</span>`}</li>`).join("")}</ul>
      ${cl.reality !== "verified" ? `<p class="small muted">Not used for ads: ${cl.reality === "disputed" ? "advertising against an unverified claim about a competitor is a false-advertising risk." : "not enough first-hand evidence yet."}</p>`
        : matches.length ? `<table class="match"><thead><tr><th>Our SKU</th><th>Why it answers this</th><th class="num">On hand</th><th class="num">Supply</th><th class="num">Margin</th><th class="num">Max CAC</th></tr></thead><tbody>
          ${matches.map((m) => { const s = skuBy[m.sku] || {}; return `<tr><td><b>${esc(s.name)}</b><div class="small muted mono">${esc(m.sku)}</div></td>
            <td>"${esc(m.evidence)}"<div class="small muted">${esc(m.angle)}</div></td><td class="num">${s.units_on_hand}</td>
            <td class="num">${s.days_of_inventory == null ? "no sales" : `${s.days_of_inventory} d`}</td><td class="num">${s.margin_pct}%</td><td class="num">${money(s.max_cac)}</td></tr>`; }).join("")}
          </tbody></table>` : `<p class="small muted">Verified, but no surplus SKU's own description answers it. No ad drafted.</p>`}
    </div>`;
  }).join("");
}

function tabInventory() {
  const c = current;
  if (!c.skus.length) return `<p class="muted">${c.status === "failed" ? "Inventory didn't load. See the error above." : "Loading inventory..."}</p>`;
  const rows = [...c.skus].sort((a, b) => b.flagged - a.flagged || (b.surplus_units - a.surplus_units));
  return `<table><thead><tr><th>SKU</th><th>Product</th><th class="num">On hand</th><th class="num">Sold / wk</th><th class="num">Days of supply</th><th class="num">Price</th><th class="num">Margin</th><th class="num">Max CAC</th><th class="num">Surplus units</th><th class="num">Cash tied</th><th class="num">Holding / mo</th><th>Rule</th></tr></thead>
    <tbody>${rows.map((s) => `<tr>
      <td class="mono">${esc(s.sku)}</td><td>${esc(s.name)}${s.variant ? ` <span class="muted">${esc(s.variant)}</span>` : ""}</td>
      <td class="num">${s.units_on_hand}</td><td class="num">${s.weekly_velocity}</td>
      <td class="num">${s.days_of_inventory == null ? "no sales" : s.days_of_inventory}</td><td class="num">${money(s.price)}</td>
      <td class="num">${s.margin_pct == null ? "–" : `${s.margin_pct}%`}</td><td class="num">${money(s.max_cac)}</td><td class="num">${s.surplus_units}</td>
      <td class="num">${money0(s.cash_tied)}</td><td class="num">${money0(s.holding_cost_month)}</td>
      <td><span class="chip ${s.flagged ? "debunks" : s.stockout_risk ? "amplifies" : "unknown"}">${s.flagged ? "surplus" : s.stockout_risk ? "stockout guard" : "skip"}</span> <span class="small muted">${esc(s.flag_reason)}</span></td></tr>`).join("")}</tbody></table>
    <p class="small muted">Days of supply = on hand / average daily sales over the window. Max CAC = (price - unit cost) x your CAC share. All arithmetic, no model.</p>`;
}

function tabComplaints() {
  const c = current;
  const items = c.items.filter((i) => i.stance === "amplifies").sort((a, b) => (b.engagement - a.engagement));
  if (!items.length) return `<p class="muted">No complaints labeled yet.</p>`;
  return `<table><thead><tr><th>Date</th><th>Platform</th><th>Post</th><th>Tags</th><th class="num">Engagement</th></tr></thead>
    <tbody>${items.map((i) => `<tr><td class="mono">${esc(day(i.published_at))}</td><td>${esc(i.platform)}</td>
      <td><a href="${safeHref(i.url)}" target="_blank" rel="noopener">${esc((i.title || i.text || i.url).slice(0, 120))}</a>${i.quote ? `<div class="small muted">"${esc(i.quote)}"</div>` : ""}</td>
      <td class="small">${esc(i.stance_reason.replaceAll("|", " · "))}</td><td class="num">${i.engagement ? i.engagement.toLocaleString() : ""}</td></tr>`).join("")}</tbody></table>`;
}

function adMeta(m) {
  if (!m) return "";
  const subst = (m.substantiation || []).length ? `<div class="small" style="margin:-4px 0 10px"><b>Comparison backed by:</b> ${m.substantiation.map(esc).join("; ")}</div>` : "";
  const brief = m.brief ? `<details class="brief"><summary>What the model saw (structured brief)</summary><pre>${esc(JSON.stringify(m.brief, null, 2))}</pre></details>` : "";
  const ue = m.unit_economics || {};
  const pos = m.positioning ? `<div class="positioning"><span class="action-kind">Positioning</span> ${esc(m.positioning)}</div>` : "";
  const econ = ue.unit_gross_profit ? `<div class="econ">
    <div><div class="v">${money(ue.unit_gross_profit)}</div><div class="k">gross profit per unit</div></div>
    <div><div class="v">${money0(ue.campaign_spend)}</div><div class="k">suggested spend over the campaign</div></div>
    <div><div class="v">${ue.breakeven_units.toLocaleString()}</div><div class="k">units to sell to cover the spend</div></div>
    <div><div class="v">${ue.days_to_clear_without_ads == null ? "never" : ue.days_to_clear_without_ads + " days"}</div><div class="k">to clear surplus at today's pace, no ads${ue.holding_cost_of_waiting != null ? ` (${money0(ue.holding_cost_of_waiting)} holding cost)` : ""}</div></div>
  </div>` : "";
  return pos + subst + brief + econ + `<div class="admeta">
    <span><b>${esc(m.sku)}</b></span><span>${m.units_on_hand} on hand</span><span>${m.surplus_units} surplus</span>
    <span>${m.days_of_inventory == null ? "no recent sales" : `${m.days_of_inventory} days of supply`}</span>
    <span>margin ${m.margin_pct}%</span><span>max CAC ${money(m.max_cac)}</span>
    <span title="${esc(m.budget_note)}">suggested ${money(m.suggested_daily_budget)}/day (heuristic)</span>
  </div>`;
}

// ================= Watch mode (live brand monitor) =================
const W = { data: null, sel: null, filter: "all", seen: new Set(), firstLoad: true, timer: null, poll: null, refresh: null, showLow: false, tracing: false };
const LEVEL_LABEL = { monitor: "Monitor", prepare: "Prepare", respond: "Respond", escalate: "Escalate" };
const VERACITY = { false: ["False", "fabricated outright"], misframed: ["Misframed", "real fact, wrong conclusion"],
  true_unflattering: ["True but unflattering", "accurate, just bad for the brand"], opinion: ["Opinion", "a take, not a factual claim"],
  unclear: ["Unclear", "needs a fact check before anyone speaks"] };
const TIER = { internal_brief: 0, faq_update: 1, correction_request: 2, community_note: 3, support_macro: 3, task: 3, holding_statement: 4, social_reply: 4 };
const TIER_LABEL = ["Prep", "1 · Where people check later: AI answers and search", "2 · The original source", "3 · Platform tools", "4 · Public reply (last resort)"];
const AMP = { stay_quiet: "Stay quiet (for now)", public_ok: "Public reply is OK", act: "Act now" };
const WKIND = { community_note: "Community note", holding_statement: "Holding statement", social_reply: "Social reply", support_macro: "Support macro", faq_update: "AI answer page",
  correction_request: "Correction request", internal_brief: "Internal brief", task: "Task" };
const STAGE_LABEL = { starting: "Starting", polling: "Polling sources", triage: "Reading new mentions", playbook: "Drafting playbook", ai_check: "Checking AI answers (Profound)", waiting: "Listening" };

function ago(iso) {
  if (!iso) return "undated";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (isNaN(s)) return "undated";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
const scoreClass = (s) => (s >= 70 ? "crit" : s >= 45 ? "high" : s >= 25 ? "mid" : "low");
const isThreat = (n) => n.threat_type !== "praise" && (n.severity >= 1 || n.score >= 25);

function watchForm() {
  return `
    <h1 class="hero-h">Type a brand. Get alerted the moment talk about it turns into a threat.</h1>
    <form id="watch-form" class="hero-form" autocomplete="off">
      <div class="hero-input">
        <input id="w-brand" name="brand" required maxlength="120" placeholder="Brand or product, e.g. Stanley" aria-label="Brand or product" autofocus>
        <button class="btn" type="submit">Start watching</button>
      </div>
      <details class="rules"><summary>More options (product, site, keywords, verified facts)</summary>
        <div class="row">
          <div class="field"><label for="w-product">Product or campaign</label><input id="w-product" name="product" maxlength="120" placeholder="e.g. Quencher tumbler"></div>
          <div class="field"><label for="w-domain">Official site</label><input id="w-domain" name="domain" maxlength="200" placeholder="stanley1913.com"></div>
        </div>
        <div class="field"><label for="w-kw">Extra keywords <span class="hint">comma separated</span></label><input id="w-kw" name="keywords" placeholder="e.g. stanley cup lead, stanley recall"></div>
        <div class="field"><label for="w-pos">Verified brand facts <span class="hint">drafts may use these; anything else becomes a [CONFIRM] placeholder</span></label>
        <textarea id="w-pos" name="position" rows="3" maxlength="2000"></textarea></div>
        <div class="field"><label for="w-aud">Brand audience <span class="hint">followers on your main channel; used to judge whether replying would amplify a rumor</span></label>
        <input id="w-aud" name="brand_audience" type="number" min="0" step="1000" placeholder="e.g. 2000000"></div>
      </details>
      <div id="form-error"></div>
    </form>
    <ol class="how">
      <li><b>Listens</b> across news, forums, Bluesky, YouTube and the open web (TikTok, X, Reddit via search), every 90 seconds.</li>
      <li><b>Reads every mention</b> and groups them into narratives with a 0 to 100 threat score.</li>
      <li><b>Alerts your team</b> when a narrative turns serious, speeds up, jumps platforms or hits the news.</li>
      <li><b>Drafts a response plan</b> with owners and ready-to-edit copy. A person approves everything; nothing is posted or sent.</li>
    </ol>`;
}

function wireWatchForm() {
  document.getElementById("watch-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    body.keywords = (body.keywords || "").split(",").map((s) => s.trim()).filter(Boolean);
    if ("brand_audience" in body) body.brand_audience = Number(body.brand_audience) || 0;
    const btn = e.target.querySelector("button[type=submit]");
    btn.disabled = true; btn.textContent = "Starting...";
    try {
      const { id } = await api("/api/watch", { method: "POST", body: JSON.stringify(body) });
      location.hash = `#/watch/${id}`;
    } catch (err) {
      document.getElementById("form-error").innerHTML = `<div class="error-box">${esc(err.message)}</div>`;
      btn.disabled = false; btn.textContent = "Start watching";
    }
  });
}

function stopWatchTimers() {
  clearInterval(W.timer); clearInterval(W.poll); clearTimeout(W.refresh);
  W.timer = W.poll = W.refresh = null;
}

async function renderWatch(id) {
  stopWatchTimers();
  W.data = null; W.sel = null; W.seen = new Set(); W.firstLoad = true; W.tracing = false;
  await loadWatch(id);
  if (!W.data.replay) {
    const url = `/api/cases/${id}/events${state.token ? `?token=${encodeURIComponent(state.token)}` : ""}`;
    state.es = new EventSource(url);
    state.es.onmessage = (msg) => {
      const ev = JSON.parse(msg.data);
      if (ev.type === "alert") notifyAlert(ev.alert);
      if (ev.type === "update" && W.data) { W.data.stage = ev.stage || W.data.stage; if (ev.next_poll_at) W.data.next_poll_at = ev.next_poll_at; drawWatchStatus(); }
      clearTimeout(W.refresh);
      W.refresh = setTimeout(() => loadWatch(id), 600);
    };
    W.poll = setInterval(() => loadWatch(id), 20000);   // safety net if the stream drops
  }
  W.timer = setInterval(drawWatchStatus, 1000);
}

async function loadWatch(id) {
  if (!location.hash.startsWith(`#/watch/${id}`)) return;
  const d = await api(`/api/watch/${id}`);
  const prevSel = W.sel;
  W.data = d;
  if (!W.sel || !d.narratives.some((n) => n.id === W.sel)) W.sel = (d.narratives.find(isThreat) || d.narratives[0] || {}).id || null;
  drawWatch(prevSel !== W.sel);
  d.mentions.forEach((m) => W.seen.add(m.id));
  W.firstLoad = false;
}

function drawWatch(selChanged) {
  const w = W.data;
  if (!view.querySelector(".watch")) {
    view.innerHTML = `<div class="watch">
      <div id="w-banner"></div>
      <div class="w-head">
        <div>
          <div class="case-brand" id="w-meta"></div>
          <h1 class="w-title">${esc(w.input.brand)}${w.input.product ? ` <span class="muted">/ ${esc(w.input.product)}</span>` : ""} <span class="live-pill" id="w-live"><i></i><b>LIVE</b></span></h1>
          <div class="w-sub" id="w-sub"></div>
        </div>
        <div class="w-controls" id="w-controls"></div>
      </div>
      <div class="kpis" id="w-kpis"></div>
      <div class="w-grid">
        <section>
          <div class="panel"><div class="panel-head"><h3>Narratives by threat</h3><span class="small muted" id="w-narr-note"></span></div><div id="w-board"></div></div>
          <div class="panel" id="w-detail"></div>
        </section>
        <aside>
          <div class="panel"><div class="panel-head"><h3>Alerts</h3><button class="linkish small" id="w-read">Mark all read</button></div><div id="w-alerts"></div></div>
          <div class="panel"><div class="panel-head"><h3>Live evidence</h3><span class="small muted" id="w-stream-count"></span></div>
            <div class="filters" id="w-filters"></div><div class="stream" id="w-stream"></div></div>
        </aside>
      </div>
      <div class="panel"><details><summary class="small">Sources and log</summary><div id="w-log"></div></details></div>
      <div id="toasts" class="toasts" aria-live="assertive"></div>
    </div>`;
    document.getElementById("w-read").addEventListener("click", async () => { await api(`/api/watch/${w.id}/alerts/read`, { method: "POST" }); loadWatch(w.id); });
  }
  document.getElementById("w-banner").innerHTML = w.replay
    ? `<div class="banner">Recorded watch: a saved snapshot, no live polling. Start a new watch for live data.</div>`
    : `<div class="banner subtle">Public sources only · not affiliated with ${esc(w.input.brand)} · nothing is posted or sent without your approval</div>`;
  drawWatchStatus();
  drawWatchKpis();
  drawBoard();
  drawAlerts();
  drawStream();
  drawWatchLog();
  drawDetail(selChanged);
}

function drawWatchStatus() {
  const w = W.data; if (!w) return;
  const live = w.live && w.status === "running";
  const pill = document.getElementById("w-live");
  if (!pill) return;
  pill.className = `live-pill ${live ? "" : "off"}`;
  pill.querySelector("b").textContent = w.replay ? "RECORDED" : live ? "LIVE" : w.status.toUpperCase();
  const next = w.next_poll_at ? Math.max(0, Math.round((new Date(w.next_poll_at) - Date.now()) / 1000)) : null;
  const stage = w.stage === "waiting" && next !== null ? `next poll in ${next}s` : (STAGE_LABEL[w.stage] || w.stage) + "...";
  document.getElementById("w-meta").textContent = `WATCH · started ${day(w.created_at)} · cycle ${w.cycles}${live ? " · " + stage : ""}`;
  const qs = quietStats(w);
  document.getElementById("w-sub").innerHTML = `Listening across news, forums, video and the open web · checks every ${w.poll_seconds}s${(w.channels || []).length ? ` · alerts to ${esc(w.channels.join(", "))}` : ""}`
    + (qs.plans ? ` · <b>recommended staying quiet on ${qs.quiet} of ${qs.plans} narrative${qs.plans === 1 ? "" : "s"}</b>${qs.avoided ? `, avoiding up to ${qs.avoided.toLocaleString()} extra exposures` : ""}` : "");
  const ctr = document.getElementById("w-controls");
  const sig = `${w.status}|${w.replay}|${typeof Notification !== "undefined" ? Notification.permission : "na"}|${w.actions.filter((a) => a.status === "approved").length}`;
  if (ctr.dataset.sig === sig) return;
  ctr.dataset.sig = sig;
  const approved = w.actions.filter((a) => a.status === "approved").length;
  const tok = state.token ? `?token=${encodeURIComponent(state.token)}` : "";
  ctr.innerHTML = w.replay ? "" : `
    <button class="btn small" data-ctl="poll">Poll now</button>
    <button class="btn small secondary" data-ctl="${w.status === "paused" ? "resume" : "pause"}">${w.status === "paused" ? "Resume" : "Pause"}</button>
    ${typeof Notification !== "undefined" && Notification.permission !== "granted" ? `<button class="btn small secondary" id="w-notify">Enable desktop alerts</button>` : ""}
    ${(w.channels || []).length ? `<button class="btn small secondary" id="w-test">Send top threat to ${esc(w.channels.join(" + "))}</button>` : ""}
    <button class="btn small secondary" id="w-save">Save replay</button>
    <a class="btn small secondary" href="/api/watch/${esc(w.id)}/export.csv${tok}" ${approved ? "" : `aria-disabled="true" style="pointer-events:none;opacity:.4"`}>Export ${approved} approved</a>`;
  ctr.querySelectorAll("[data-ctl]").forEach((b) => b.addEventListener("click", async () => {
    b.disabled = true;
    await api(`/api/watch/${w.id}/control`, { method: "POST", body: JSON.stringify({ command: b.dataset.ctl }) });
    loadWatch(w.id);
  }));
  document.getElementById("w-notify")?.addEventListener("click", async () => { await Notification.requestPermission(); ctr.dataset.sig = ""; drawWatchStatus(); });
  document.getElementById("w-test")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Sending...";
    try { const r = await api(`/api/watch/${w.id}/test-alert`, { method: "POST" }); e.target.textContent = r.delivered.length ? `Sent to ${r.delivered.join(", ")}` : "Not delivered: see log"; }
    catch (err) { e.target.textContent = err.message; }
    setTimeout(() => { ctr.dataset.sig = ""; drawWatchStatus(); }, 4000);
  });
  document.getElementById("w-save")?.addEventListener("click", async (e) => {
    const name = `${w.input.brand}-watch`.toLowerCase().replace(/[^a-z0-9-]+/g, "-");
    const { name: saved } = await api(`/api/cases/${w.id}/save-replay`, { method: "POST", body: JSON.stringify({ name }) });
    e.target.textContent = `Saved: ${saved}`;
  });
}

function drawWatchKpis() {
  const w = W.data;
  const rel = w.mentions.filter((m) => m.relevant && m.triaged);
  const dayAgo = Date.now() - 86400000;
  const m24 = rel.filter((m) => new Date(m.published_at || m.found_at).getTime() >= dayAgo);
  const neg = m24.length ? Math.round((m24.filter((m) => m.sentiment === "negative" || m.sentiment === "mixed").length / m24.length) * 100) : 0;
  const threats = w.narratives.filter((n) => isThreat(n) && n.status !== "fading");
  const top = w.narratives.reduce((a, n) => Math.max(a, n.score), 0);
  const unread = w.alerts.filter((a) => !a.read).length;
  document.getElementById("w-kpis").innerHTML =
    kpi(m24.length, `mentions in 24h (${rel.length} total)`) + kpi(`${neg}%`, "negative or mixed (24h)", neg >= 40) +
    kpi(threats.length, "active threat narratives", threats.length > 0) +
    `<div class="kpi"><div class="v score-${scoreClass(top)}">${top}<span class="of">/100</span></div><div class="k">top threat score</div></div>` +
    kpi(unread, "unread alerts", unread > 0);
}

function spark(vals) {
  const max = Math.max(1, ...vals);
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * 120},${26 - (v / max) * 22}`).join(" ");
  return `<svg class="spark" viewBox="0 0 120 28" preserveAspectRatio="none" aria-hidden="true"><polyline points="0,28 ${pts} 120,28" class="area"/><polyline points="${pts}" class="line"/></svg>`;
}

function narrCard(n) {
  return `<button class="narr ${n.id === W.sel ? "on" : ""}" data-nid="${esc(n.id)}" type="button">
    <div class="narr-top">
      <span class="chip type">${esc(n.threat_type.replace("_", " "))}</span>
      <span class="chip st-${esc(n.status)}">${esc(n.status)}</span>
      ${n.playbook ? `<span class="chip lvl-${esc(n.playbook.response_level)}">${esc(LEVEL_LABEL[n.playbook.response_level])}</span>` : ""}
      ${n.ai_exposure && n.ai_exposure.answers ? `<span class="chip ai-hit" title="Profound: AI answers repeating or citing this">In AI answers</span>` : ""}
      <span class="score-num score-${scoreClass(n.score)}">${n.score}</span>
    </div>
    <div class="narr-title">${esc(n.title)}</div>
    <div class="narr-claim">${esc(n.claim || n.summary)}</div>
    <div class="narr-foot">
      <div class="narr-meta">${plural(n.count, "mention")} · ${n.count_24h} in 24h${n.velocity ? ` · ${n.velocity}x rate` : ""}<br>${esc(n.platforms.join(", "))} · first ${esc(ago(n.first_seen))}</div>
      ${spark(n.spark_days && n.spark_days.some(Boolean) && !n.spark.some(Boolean) ? n.spark_days : n.spark)}
    </div>
    <div class="scorebar"><span class="score-bg-${scoreClass(n.score)}" style="width:${n.score}%"></span></div>
  </button>`;
}

function drawBoard() {
  const w = W.data;
  const threats = w.narratives.filter(isThreat);
  const low = w.narratives.filter((n) => !isThreat(n));
  document.getElementById("w-narr-note").textContent = w.narratives.length ? `${threats.length} threats · ${low.length} low-risk` : "";
  const board = document.getElementById("w-board");
  if (!w.narratives.length) {
    const errs = w.stage === "waiting" ? Object.entries(w.sources_status || {}).filter(([, v]) => v.startsWith("error")) : [];
    board.innerHTML = `<div class="empty card">${w.cycles && w.stage === "waiting" ? "No mentions found yet." : "First sweep running: pulling the last 7 days of public mentions, then reading every one..."}
      ${errs.length ? `<div class="small muted" style="margin-top:6px">Some sources didn't respond; details under Sources and log.</div>` : ""}</div>`;
    return;
  }
  board.innerHTML = `<div class="board">${threats.map(narrCard).join("") || `<div class="empty card">No threat narratives right now. Low-risk chatter is below.</div>`}</div>
    ${low.length ? `<button class="linkish small low-toggle" id="w-low">${W.showLow ? "Hide" : "Show"} low-risk chatter (${low.length})</button>
    ${W.showLow ? `<div class="board low">${low.map(narrCard).join("")}</div>` : ""}` : ""}`;
  board.querySelectorAll("[data-nid]").forEach((b) => b.addEventListener("click", () => { W.sel = b.dataset.nid; W.tracing = false; drawBoard(); drawDetail(true); drawStream(); document.getElementById("w-detail").scrollIntoView({ behavior: "smooth", block: "start" }); }));
  document.getElementById("w-low")?.addEventListener("click", () => { W.showLow = !W.showLow; drawBoard(); });
}

function drawAlerts() {
  const w = W.data;
  const el = document.getElementById("w-alerts");
  el.innerHTML = w.alerts.length ? `<ul class="alerts">${w.alerts.slice(0, 12).map((a) => `
    <li class="al ${a.read ? "" : "unread"}" data-anid="${esc(a.narrative_id)}">
      <span class="lvl lvl-${esc(a.level)}"></span>
      <div><div class="al-title">${esc(a.title)}</div><div class="al-reason">${esc(a.reason)}</div>
      <div class="al-meta">${esc(ago(a.at))}${a.delivered.length ? ` · sent to ${esc(a.delivered.join(", "))}` : ""}</div></div></li>`).join("")}</ul>`
    : `<div class="empty">No alerts yet. You'll get one when a narrative turns serious, speeds up, jumps platforms or reaches the news.</div>`;
  el.querySelectorAll("[data-anid]").forEach((li) => li.addEventListener("click", () => { if (li.dataset.anid) { W.sel = li.dataset.anid; drawBoard(); drawDetail(true); drawStream(); } }));
}

function streamRow(m, fresh) {
  return `<a class="sr ${fresh ? "fresh" : ""} sev-${m.severity}" href="${safeHref(m.url)}" target="_blank" rel="noopener noreferrer">
    <div class="sr-top"><span class="plat">${esc(m.platform)}</span><span class="chip ${m.sentiment === "negative" ? "amplifies" : m.sentiment === "positive" ? "debunks" : "reports"}">${esc(m.sentiment)}</span>
      ${m.stance !== "neutral" ? `<span class="chip ${m.stance === "spreading" ? "amplifies" : "debunks"}">${esc(m.stance)}</span>` : ""}
      <span class="sr-time">${esc(ago(m.published_at || m.found_at))}</span></div>
    <div class="sr-text">${esc(m.summary || m.title || m.text.slice(0, 200))}</div>
    <div class="sr-by">${esc(m.author || m.domain || host(m.url))}${m.engagement ? ` · ${m.engagement} engagements` : ""}</div></a>`;
}

function drawStream() {
  const w = W.data;
  const plats = [...new Set(w.mentions.filter((m) => m.relevant && m.triaged).map((m) => m.platform))].sort();
  const opts = [["all", "All"], ["threats", "Threats"], ["negative", "Negative"], ["narrative", "This narrative"], ...plats.map((p) => [`p:${p}`, p])];
  document.getElementById("w-filters").innerHTML = opts.map(([k, l]) => `<button type="button" data-f="${esc(k)}" aria-pressed="${W.filter === k}">${esc(l)}</button>`).join("");
  document.querySelectorAll("#w-filters [data-f]").forEach((b) => b.addEventListener("click", () => { W.filter = b.dataset.f; drawStream(); }));
  // Show what has been read and kept; unread items appear once triage confirms they are about the brand.
  let rows = w.mentions.filter((m) => m.relevant && m.triaged);
  if (W.filter === "threats") rows = rows.filter((m) => m.severity >= 2);
  else if (W.filter === "negative") rows = rows.filter((m) => m.sentiment === "negative" || m.sentiment === "mixed");
  else if (W.filter === "narrative") rows = rows.filter((m) => m.narrative_id === W.sel);
  else if (W.filter.startsWith("p:")) rows = rows.filter((m) => m.platform === W.filter.slice(2));
  rows = [...rows].sort((a, b) => (b.published_at || b.found_at).localeCompare(a.published_at || a.found_at));
  document.getElementById("w-stream-count").textContent = `${rows.length} shown`;
  document.getElementById("w-stream").innerHTML = rows.length
    ? rows.slice(0, 150).map((m) => streamRow(m, !W.firstLoad && !W.seen.has(m.id))).join("")
    : `<div class="empty">${w.mentions.some((m) => !m.triaged) ? `Reading ${w.mentions.filter((m) => !m.triaged).length} new mentions...` : w.cycles ? "Nothing matches this filter." : "Listening..."}</div>`;
}

function drawDetail(force) {
  const w = W.data;
  const el = document.getElementById("w-detail");
  const n = w.narratives.find((x) => x.id === W.sel);
  if (!n) { el.innerHTML = ""; return; }
  const acts = w.actions.filter((a) => a.narrative_id === n.id);
  const sig = JSON.stringify([n.id, n.score, n.count, n.playbook?.generated_at, acts.map((a) => a.id + a.status), W.tracing, n.trace_case_id]);
  if (!force && el.dataset.sig === sig) return;
  // Keep any unsaved edits the reviewer typed.
  const edits = {};
  el.querySelectorAll("textarea[data-aid]").forEach((t) => { edits[t.dataset.aid] = t.value; });
  const focused = document.activeElement?.dataset?.aid;
  el.dataset.sig = sig;
  const pb = n.playbook;
  const parts = Object.entries(n.score_parts || {}).map(([k, v]) => `<span>${esc(k)} <b>${v}</b></span>`).join("");
  el.innerHTML = `<div class="detail card">
    <div class="detail-head">
      <div><div class="case-brand">NARRATIVE · ${esc(n.threat_type.replace("_", " "))} · ${esc(n.status)}</div><h2 class="detail-title">${esc(n.title)}</h2>
      <div class="muted small">${esc(n.claim || n.summary)}</div></div>
      <div class="detail-score"><div class="score-num big score-${scoreClass(n.score)}">${n.score}</div><div class="small muted">threat score</div></div>
    </div>
    <div class="parts">${parts}</div>
    ${aiBlock(n)}
    ${pb ? `
      <div class="level-row lvl-bg-${esc(pb.response_level)}"><div class="lvl-name">${esc(LEVEL_LABEL[pb.response_level])}</div><div>${esc(pb.level_reason)}</div></div>
      ${judgmentBlocks(pb)}
      <dl class="assess">
        <div><dt>What's being said</dt><dd>${esc(pb.what)}</dd></div>
        <div><dt>Who's carrying it</dt><dd>${esc(pb.who)}</dd></div>
        <div><dt>How fast</dt><dd>${esc(pb.how_fast)}</dd></div>
        <div><dt>Why it matters</dt><dd>${esc(pb.why_it_matters)}</dd></div>
      </dl>
      <div class="two-lists">
        ${pb.do_not.length ? `<div><h3>Don't</h3><ul>${pb.do_not.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
        ${pb.watch_for.length ? `<div><h3>Change the plan if</h3><ul>${pb.watch_for.map((x) => `<li>${esc(x)}</li>`).join("")}</ul></div>` : ""}
      </div>
      <div class="panel-head"><h3>Recommended actions · ${acts.filter((a) => a.status === "pending").length} awaiting approval</h3>
        <span class="small muted">${pb.by === "template" ? "standard plan" : `drafted by ${esc(pb.by)}`} · ${esc(ago(pb.generated_at))}</span></div>
      <div>${groupByTier(acts)}</div>`
    : `<div class="empty">No response plan yet. Plans are drafted automatically once a narrative's threat score reaches 45, or build one now.</div>`}
    <div class="detail-tools">
      <button class="btn small ${pb ? "secondary" : ""}" id="w-pb">${pb ? "Rebuild plan" : "Build response plan"}</button>
      ${n.trace_case_id ? `<a class="btn small secondary" href="#/case/${esc(n.trace_case_id)}">Open deep trace</a>` : `<button class="btn small secondary" id="w-trace-open">Deep trace + AI engine check</button>`}
      <details class="facts"><summary class="small">Verified brand facts and audience size</summary>
        <textarea id="w-pos" rows="3" maxlength="2000" placeholder="e.g. No recall has been issued. Lead is sealed under a steel cap and never touches the drink.">${esc(w.input.position)}</textarea>
        <label class="small" for="w-aud2" style="margin-top:8px">Brand audience (followers on main channel)</label>
        <input id="w-aud2" type="number" min="0" step="1000" value="${w.input.brand_audience || ""}" placeholder="unknown">
        <div class="row-end"><button class="btn small secondary" id="w-pos-save">Save facts and rebuild plan</button></div></details>
    </div>
    ${W.tracing ? `<form class="trace-form" id="w-trace">
      <p class="small muted">Runs the full trace on this narrative: origin, platform hops, and whether AI answer engines repeat it.</p>
      <div class="field"><label>The claim, as people repeat it</label><textarea name="claim" rows="2" required minlength="5">${esc(n.claim || n.title)}</textarea></div>
      <div class="field"><label>What is true <span class="hint">the brand's position</span></label><textarea name="truth" rows="2" required minlength="5">${esc(w.input.position)}</textarea></div>
      <div class="row-end"><button class="btn small secondary" type="button" id="w-trace-cancel">Cancel</button><button class="btn small" type="submit">Start deep trace</button></div></form>` : ""}
  </div>`;
  el.querySelectorAll("textarea[data-aid]").forEach((t) => { if (edits[t.dataset.aid] !== undefined) t.value = edits[t.dataset.aid]; });
  if (focused) el.querySelector(`textarea[data-aid="${focused}"]`)?.focus();
  wireDetail(n);
}

function aiBlock(n) {
  const ex = n.ai_exposure || {};
  if (!ex.checked) return "";
  const counts = (ex.citation_counts || []).filter((r) => r.count);
  return `<div class="ai-box ${ex.answers ? "hit" : ""}">
    <div class="ai-head"><b>${ex.answers ? `In AI answers: ${ex.answers} of ${ex.checked}` : `Not in AI answers yet (0 of ${ex.checked})`}</b>
      <span class="small muted">Profound · ${esc(W.data.ai_category_name || "")} · ${esc(ago(W.data.ai_checked_at))}</span></div>
    ${ex.answers ? `<div class="small">${esc(ex.models.join(", "))} already ${ex.answers === 1 ? "repeats or cites" : "repeat or cite"} this narrative. Fix the pages they cite first.</div>
      <ul class="ai-ex">${ex.examples.map((e) => `<li><span class="plat">${esc(e.model)}</span> <span class="muted small">"${esc(e.prompt)}"</span><blockquote>${esc(e.snippet)}</blockquote><span class="small muted">${esc(e.why)}</span></li>`).join("")}</ul>`
      : `<div class="small muted">Recent answers for this category don't cite or repeat it. Watching for the moment they do.</div>`}
    ${counts.length ? `<div class="small muted" style="margin-top:6px">Citations of sites carrying it (30 days): ${counts.slice(0, 6).map((r) => `${esc(r.hostname)} on ${esc(r.model)}: ${esc(r.count)}`).join(" · ")}</div>` : ""}
  </div>`;
}

function watchActionCard(a) {
  const locked = a.status !== "pending";
  return `<div class="action" data-status="${esc(a.status)}" data-aid-card="${esc(a.id)}">
    <div class="action-head"><span class="action-kind">${esc(WKIND[a.kind] || a.kind)}</span><span class="action-title">${esc(a.title)}</span><span class="chip ${esc(a.status)}">${esc(a.status)}</span></div>
    <div class="action-body">
      <div class="owner-row">${a.owner ? `<span><b>Owner</b> ${esc(a.owner)}</span>` : ""}${a.timing ? `<span><b>When</b> ${esc(a.timing)}</span>` : ""}${a.channel ? `<span><b>Where</b> ${esc(a.channel)}</span>` : ""}${a.target_url ? `<span><b>Target</b> <a href="${safeHref(a.target_url)}" target="_blank" rel="noopener noreferrer">${esc(host(a.target_url))}</a></span>` : ""}</div>
      ${a.raci && a.raci.A ? `<div class="raci"><span><b>R</b> ${esc(a.raci.R)}</span><span><b>A</b> ${esc(a.raci.A)}</span>${a.raci.C.length ? `<span><b>C</b> ${esc(a.raci.C.join(", "))}</span>` : ""}${a.raci.I.length ? `<span><b>I</b> ${esc(a.raci.I.join(", "))}</span>` : ""}</div>` : ""}
      ${a.why ? `<div class="why">${esc(a.why)}</div>` : ""}
      ${a.draft || a.final_text ? `<textarea data-aid="${esc(a.id)}" ${locked ? "readonly" : ""}>${esc(a.status === "approved" ? a.final_text : a.draft)}</textarea>` : ""}
      ${a.flags.length ? `<ul class="flags">${a.flags.map((f) => `<li>${esc(f)}</li>`).join("")}</ul>` : ""}
      <div class="row-end">${locked ? `<button class="btn small secondary" data-dec="reset">Undo</button>`
        : `<button class="btn small danger" data-dec="reject">Reject</button><button class="btn small" data-dec="approve">Approve${a.draft ? " as edited" : ""}</button>`}</div>
    </div></div>`;
}

function wireDetail(n) {
  const w = W.data;
  const el = document.getElementById("w-detail");
  el.querySelectorAll("[data-aid-card]").forEach((card) => card.querySelectorAll("[data-dec]").forEach((b) => b.addEventListener("click", async () => {
    const t = card.querySelector("textarea");
    b.disabled = true;
    try {
      await api(`/api/watch/${w.id}/actions/${card.dataset.aidCard}`, { method: "POST", body: JSON.stringify({ decision: b.dataset.dec, text: t ? t.value : null }) });
    } catch (err) { b.disabled = false; alertInline(card, err.message); return; }
    await loadWatch(w.id); drawDetail(true); drawWatchStatus();
  })));
  document.getElementById("w-pb")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Drafting plan...";
    try { await api(`/api/watch/${w.id}/narratives/${n.id}/playbook`, { method: "POST" }); } catch (err) { e.target.textContent = err.message; return; }
    await loadWatch(w.id); drawDetail(true);
  });
  document.getElementById("w-pos-save")?.addEventListener("click", async (e) => {
    e.target.disabled = true; e.target.textContent = "Saving...";
    await api(`/api/watch/${w.id}/position`, { method: "POST", body: JSON.stringify({ position: document.getElementById("w-pos").value, brand_audience: Number(document.getElementById("w-aud2").value) || 0 }) });
    e.target.textContent = "Drafting plan...";
    await api(`/api/watch/${w.id}/narratives/${n.id}/playbook`, { method: "POST" });
    await loadWatch(w.id); drawDetail(true);
  });
  document.getElementById("w-trace-open")?.addEventListener("click", () => { W.tracing = true; drawDetail(true); });
  document.getElementById("w-trace-cancel")?.addEventListener("click", () => { W.tracing = false; drawDetail(true); });
  document.getElementById("w-trace")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const body = Object.fromEntries(new FormData(e.target).entries());
    const btn = e.target.querySelector("button[type=submit]"); btn.disabled = true; btn.textContent = "Starting...";
    try {
      const { id } = await api(`/api/watch/${w.id}/narratives/${n.id}/trace`, { method: "POST", body: JSON.stringify(body) });
      location.hash = `#/case/${id}`;
    } catch (err) { btn.disabled = false; btn.textContent = err.message; }
  });
}

function drawWatchLog() {
  const w = W.data;
  const src = Object.entries(w.sources_status).map(([k, v]) => `<tr><td>${esc(k)}</td><td class="${v.startsWith("ok") ? "status-ok" : "status-bad"}">${esc(v)}</td><td class="mono small muted">${esc(ago(w.source_last_run[k]))}</td></tr>`).join("");
  const meta = [["Reading with", w.triage_mode || "pending"], ["AI answer check (Profound)", w.ai_status || "pending"],
    ["Team alerts", (w.channels || []).join(", ") || "in-app only (set SLACK_WEBHOOK_URL or ALERT_WEBHOOK_URL)"]]
    .map(([k, v]) => `<tr><td>${esc(k)}</td><td>${esc(v)}</td><td></td></tr>`).join("");
  document.getElementById("w-log").innerHTML = `<table class="sources-table"><tbody>${meta}${src}</tbody></table>
    <div class="log" style="margin-top:12px">${[...w.log].reverse().slice(0, 80).map((l) => `<div class="${esc(l.level)}"><span class="t">${esc((l.at || "").slice(11, 19))}</span><span class="s">${esc(l.stage)}</span>${esc(l.message)}</div>`).join("")}</div>`;
}

function notifyAlert(a) {
  const box = document.getElementById("toasts");
  if (box) {
    const t = document.createElement("div");
    t.className = `toast lvl-b-${a.level}`;
    t.innerHTML = `<b>${esc(a.title)}</b><div>${esc(a.reason)}</div>`;
    t.addEventListener("click", () => { if (a.narrative_id) { W.sel = a.narrative_id; drawBoard(); drawDetail(true); } t.remove(); });
    box.prepend(t);
    setTimeout(() => t.remove(), 9000);
  }
  try {
    if (typeof Notification !== "undefined" && Notification.permission === "granted" && a.level !== "info") {
      new Notification(`Contagion: ${a.title}`, { body: a.reason, tag: a.id });
    }
  } catch { /* some browsers block notifications from this context */ }
}

// Start the router last so every module-level constant above is initialized.
route();


// ---------- judgment layer (veracity, amplification, channel order) ----------
function judgmentBlocks(pb) {
  const v = VERACITY[pb.veracity] || VERACITY.unclear;
  const a = pb.amplification || {};
  const src = pb.original_source || {};
  const ver = `<div class="judge">
    <div class="judge-head"><h3>How true is it?</h3><span class="chip ver-${esc(pb.veracity || "unclear")}">${esc(v[0])}</span><span class="small muted">${esc(v[1])}</span></div>
    ${pb.true_part ? `<div class="small"><b>True part:</b> ${esc(pb.true_part)}</div>` : ""}
    ${pb.missing_context ? `<div class="small"><b>Missing context:</b> ${esc(pb.missing_context)}</div>` : ""}
    ${pb.veracity === "misframed" || pb.veracity === "true_unflattering" ? `<div class="small rule">Answer by agreeing with the true part and adding context. Never deny it.</div>` : ""}
    ${pb.veracity_basis ? `<div class="small muted">Basis: ${esc(pb.veracity_basis)}</div>` : ""}
  </div>`;
  const sc = pb.scct || {};
  const scctHtml = sc.strategy ? `<div class="judge">
    <div class="judge-head"><h3>Crisis response strategy</h3><span class="chip scct-${esc(sc.strategy)}">${esc(sc.strategy_name)}</span>
      <span class="small muted">SCCT cluster: ${esc(sc.cluster)}</span></div>
    <div class="small">${esc(sc.how)}${sc.bolster ? " Add bolstering: remind people of relevant goodwill, briefly." : ""}</div>
    <div class="small muted">${esc(sc.cluster_why)} ${esc(sc.note)}</div>
  </div>` : "";
  const sh = (pb.stakeholders || []).filter((r) => r.quadrant !== "Monitor");
  const shHtml = sh.length ? `<div class="judge"><div class="judge-head"><h3>Who to brief first</h3><span class="small muted">Mendelow power/interest grid</span></div>
    <table class="sh"><tbody>${sh.map((r) => `<tr><td><b>${esc(r.stakeholder)}</b></td><td><span class="chip q-${r.rank}">${esc(r.quadrant)}</span></td><td class="small">${esc(r.action)}</td></tr>`).join("")}</tbody></table></div>` : "";
  if (!a.verdict) return ver + scctHtml + shHtml;
  const amp = `<div class="judge amp-${esc(a.verdict)}">
    <div class="judge-head"><h3>Would replying amplify it?</h3><span class="chip amp-chip-${esc(a.verdict)}">${esc(AMP[a.verdict] || a.verdict)}</span></div>
    <div class="small">${esc(a.why)}</div>
    <div class="reach-row">
      <div><div class="v">${Number(a.rumor_reach || 0).toLocaleString()}</div><div class="k">rumor reach (engagements found)</div></div>
      <div><div class="v">${a.brand_audience ? Number(a.brand_audience).toLocaleString() : "unknown"}</div><div class="k">brand audience</div></div>
      <div><div class="v">${a.exposure_avoided ? "up to " + Number(a.exposure_avoided).toLocaleString() : "–"}</div><div class="k">extra exposure avoided by not replying</div></div>
    </div>
    ${(a.triggers || []).length ? `<div class="small"><b>Respond publicly only if:</b><ul class="trig">${a.triggers.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>` : ""}
    <div class="small muted">${esc(a.reach_note || "")}</div>
    ${src.url ? `<div class="small" style="margin-top:6px"><b>Original source to ask for a correction:</b> <a href="${safeHref(src.url)}" target="_blank" rel="noopener noreferrer">${esc(src.author || host(src.url))}</a> <span class="muted">· ${esc(src.why || "")}</span></div>` : ""}
  </div>`;
  return ver + scctHtml + amp + shHtml;
}

function groupByTier(acts) {
  let html = "", last = -1;
  for (const a of acts) {
    const t = TIER[a.kind] ?? 3;
    if (t !== last) { html += `<div class="tier-label">${esc(TIER_LABEL[t])}</div>`; last = t; }
    html += watchActionCard(a);
  }
  return html;
}

function quietStats(w) {
  const pbs = (w.narratives || []).map((n) => n.playbook).filter(Boolean);
  const quiet = pbs.filter((p) => p.amplification?.verdict === "stay_quiet");
  const avoided = quiet.reduce((s, p) => s + (p.amplification.exposure_avoided || 0), 0);
  return { plans: pbs.length, quiet: quiet.length, avoided };
}
