// Contagion UI. Vanilla JS, no build step. All scraped text is escaped before rendering.

const view = document.getElementById("view");
const state = { config: null, token: sessionStorageGet("contagion_token") || "", es: null, tab: "spread", filter: "all", probe: null };

const STAGES = [["discovery", "Discovery"], ["analysis", "Analysis"], ["cross_reference", "Cross-referencing"], ["synthesis", "Synthesis"]];
const KIND_LABEL = { fact_sheet: "Fact sheet", site_fix: "Site fix", correction_request: "Correction request", platform_flag: "Platform review flag" };
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
route();

async function route() {
  if (state.es) { state.es.close(); state.es = null; }
  const m = location.hash.match(/^#\/case\/([\w-]+)/);
  try {
    if (!state.config) state.config = await api("/api/config");
    if (m) await renderCase(m[1], true);
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
        </form>
      </section>
      <aside>
        <div class="side-section">
          <h3>Recent cases</h3>
          <div class="list">${cases.length ? cases.map(caseRow).join("") : `<div class="empty">No cases yet.</div>`}</div>
        </div>
        <div class="side-section">
          <h3>Recorded runs</h3>
          <div class="list">${replays.length ? replays.map((r) => `
            <a class="list-item" href="#" data-replay="${esc(r.name)}">
              <div class="li-top"><span>${esc(r.brand)}</span><span class="mono">${esc(day(r.recorded_at))}</span></div>
              <div class="li-claim">${esc(r.claim)}</div></a>`).join("") : `<div class="empty">Save a finished live run as a replay to have a backup for the demo.</div>`}</div>
        </div>
      </aside>
    </div>`;

  view.querySelectorAll("[data-preset]").forEach((b) => b.addEventListener("click", () => fillPreset(presets.find((p) => p.key === b.dataset.preset))));
  view.querySelectorAll("[data-replay]").forEach((a) => a.addEventListener("click", async (e) => {
    e.preventDefault();
    const { id } = await api(`/api/replays/${encodeURIComponent(a.dataset.replay)}`, { method: "POST" });
    location.hash = `#/case/${id}`;
  }));
  document.getElementById("case-form").addEventListener("submit", submitCase);
}

function caseRow(c) {
  const g = c.grade && c.grade.level !== undefined ? `Grade ${c.grade.level} · ${c.grade.label}` : c.status;
  return `<a class="list-item" href="#/case/${esc(c.id)}">
    <div class="li-top"><span>${esc(c.brand)}${c.replay ? " · recorded" : ""}</span><span class="mono">${esc(g)}</span></div>
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
  const idx = STAGES.findIndex(([k]) => k === current.stage);
  const done = current.status === "done";
  el.innerHTML = STAGES.map(([k, label], i) => {
    const cls = done || i < idx ? "done" : i === idx && current.status === "running" ? "active" : "";
    return `<div class="step ${cls}"><span class="n">0${i + 1}</span>${label}</div>`;
  }).join("");
}

function drawTabBody() {
  const el = document.getElementById("tab-body");
  if (!el) return;
  const fn = { spread: tabSpread, ai: tabAI, actions: tabActions, evidence: tabEvidence, log: tabLog }[state.tab];
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
  const order = ["site_fix", "fact_sheet", "correction_request", "platform_flag"];
  const sorted = [...c.actions].sort((a, b) => order.indexOf(a.kind) - order.indexOf(b.kind));
  return `
    <div class="actions-bar">
      <div class="gate-note">Every action is a draft. Approve, edit or reject each one. Only approved actions are exported, and nothing is sent from this tool.</div>
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
    } catch (err) { alertInline(card, err.message); b.disabled = false; }
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
