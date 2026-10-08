// Run Theater: while a run is going, show the machine working. Left: what the current stage does,
// in plain words, plus the real log lines as they arrive. Right: the five system layers opened up,
// with the parts doing work right now lit. Progressive enhancement: with no WebGL the left side
// still works on its own. Everything shown comes from the same events as the Log tab.

const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

// layers and parts mirror the architecture in the README
const LAYERS = ["Interface", "Orchestration", "Intelligence and data", "Deterministic core", "Control and persistence"];
const PARTS = [
  ["Web console", "FastAPI service", "Alert channels"],
  ["Watch engine", "Deep Trace", "Counter"],
  ["Source connectors", "LLM judgment", "AI engine probes"],
  ["Spread analysis", "Threat scoring", "Guardrails"],
  ["Human approval", "Case store", "HTTP client"],
];

// stage key -> parts doing work, as [layer, part]
const ACTIVE = {
  defend: {
    discovery: [[2, 0], [4, 2]],
    analysis: [[2, 1], [3, 0]],
    cross_reference: [[2, 2], [2, 1]],
    synthesis: [[3, 1], [3, 2], [4, 0]],
  },
  counter: {
    inventory: [[4, 1], [4, 2]],
    listening: [[2, 0], [2, 1]],
    reality: [[3, 2], [3, 0]],
    drafting: [[3, 2], [4, 0]],
  },
  watch: {
    polling: [[2, 0], [4, 2]],
    triage: [[2, 1], [1, 0]],
    playbook: [[3, 2], [4, 0]],
    ai_check: [[2, 2], [2, 1]],
  },
};
const IDLE = [[0, 1], [4, 1]];

const WHAT = {
  defend: {
    queued: "The run is queued. It starts as soon as a worker is free.",
    discovery: "Searching news, social platforms and forums at the same time, then collapsing duplicate links so one story counts once.",
    analysis: "Labeling every post as spreading, correcting, neutral or unrelated. A second reviewer re-checks each accusation.",
    cross_reference: "Asking AI assistants the same neutral questions and tracing which pages they cite when they repeat the claim.",
    synthesis: "Grading the spread with fixed rules, then drafting the fixes. Every draft waits for a person to approve it.",
  },
  counter: {
    queued: "The run is queued. It starts as soon as a worker is free.",
    inventory: "Reading your stock and working out margin, days of supply and surplus. This step is plain arithmetic.",
    listening: "Reading what customers say about the competitor and sorting each post into a friction type.",
    reality: "Keeping a complaint only when enough people report it first-hand on enough platforms. Rumor-led complaints are dropped.",
    drafting: "Matching verified complaints to surplus products and drafting paused ad packages for review.",
  },
  watch: {
    starting: "Starting the first sweep.",
    polling: "Pulling the last seven days of public mentions from every connected source.",
    triage: "Reading each new mention and filing it under a narrative with a threat score.",
    playbook: "Drafting a response plan for narratives that score high enough. Nothing is sent.",
    ai_check: "Checking whether AI assistants are repeating the narrative.",
  },
};

const MODE_LABEL = { defend: "Deep Trace", counter: "Counter", watch: "Watch, first sweep" };

const $ = (s, r = document) => r.querySelector(s);
const mk = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };

let el, term, titleEl, whatEl, stepsEl, kickEl, clockEl, actionsEl, legendEl, canvas;
let scene3 = null, sceneTried = false;
const runs = {};
let curId = null, curRun = null, curMode = "defend", curStageKey = "queued", curDone = false, curFailed = false;
let queue = [], typing = false, clockTimer = null;

function build() {
  if (el) return;
  el = mk("div"); el.id = "theater"; el.hidden = true;
  el.setAttribute("role", "dialog"); el.setAttribute("aria-label", "Run in progress");
  canvas = mk("canvas", "t-canvas"); canvas.setAttribute("aria-hidden", "true");
  const veil = mk("div", "t-veil");
  const top = mk("div", "t-top");
  top.append(mk("div", "t-mark", "CONTAGION"));
  const right = mk("div", "t-right");
  clockEl = mk("span", "t-clock", "00:00");
  const min = mk("button", "t-soft", "Minimize"); min.type = "button"; min.id = "t-min";
  min.addEventListener("click", () => dismiss());
  right.append(clockEl, min); top.append(right);
  legendEl = mk("div", "t-legend");
  LAYERS.forEach((_, l) => PARTS[l].forEach((p) => { const s = mk("span", "", p); s.dataset.k = p; legendEl.append(s); }));
  const copy = mk("section", "t-copy");
  kickEl = mk("div", "t-kick");
  titleEl = mk("h2", "t-title");
  whatEl = mk("p", "t-what");
  stepsEl = mk("ol", "t-steps");
  term = mk("pre", "t-term"); term.setAttribute("aria-live", "off");
  actionsEl = mk("div", "t-actions");
  copy.append(kickEl, titleEl, whatEl, stepsEl, term, actionsEl);
  const note = mk("div", "t-note", "Live log from this run. The run continues if you minimize.");
  el.append(canvas, veil, top, legendEl, copy, note);
  document.body.append(el);
  addEventListener("keydown", (e) => { if (e.key === "Escape" && el.classList.contains("on")) dismiss(); });
}

function open(run) {
  build();
  run.opened = true;
  el.hidden = false;
  document.body.classList.add("theater-open");
  requestAnimationFrame(() => { el.classList.add("on"); $("#t-min")?.focus({ preventScroll: true }); });
  startClock(run);
  if (!sceneTried) { sceneTried = true; initScene(); }
}
function close() {
  if (!el) return;
  el.classList.remove("on");
  document.body.classList.remove("theater-open");
  stopClock();
  setTimeout(() => { if (!el.classList.contains("on")) el.hidden = true; }, 480);
}
function dismiss() { if (curRun) curRun.dismissed = true; close(); }

function startClock(run) {
  stopClock();
  const tick = () => {
    const s = Math.floor((Date.now() - run.start) / 1000);
    clockEl.textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };
  tick(); clockTimer = setInterval(tick, 1000);
}
function stopClock() { clearInterval(clockTimer); clockTimer = null; }

// ---------- terminal ----------
function addLine(l, instant) {
  const row = mk("span", `ln ${l.level === "warn" || l.level === "error" ? l.level : ""}`);
  row.append(mk("span", "s", (l.stage || "").replace("_", "-")));
  const msg = mk("span", "m");
  row.append(msg);
  term.querySelectorAll(".last").forEach((n) => n.classList.remove("last"));
  row.classList.add("last");
  term.append(row);
  while (term.children.length > 16) term.firstChild.remove();
  const text = String(l.message || "");
  if (instant || reduce) { msg.textContent = text; return Promise.resolve(); }
  return new Promise((res) => {
    let i = 0; const step = Math.max(2, Math.ceil(text.length / 28));
    const tick = () => { i += step; msg.textContent = text.slice(0, i); if (i < text.length) setTimeout(tick, 16); else res(); };
    tick();
  });
}
async function drain() {
  if (typing) return; typing = true;
  while (queue.length) {
    const l = queue.shift();
    await addLine(l, queue.length > 4);
    await new Promise((r) => setTimeout(r, queue.length > 2 ? 0 : 140));
  }
  typing = false;
}

// ---------- public sync ----------
function sync(o) {
  const { id, mode = "defend", stages = [], keys = [], index = -1, live = false, done = false, failed = false, log = [], doneLine = "", doneTitle = "" } = o;
  if (!id) return;
  const run = (runs[id] ||= { opened: false, dismissed: false, shown: 0, start: Date.now() });
  curId = id; curRun = run; curMode = mode; curDone = done; curFailed = failed;

  if (live && !run.opened && !run.dismissed) open(run);
  if (!run.opened) { run.shown = log.length; return; }
  build();

  const key = keys[index] || (live ? (mode === "watch" ? "starting" : "queued") : curStageKey);
  curStageKey = key;
  kickEl.textContent = `${MODE_LABEL[mode] || "Run"}${o.brand ? ` on ${o.brand}` : ""}`;

  if (done || failed) {
    titleEl.textContent = failed ? "The run stopped with an error." : (doneTitle || "Run complete.");
    whatEl.textContent = failed ? "Open the log tab to see what happened. Nothing was sent." : (doneLine || "Open the results to review. Nothing has been sent.");
    stopClock();
  } else {
    titleEl.textContent = index >= 0 ? stages[index] : (mode === "watch" ? "Starting" : "Queued");
    whatEl.textContent = (WHAT[mode] || {})[key] || "Working.";
  }

  stepsEl.replaceChildren(...stages.map((label, i) => {
    const li = mk("li", done || i < index ? "done" : (i === index && live) ? "active" : "");
    li.append(mk("i"), document.createTextNode(label));
    return li;
  }));

  // new log lines
  const fresh = log.slice(run.shown);
  if (run.shown === 0 && fresh.length > 14) { fresh.slice(-14).forEach((l) => addLine(l, true)); }
  else queue.push(...fresh);
  run.shown = log.length;
  drain();

  // actions
  actionsEl.replaceChildren();
  if (done || failed) {
    const b = mk("button", "t-soft primary", failed ? "See the log" : "See results"); b.type = "button";
    b.addEventListener("click", () => { run.dismissed = true; close(); if (failed) document.querySelector('[data-tab="log"]')?.click(); });
    actionsEl.append(b);
    setTimeout(() => b.focus({ preventScroll: true }), 60);
  }

  const act = done ? [] : (live ? ((ACTIVE[mode] || {})[key] || IDLE) : []);
  const names = new Set(act.map(([l, c]) => PARTS[l][c]));
  legendEl.querySelectorAll("span").forEach((s) => s.classList.toggle("on", names.has(s.dataset.k)));
  scene3?.set(act, done);
}

// ---------- 3D scene ----------
function loadThree() {
  if (window.THREE) return Promise.resolve(window.THREE);
  return new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
    s.onload = () => (window.THREE ? res(window.THREE) : rej(new Error("three missing")));
    s.onerror = () => rej(new Error("three failed to load"));
    document.head.appendChild(s);
  });
}

async function initScene() {
  let THREE;
  try { THREE = await loadThree(); } catch { el.classList.add("no-gl"); return; }
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true, powerPreference: "low-power" }); }
  catch { el.classList.add("no-gl"); return; }
  renderer.setClearColor(0x000000, 0);
  const DPR = Math.min(devicePixelRatio || 1, 2);
  renderer.setPixelRatio(DPR);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(36, 1, 0.1, 60);
  camera.position.set(0, 0, 11);
  const group = new THREE.Group(); scene.add(group);

  const SIG = new THREE.Color(0xff4a24), CAL = new THREE.Color(0x2fa58c), EDGE = new THREE.Color(0xb4b3ae);
  const rrect = `
    float rr(vec2 p, vec2 b, float r){ vec2 q=abs(p)-b+r; return length(max(q,0.))+min(max(q.x,q.y),0.)-r; }`;
  const plateMat = () => new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    uniforms: { uE: { value: EDGE }, uAmt: { value: 0.0 } },
    vertexShader: "varying vec2 vP; void main(){ vP=position.xy; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
    fragmentShader: `uniform vec3 uE; uniform float uAmt; varying vec2 vP; ${rrect}
      void main(){ float d=rr(vP, vec2(1.6,2.2), .42); if(d>0.) discard;
        float edge=smoothstep(-.05,0.,d); float rim=smoothstep(-.4,0.,d);
        gl_FragColor=vec4(uE, .05+.10*rim+.55*edge); }`,
  });
  const chipMat = () => new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: { uA: { value: 0 }, uD: { value: 0 }, uS: { value: SIG }, uC: { value: CAL }, uE: { value: EDGE } },
    vertexShader: "varying vec2 vP; void main(){ vP=position.xy; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }",
    fragmentShader: `uniform float uA,uD; uniform vec3 uS,uC,uE; varying vec2 vP; ${rrect}
      void main(){ float d=rr(vP, vec2(.72,.27), .16); if(d>0.) discard;
        vec3 col=mix(uE,uC,uD); col=mix(col,uS,uA); float g=max(uA,uD*.6);
        float edge=smoothstep(-.03,0.,d);
        gl_FragColor=vec4(col, .10+.5*g+.5*edge*(.35+.65*g)); }`,
  });

  const Z = (l) => (l - 2) * 0.85;
  const chips = [];
  for (let l = 0; l < 5; l++) {
    const plate = new THREE.Mesh(new THREE.PlaneGeometry(3.4, 4.6), plateMat());
    plate.position.z = Z(l); group.add(plate);
    for (let c = 0; c < 3; c++) {
      const m = new THREE.Mesh(new THREE.PlaneGeometry(1.5, 0.58), chipMat());
      m.position.set(0, 1.35 - 1.35 * c, Z(l) + 0.02); group.add(m);
      chips.push({ l, c, m, a: 0, d: 0, ta: 0, td: 0 });
    }
  }

  // particles: leave an active part and drift through the stack
  const NP = 220;
  const P = { pos: new Float32Array(NP * 3), seed: new Float32Array(NP), t: new Float32Array(NP), src: new Int16Array(NP) };
  const pg = new THREE.BufferGeometry();
  pg.setAttribute("position", new THREE.BufferAttribute(P.pos, 3));
  pg.setAttribute("aSeed", new THREE.BufferAttribute(P.seed, 1));
  const pts = new THREE.Points(pg, new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: { uPx: { value: DPR } },
    vertexShader: "attribute float aSeed; uniform float uPx; void main(){ vec4 mv=modelViewMatrix*vec4(position,1.); gl_PointSize=(2.2+aSeed*2.4)*uPx*(9./-mv.z); gl_Position=projectionMatrix*mv; }",
    fragmentShader: "void main(){ vec2 c=gl_PointCoord-.5; float d=length(c); if(d>.5) discard; gl_FragColor=vec4(1.,.36,.2,smoothstep(.5,0.,d)*.8); }",
  }));
  group.add(pts);
  for (let i = 0; i < NP; i++) { P.seed[i] = Math.random(); P.t[i] = Math.random(); P.src[i] = -1; }

  let active = [], allDone = false;
  const respawn = (i) => {
    if (!active.length) { P.src[i] = -1; return; }
    P.src[i] = active[(Math.random() * active.length) | 0]; P.t[i] = 0;
  };
  const api = {
    set(list, done) {
      allDone = !!done;
      const ids = new Set(list.map(([l, c]) => l * 3 + c));
      active = [...ids];
      chips.forEach((ch, i) => {
        if (ids.has(i)) { ch.ta = 1; return; }
        if (ch.a > 0.4 || ch.ta === 1) ch.td = 1;   // finished a stage: stays teal
        ch.ta = 0;
      });
      for (let i = 0; i < NP; i++) if (P.src[i] < 0 || !active.includes(P.src[i])) respawn(i);
    },
  };
  scene3 = api;

  // drag to turn
  let drag = null, yaw = 0, pitch = 0;
  canvas.addEventListener("pointerdown", (e) => { drag = { x: e.clientX, y: e.clientY, yaw, pitch }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener("pointermove", (e) => { if (!drag) return; yaw = drag.yaw + (e.clientX - drag.x) * 0.006; pitch = Math.max(-0.5, Math.min(0.5, drag.pitch + (e.clientY - drag.y) * 0.004)); });
  const up = () => { drag = null; }; canvas.addEventListener("pointerup", up); canvas.addEventListener("pointercancel", up);

  let wide = true;
  const resize = () => {
    const w = innerWidth, h = innerHeight;
    renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
    wide = w > 760;
    group.position.set(wide ? Math.min(2.3, 0.8 + w / 950) : 0, wide ? 0 : 1.6, 0);
    group.scale.setScalar(wide ? Math.min(1, h / 760 + 0.25) : 0.62);
  };
  resize(); addEventListener("resize", resize);

  let last = performance.now(), time = 0;
  const frame = (now) => {
    requestAnimationFrame(frame);
    if (document.hidden || !el.classList.contains("on")) { last = now; return; }
    const dt = Math.min(0.05, (now - last) / 1000); last = now; time += dt;
    group.rotation.y = -0.62 + yaw + (reduce ? 0 : Math.sin(time * 0.3) * 0.08);
    group.rotation.x = 0.12 + pitch;
    chips.forEach((ch) => {
      ch.a += (ch.ta - ch.a) * Math.min(1, dt * 6);
      ch.d += (ch.td - ch.d) * Math.min(1, dt * 4);
      const u = ch.m.material.uniforms;
      u.uA.value = ch.a * (0.75 + (reduce ? 0.25 : 0.25 * Math.sin(time * 5 + ch.l)));
      u.uD.value = ch.d * (1 - ch.a);
    });
    for (let i = 0; i < NP; i++) {
      const s = P.src[i];
      if (s < 0) { P.pos[i * 3 + 2] = 99; continue; }
      P.t[i] += dt * (0.28 + P.seed[i] * 0.3);
      if (P.t[i] > 1) { respawn(i); if (P.src[i] < 0) continue; }
      const ch = chips[P.src[i]], t = P.t[i];
      const sx = ch.m.position.x, sy = ch.m.position.y, sz = ch.m.position.z;
      const a = P.seed[i] * 6.283 + time * 0.6;
      P.pos[i * 3] = sx + (P.seed[i] - 0.5) * 1.4 + Math.cos(a) * 0.12 * t;
      P.pos[i * 3 + 1] = sy + (((i * 7) % 11) / 11 - 0.5) * 0.5 + t * 0.35;
      P.pos[i * 3 + 2] = sz + t * 1.7 - 0.4;
    }
    pg.attributes.position.needsUpdate = true;
    renderer.render(scene, camera);
  };
  requestAnimationFrame(frame);
  // apply whatever stage the run is in right now
  const act = curDone ? [] : ((ACTIVE[curMode] || {})[curStageKey] || IDLE);
  api.set(act, curDone);
}

window.Theater = { sync, dismiss };
