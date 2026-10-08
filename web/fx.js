// Contagion FX: fluid WebGL background + Three.js "infection network" loader.
// Pure progressive enhancement. If WebGL or Three.js is unavailable the UI falls back to CSS.

const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
const pointer = { x: 0.5, y: 0.5, tx: 0.5, ty: 0.5 };
addEventListener("pointermove", (e) => { pointer.tx = e.clientX / innerWidth; pointer.ty = 1 - e.clientY / innerHeight; }, { passive: true });

let intensityTarget = 0.15, intensity = 0.15;       // 0 calm .. 1 outbreak
const setIntensity = (v) => { intensityTarget = v; };

// ---------- fluid background ----------
function initFluid() {
  const c = document.getElementById("fx-bg");
  if (!c) return;
  const gl = c.getContext("webgl", { antialias: false, alpha: false, powerPreference: "low-power" });
  if (!gl) { c.remove(); document.body.classList.add("no-gl"); return; }
  const vs = "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}";
  const fs = `
  #ifdef GL_FRAGMENT_PRECISION_HIGH
  precision highp float;
  #else
  precision mediump float;
  #endif
  uniform vec2 r; uniform float t; uniform vec2 m; uniform float k;
  float h(vec2 p){return fract(sin(dot(p,vec2(127.1,311.7)))*43758.5453);}
  float n(vec2 p){vec2 i=floor(p),f=fract(p);f=f*f*(3.-2.*f);
    return mix(mix(h(i),h(i+vec2(1.,0.)),f.x),mix(h(i+vec2(0.,1.)),h(i+vec2(1.,1.)),f.x),f.y);}
  float fbm(vec2 p){float a=.5,s=0.;for(int i=0;i<4;i++){s+=a*n(p);p=p*2.03+vec2(3.1,1.7);a*=.5;}return s;}
  void main(){
    vec2 uv=gl_FragCoord.xy/r; vec2 p=(gl_FragCoord.xy-.5*r)/r.y;
    vec2 mm=(m-.5)*vec2(r.x/r.y,1.);
    float sp=.045+.10*k;
    vec2 q=vec2(fbm(p*1.5+t*sp),fbm(p*1.5+vec2(5.2,1.3)-t*sp));
    vec2 w=vec2(fbm(p*1.3+2.*q+vec2(1.7,9.2)+t*sp*1.3),fbm(p*1.3+2.*q+vec2(8.3,2.8)-t*sp));
    float f=fbm(p*1.1+2.4*w);
    float d=length(p-mm);
    f+=.20*exp(-d*3.2);
    vec3 col=vec3(.024,.028,.040);
    col+=vec3(.0,.50,.58)*smoothstep(.32,.9,f)*.20*(1.-k*.55);
    col+=vec3(.86,.13,.09)*smoothstep(.42,1.,w.x*f*1.9)*(.13+.34*k);
    col+=vec3(1.,.35,.2)*pow(smoothstep(.6,1.,f),3.)*.10*k;
    col*=1.-1.1*dot(uv-.5,uv-.5);
    col+=(h(gl_FragCoord.xy+fract(t))-.5)*.014;
    gl_FragColor=vec4(col,1.);
  }`;
  const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); return s; };
  const prog = gl.createProgram();
  gl.attachShader(prog, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(prog, sh(gl.FRAGMENT_SHADER, fs));
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) { c.remove(); document.body.classList.add("no-gl"); return; }
  gl.useProgram(prog);
  const buf = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW);
  const loc = gl.getAttribLocation(prog, "p"); gl.enableVertexAttribArray(loc); gl.vertexAttribPointer(loc, 2, gl.FLOAT, false, 0, 0);
  const U = Object.fromEntries(["r", "t", "m", "k"].map((n) => [n, gl.getUniformLocation(prog, n)]));
  const scale = 0.5;   // render at half resolution; the blur is part of the look
  const resize = () => { c.width = Math.max(2, Math.floor(innerWidth * scale)); c.height = Math.max(2, Math.floor(innerHeight * scale)); gl.viewport(0, 0, c.width, c.height); };
  resize(); addEventListener("resize", () => { resize(); if (reduce) frame(0); });
  let t0 = performance.now();
  const frame = (now) => {
    intensity += (intensityTarget - intensity) * 0.03;
    pointer.x += (pointer.tx - pointer.x) * 0.06; pointer.y += (pointer.ty - pointer.y) * 0.06;
    gl.uniform2f(U.r, c.width, c.height);
    gl.uniform1f(U.t, reduce ? 12 : (now - t0) / 1000 + 8);
    gl.uniform2f(U.m, pointer.x, pointer.y);
    gl.uniform1f(U.k, intensity);
    gl.drawArrays(gl.TRIANGLES, 0, 3);
  };
  if (reduce) { frame(0); return; }
  const loop = (now) => { if (!document.hidden) frame(now); requestAnimationFrame(loop); };
  requestAnimationFrame(loop);
}

// ---------- Three.js loader ----------
let threePromise = null;
function loadThree() {
  if (window.THREE) return Promise.resolve(window.THREE);
  if (!threePromise) threePromise = new Promise((res, rej) => {
    const s = document.createElement("script");
    s.src = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js";
    s.onload = () => (window.THREE ? res(window.THREE) : rej(new Error("three missing")));
    s.onerror = () => rej(new Error("three failed to load"));
    document.head.appendChild(s);
  });
  return threePromise;
}

// Builds a self-contained loader scene in `host`. Returns a controller.
async function buildLoader(host) {
  let THREE;
  try { THREE = await loadThree(); } catch { host.classList.add("fx-fallback"); return null; }
  let renderer;
  try { renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true, powerPreference: "low-power" }); }
  catch { host.classList.add("fx-fallback"); return null; }
  renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  host.appendChild(renderer.domElement);
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
  camera.position.set(0, 0, 8.2);
  const group = new THREE.Group(); scene.add(group);

  const U = { uTime: { value: 0 }, uWave: { value: 0 }, uK: { value: 0.15 }, uStage: { value: 0 } };

  // core: displaced icosphere with fresnel rim, teal when calm, red when infected
  const core = new THREE.Mesh(
    new THREE.IcosahedronGeometry(1, 4),
    new THREE.ShaderMaterial({
      uniforms: U, transparent: true,
      vertexShader: `
        uniform float uTime; uniform float uK; varying vec3 vN; varying vec3 vV; varying float vD;
        void main(){
          vec3 p=position; float d=sin(p.x*3.1+uTime*1.3)*sin(p.y*2.7-uTime*1.1)*sin(p.z*3.3+uTime*.9);
          p+=normal*d*(.10+.22*uK); vD=d;
          vec4 mv=modelViewMatrix*vec4(p,1.); vN=normalize(normalMatrix*normal); vV=normalize(-mv.xyz);
          gl_Position=projectionMatrix*mv;
        }`,
      fragmentShader: `
        uniform float uK; varying vec3 vN; varying vec3 vV; varying float vD;
        void main(){
          float f=pow(1.-max(dot(normalize(vN),normalize(vV)),0.),2.2);
          vec3 teal=vec3(.1,.85,.95), red=vec3(1.,.22,.14);
          vec3 c=mix(teal,red,clamp(uK*1.15+vD*.5,0.,1.));
          gl_FragColor=vec4(c*(.25+f*1.6),.35+f*.65);
        }`,
    })
  );
  group.add(core);

  // wire shell
  const shell = new THREE.LineSegments(
    new THREE.WireframeGeometry(new THREE.IcosahedronGeometry(1.45, 2)),
    new THREE.LineBasicMaterial({ color: 0x5ef2ff, transparent: true, opacity: 0.16, blending: THREE.AdditiveBlending, depthWrite: false })
  );
  group.add(shell);

  // network nodes on a fibonacci shell
  const N = 150, pos = [], dist = [], seed = [];
  const R0 = 2.1, R1 = 3.5;
  for (let i = 0; i < N; i++) {
    const y = 1 - (i / (N - 1)) * 2, rad = Math.sqrt(1 - y * y), th = i * 2.399963;
    const r = R0 + Math.random() * (R1 - R0);
    pos.push(Math.cos(th) * rad * r, y * r * 0.82, Math.sin(th) * rad * r);
    dist.push((r - R0) / (R1 - R0)); seed.push(Math.random());
  }
  const pg = new THREE.BufferGeometry();
  pg.setAttribute("position", new THREE.Float32BufferAttribute(pos, 3));
  pg.setAttribute("aDist", new THREE.Float32BufferAttribute(dist, 1));
  pg.setAttribute("aSeed", new THREE.Float32BufferAttribute(seed, 1));
  const waveGLSL = `
    uniform float uTime; uniform float uWave; uniform float uK;
    float ring(float d){ float x=(uWave-d)*6.; return exp(-x*x); }
    float after(float d){ return smoothstep(d-.02,d+.18,uWave)*(.35+.65*uK); }`;
  const nodes = new THREE.Points(pg, new THREE.ShaderMaterial({
    uniforms: U, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: waveGLSL + `
      attribute float aDist; attribute float aSeed; varying float vR; varying float vA;
      void main(){
        vR=ring(aDist); vA=after(aDist);
        vec3 p=position; p+=normalize(p)*sin(uTime*.8+aSeed*30.)*.08;
        vec4 mv=modelViewMatrix*vec4(p,1.);
        gl_PointSize=(3.2+aSeed*3.+vR*9.+vA*2.)*(8./-mv.z)*1.6;
        gl_Position=projectionMatrix*mv;
      }`,
    fragmentShader: `
      varying float vR; varying float vA;
      void main(){
        vec2 c=gl_PointCoord-.5; float d=length(c); if(d>.5) discard;
        float a=smoothstep(.5,0.,d);
        vec3 col=mix(vec3(.2,.85,.95),vec3(1.,.25,.15),clamp(vA+vR,0.,1.));
        gl_FragColor=vec4(col*(.7+vR*2.),a*(.55+vR*.6));
      }`,
  }));
  group.add(nodes);

  // edges: each node to its two nearest neighbours and to a point near the core
  const ep = [], ed = [];
  const P = (i) => [pos[i * 3], pos[i * 3 + 1], pos[i * 3 + 2]];
  for (let i = 0; i < N; i++) {
    const a = P(i);
    const near = [];
    for (let j = 0; j < N; j++) if (j !== i) { const b = P(j); near.push([(a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2, j]); }
    near.sort((x, y) => x[0] - y[0]);
    for (const [, j] of near.slice(0, 2)) { const b = P(j); ep.push(...a, ...b); ed.push(dist[i], dist[j]); }
    if (i % 3 === 0) { const l = Math.hypot(...a); ep.push(...a, a[0] / l * 1.5, a[1] / l * 1.5, a[2] / l * 1.5); ed.push(dist[i], 0); }
  }
  const eg = new THREE.BufferGeometry();
  eg.setAttribute("position", new THREE.Float32BufferAttribute(ep, 3));
  eg.setAttribute("aDist", new THREE.Float32BufferAttribute(ed, 1));
  const edges = new THREE.LineSegments(eg, new THREE.ShaderMaterial({
    uniforms: U, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: waveGLSL + `
      attribute float aDist; varying float vR; varying float vA;
      void main(){ vR=ring(aDist); vA=after(aDist); gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.); }`,
    fragmentShader: `
      varying float vR; varying float vA;
      void main(){ vec3 col=mix(vec3(.1,.55,.65),vec3(1.,.25,.15),clamp(vA+vR,0.,1.)); gl_FragColor=vec4(col,.10+vR*.65+vA*.12); }`,
  }));
  group.add(edges);

  // stage rings: pending dim, active red pulse, done teal
  const rings = [];
  for (let i = 0; i < 4; i++) {
    const m = new THREE.Mesh(new THREE.TorusGeometry(1.75 + i * 0.22, 0.012, 8, 160),
      new THREE.MeshBasicMaterial({ color: 0x5ef2ff, transparent: true, opacity: 0.1, blending: THREE.AdditiveBlending, depthWrite: false }));
    m.rotation.set(1.1 + i * 0.45, i * 0.8, i * 0.5);
    group.add(m); rings.push(m);
  }

  let stage = { index: -1, count: 4, done: false };
  const ctrl = {
    setStage(index, count, done) { stage = { index, count: count || 4, done: !!done }; },
    resize() {
      const w = host.clientWidth || 1, h = host.clientHeight || 1;
      renderer.setSize(w, h, false); renderer.domElement.style.width = "100%"; renderer.domElement.style.height = "100%";
      camera.aspect = w / h; camera.updateProjectionMatrix();
    },
    active: false,
    destroy() { dead = true; ro.disconnect(); renderer.dispose(); renderer.domElement.remove(); },
  };
  const ro = new ResizeObserver(() => ctrl.resize()); ro.observe(host); ctrl.resize();

  const col = { teal: new THREE.Color(0x5ef2ff), red: new THREE.Color(0xff3b24) };
  let dead = false, last = performance.now(), time = 0, wave = 0, shownOnce = false;
  const render = (dt) => {
    time += dt;
    const k = Math.min(1, Math.max(0, ctrl.active ? 0.35 + intensity * 0.65 : intensity * 0.5));
    wave += dt * (0.22 + k * 0.5); if (wave > 1.5) wave = -0.25;
    U.uTime.value = time; U.uWave.value = wave; U.uK.value = k;
    group.rotation.y += dt * (0.12 + k * 0.25);
    group.rotation.x = Math.sin(time * 0.25) * 0.12 + (pointer.y - 0.5) * 0.35;
    group.position.x += ((pointer.x - 0.5) * 0.5 - group.position.x) * 0.05;
    const s = 1 + Math.sin(time * (1.6 + k * 2.4)) * 0.03 * (0.5 + k);
    core.scale.setScalar(s);
    shell.rotation.y -= dt * 0.2;
    rings.forEach((m, i) => {
      m.rotation.z += dt * (0.1 + i * 0.05) * (i % 2 ? -1 : 1);
      const isDone = stage.done || i < stage.index, isActive = !stage.done && i === stage.index;
      const target = isDone ? 0.55 : isActive ? 0.55 + Math.sin(time * 5) * 0.3 : 0.08;
      m.material.opacity += (target - m.material.opacity) * 0.12;
      m.material.color.lerp(isActive ? col.red : col.teal, 0.12);
    });
    renderer.render(scene, camera);
  };
  const tick = (now) => {
    if (dead) return;
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!document.hidden && host.offsetParent !== null) {
      if (reduce) { if (!shownOnce || ctrl.dirty) { render(0.016); shownOnce = true; ctrl.dirty = false; } }
      else render(dt);
    }
    requestAnimationFrame(tick);
  };
  if (reduce) { time = 2; wave = 0.6; }
  requestAnimationFrame(tick);
  return ctrl;
}

// ---------- dock (persistent run indicator) ----------
const dockEl = document.getElementById("fx-dock");
let dockCtl = null, dockStarted = false;
const run = {
  sync({ active, stages = [], index = -1, done = false, line = "", kicker = "TRACE RUNNING", stageLabel } = {}) {
    if (!dockEl) return;
    const show = !!active;
    dockEl.classList.toggle("on", show);
    dockEl.hidden = false;
    setIntensity(show ? 0.85 : 0.15);
    if (!show) return;
    if (!dockStarted) {
      dockStarted = true;
      buildLoader(dockEl.querySelector(".fx-orb")).then((c) => { dockCtl = c; if (c) { c.active = true; c.setStage(index, stages.length || 4, done); } });
    }
    if (dockCtl) { dockCtl.active = true; dockCtl.setStage(index, stages.length || 4, done); dockCtl.dirty = true; }
    dockEl.querySelector(".fx-kicker").textContent = kicker;
    dockEl.querySelector(".fx-stage").textContent = stageLabel || stages[index] || "Working";
    dockEl.querySelector(".fx-line").textContent = line || "";
    const pct = stages.length ? Math.max(4, ((index + 0.5) / stages.length) * 100) : 20;
    dockEl.querySelector(".fx-bar i").style.width = `${Math.min(100, pct)}%`;
  },
  hide() { run.sync({ active: false }); },
};

// ---------- busy overlay (route loads, form submits) ----------
const overlay = document.getElementById("fx-overlay");
let busyCount = 0, showTimer = null, shownAt = 0, ovCtl = null, ovStarted = false;
const busy = {
  begin(label = "Loading") {
    busyCount++;
    if (busyCount > 1 || !overlay) return;
    clearTimeout(showTimer);
    showTimer = setTimeout(() => {
      shownAt = performance.now();
      overlay.querySelector(".fx-ov-label").textContent = label;
      overlay.classList.add("on"); overlay.setAttribute("aria-hidden", "false");
      setIntensity(0.7);
      if (!ovStarted) { ovStarted = true; buildLoader(overlay.querySelector(".fx-ov-orb")).then((c) => { ovCtl = c; if (c) { c.active = true; c.setStage(-1, 4, false); } }); }
      if (ovCtl) ovCtl.dirty = true;
    }, 260);
  },
  end() {
    busyCount = Math.max(0, busyCount - 1);
    if (busyCount || !overlay) return;
    clearTimeout(showTimer);
    const wait = overlay.classList.contains("on") ? Math.max(0, 520 - (performance.now() - shownAt)) : 0;
    setTimeout(() => {
      if (busyCount) return;
      overlay.classList.remove("on"); overlay.setAttribute("aria-hidden", "true");
      setIntensity(dockEl?.classList.contains("on") ? 0.85 : 0.15);
    }, wait);
  },
};

// ---------- page enter animation + glass spotlight ----------
function enter(el) {
  if (reduce || !el) return;
  el.classList.remove("enter"); void el.offsetWidth; el.classList.add("enter");
  clearTimeout(enter.t); enter.t = setTimeout(() => el.classList.remove("enter"), 1400);
}
addEventListener("pointermove", (e) => {
  const t = e.target.closest?.(".card, .kpi, .narr, .grade, .action, .list-item, .panel > .log, .detail");
  if (!t) return;
  const r = t.getBoundingClientRect();
  t.style.setProperty("--mx", `${e.clientX - r.left}px`); t.style.setProperty("--my", `${e.clientY - r.top}px`);
}, { passive: true });

initFluid();
window.FX = { busy, run, enter, setIntensity };
