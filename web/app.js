// Tutor socrático en el navegador: el motor (Python) corre con Pyodide en el
// dispositivo del alumno, así que no hace falta ningún servidor.
"use strict";

const CFG = Object.assign(
  { pyodide: "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/", registroURL: "", clave: "", grupos: [] },
  window.TUTOR_CONFIG || {}
);
const MODE_HELP = {
  aprender: "Más preguntas y pistas: el tutor te guía paso a paso.",
  practicar: "Tú resuelves; el tutor interviene cuando detecta un error.",
  verificar: "Escribe tu procedimiento y resultados; el tutor los revisa sin resolver.",
  simulador: "Como Aprender, y al final compruebas en PhET o Multisim.",
};
const QUEUE_KEY = "tutor-registros-pendientes";

const $ = (sel) => document.querySelector(sel);
const state = { alumno: null, grupo: "", mode: "aprender", problems: [], sid: null, turns: 0, finished: false };
let bridge = null;
let engineReady = null;

// ------------------------------------------------------------------ utilidades
function setStatus(text, busy = false) {
  const el = $("#status");
  el.textContent = text;
  el.classList.toggle("busy", busy);
}

function show(id) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === id));
  window.scrollTo(0, 0);
}

function loadScript(src) {
  return new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = resolve;
    s.onerror = () => reject(new Error("No se pudo descargar " + src));
    document.head.appendChild(s);
  });
}

const nextFrame = () => new Promise((r) => setTimeout(r, 30));

async function sha256(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

// ------------------------------------------------------------------ motor
async function loadEngine() {
  setStatus("Preparando el tutor… la primera vez tarda un poco.", true);
  await loadScript(CFG.pyodide + "pyodide.js");
  const py = await window.loadPyodide({ indexURL: CFG.pyodide });
  await py.loadPackage(["sympy", "numpy"]);
  const bundle = await (await fetch("bundle.json", { cache: "no-cache" })).json();
  const home = "/home/pyodide";
  for (const [path, content] of Object.entries(bundle.files)) {
    const parts = path.split("/");
    if (parts.length > 1) py.FS.mkdirTree(home + "/" + parts.slice(0, -1).join("/"));
    py.FS.writeFile(home + "/" + path, content);
  }
  py.runPython(`import os, sys\nos.chdir("${home}")\nsys.path.insert(0, "${home}")`);
  bridge = py.pyimport("bridge");
  const data = JSON.parse(bridge.problems());
  state.problems = data.problems;
  renderProblems();
  setStatus("Tutor listo");
  setTimeout(() => setStatus(""), 2500);
}

// ------------------------------------------------------------------ registro
function queue() {
  try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); } catch { return []; }
}
function saveQueue(items) {
  try { localStorage.setItem(QUEUE_KEY, JSON.stringify(items.slice(-30))); } catch { /* sin espacio */ }
}

function currentRecord() {
  if (!bridge || !state.sid) return null;
  const rec = JSON.parse(bridge.record());
  return Object.assign(rec, { alumno_h: state.alumno, grupo: state.grupo, clave: CFG.clave });
}

async function postRecord(rec) {
  if (!CFG.registroURL) return false;
  try {
    const r = await fetch(CFG.registroURL, {
      method: "POST", headers: { "Content-Type": "text/plain;charset=utf-8" }, body: JSON.stringify(rec),
    });
    return r.ok;
  } catch {
    return false;
  }
}

async function sendRecord() {
  const rec = currentRecord();
  if (!rec) return;
  const pending = queue().filter((r) => r.sesion !== rec.sesion);
  pending.push(rec);
  const left = [];
  for (const r of pending) if (!(await postRecord(r))) left.push(r);
  saveQueue(left);
}

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "hidden" || !CFG.registroURL) return;
  const rec = currentRecord();
  if (rec) navigator.sendBeacon(CFG.registroURL, new Blob([JSON.stringify(rec)], { type: "text/plain" }));
});

// ------------------------------------------------------------------ pantalla 1
function initForm() {
  const sel = $("#grupo"), txt = $("#grupo-texto");
  if (CFG.grupos.length) {
    sel.innerHTML = CFG.grupos.map((g) => `<option>${g}</option>`).join("");
    txt.remove();
  } else {
    sel.remove();
  }
  $("#form-inicio").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const boleta = $("#boleta").value.replace(/\s+/g, "").toUpperCase();
    if (!boleta || !$("#consent").checked) return;
    // la boleta nunca sale del dispositivo: sólo su huella, que el registro vuelve a cifrar con una clave secreta
    state.alumno = await sha256("tutor-circuitos:" + boleta);
    state.grupo = (CFG.grupos.length ? $("#grupo").value : $("#grupo-texto").value).trim();
    $("#boleta").value = "";
    show("elegir");
  });
}

// ------------------------------------------------------------------ pantalla 2
function renderModes() {
  const box = $("#modes");
  box.innerHTML = "";
  for (const [key, label] of Object.entries({ aprender: "Aprender", practicar: "Practicar", verificar: "Verificar", simulador: "Simulador" })) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(key === state.mode));
    b.addEventListener("click", () => { state.mode = key; renderModes(); });
    box.appendChild(b);
  }
  $("#mode-help").textContent = MODE_HELP[state.mode];
}

function renderProblems() {
  const box = $("#lista");
  box.innerHTML = "";
  let topic = null, grid = null;
  for (const p of state.problems) {
    if (p.topicName !== topic) {
      topic = p.topicName;
      const h = document.createElement("h3");
      h.className = "topic";
      h.textContent = topic;
      grid = document.createElement("div");
      grid.className = "plist";
      box.append(h, grid);
    }
    const b = document.createElement("button");
    b.type = "button";
    b.className = "card pcard";
    b.innerHTML = `<strong></strong><span class="small muted"></span>${p.level ? '<span class="tag"></span>' : ""}`;
    b.querySelector("strong").textContent = p.title.replace(/^[^·]+·\s*/, "");
    b.querySelector(".muted").textContent = p.source;
    if (p.level) b.querySelector(".tag").textContent = p.level;
    b.addEventListener("click", () => startProblem(p.id));
    grid.appendChild(b);
  }
}

// ------------------------------------------------------------------ pantalla 3
function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  $("#chat").appendChild(div);
  div.scrollIntoView({ block: "end", behavior: "smooth" });
  return div;
}

function renderProblemCard(p) {
  const card = $("#problema");
  card.innerHTML = `
    <header><h2></h2><button type="button" class="link" id="cambiar">Cambiar problema</button></header>
    <p class="statement"></p>
    <details><summary>Elementos del circuito</summary><ul class="elements"></ul></details>`;
  card.querySelector("h2").textContent = p.title;
  card.querySelector(".statement").textContent = p.statement;
  const ul = card.querySelector(".elements");
  for (const line of [...p.elements, ...p.meshes]) {
    const li = document.createElement("li");
    li.textContent = line.replace(/^-\s*/, "");
    ul.appendChild(li);
  }
  $("#cambiar").addEventListener("click", async () => { await sendRecord(); show("elegir"); });
}

async function startProblem(pid, custom = "", method = "") {
  if (!bridge) { setStatus("El tutor todavía se está preparando…", true); await engineReady; }
  if (state.sid && !state.finished) await sendRecord();
  const res = JSON.parse(bridge.start(pid, state.mode, custom, method));
  if (res.error) { alert(res.error); return; }
  state.sid = res.sid;
  state.turns = 0;
  state.finished = false;
  $("#chat").innerHTML = "";
  renderProblemCard(res.problem);
  show("sesion");
  addMessage("tutor", res.message);
  setComposer(true);
  $("#entrada").focus();
}

function setComposer(enabled) {
  $("#entrada").disabled = !enabled;
  $("#enviar").disabled = !enabled;
  $("#pista").disabled = !enabled;
}

function showIndicators(ind) {
  const wrap = addMessage("tutor", "Registro de la interacción");
  const table = document.createElement("table");
  table.className = "indicadores";
  for (const [k, v] of Object.entries(ind)) {
    const tr = table.insertRow();
    tr.insertCell().textContent = k;
    tr.insertCell().textContent = v;
  }
  wrap.appendChild(table);
  const again = document.createElement("button");
  again.type = "button";
  again.className = "primary";
  again.textContent = "Resolver otro problema";
  again.style.marginTop = "10px";
  again.addEventListener("click", () => show("elegir"));
  wrap.appendChild(again);
}

async function send(text) {
  text = text.trim();
  if (!text || state.finished) return;
  addMessage("alumno", text);
  $("#entrada").value = "";
  autoGrow();
  setComposer(false);
  const typing = document.createElement("div");
  typing.className = "typing";
  typing.textContent = "El tutor está revisando…";
  $("#chat").appendChild(typing);
  await nextFrame();
  let res;
  try {
    res = JSON.parse(bridge.reply(text));
  } catch (err) {
    typing.remove();
    addMessage("error", "Algo falló al revisar tu respuesta. Intenta escribirla de otra forma.");
    console.error(err);
    setComposer(true);
    return;
  }
  typing.remove();
  addMessage("tutor", res.message);
  state.turns += 1;
  if (res.finished) {
    state.finished = true;
    showIndicators(res.indicators);
    await sendRecord();
    return;
  }
  setComposer(true);
  $("#entrada").focus();
  if (state.turns % 5 === 0) sendRecord();
}

function autoGrow() {
  const t = $("#entrada");
  t.style.height = "auto";
  const max = window.innerHeight * 0.4;
  t.style.height = Math.min(t.scrollHeight, max) + "px";
  t.style.overflowY = t.scrollHeight > max ? "auto" : "hidden";
}

function initSession() {
  $("#composer").addEventListener("submit", (ev) => { ev.preventDefault(); send($("#entrada").value); });
  $("#entrada").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) { ev.preventDefault(); send($("#entrada").value); }
  });
  $("#entrada").addEventListener("input", autoGrow);
  $("#pista").addEventListener("click", () => send("Dame una pista"));
  $("#propio-empezar").addEventListener("click", () => {
    const text = $("#propio").value.trim();
    if (text) startProblem("", text, $("#propio-metodo").value);
  });
}

// ------------------------------------------------------------------ arranque
initForm();
renderModes();
initSession();
engineReady = loadEngine().catch((err) => {
  console.error(err);
  setStatus("No se pudo cargar el tutor. Revisa tu conexión y recarga la página.");
});
// reintenta registros que no se pudieron enviar en sesiones anteriores
engineReady.then(async () => {
  const left = [];
  for (const r of queue()) if (!(await postRecord(r))) left.push(r);
  saveQueue(left);
});
