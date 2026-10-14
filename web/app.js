// Interfaz del Tutor Socrático de Circuitos (Aplicación de Escritorio).
"use strict";

const MODE_HELP = {
  aprender: "Más preguntas y pistas: el tutor te guía paso a paso.",
  practicar: "Tú resuelves; el tutor interviene cuando detecta un error.",
  verificar: "Escribe tu procedimiento y resultados; el tutor los revisa sin resolver.",
  simulador: "Como Aprender, y al final compruebas en PhET o Multisim.",
};

const ESTADOS_MODELO = {
  cargando: ["Preparando al tutor…", true],
  listo: ["Tutor listo", false],
  error: ["Tutor en modo básico", false],
  // el hilo que arranca el modelo todavía no empieza
  detenido: ["Preparando al tutor…", true],
  sin_modelo: ["Tutor listo (plantillas)", false],
};

const $ = (sel) => document.querySelector(sel);

const urlParams = new URLSearchParams(window.location.search);
let apiToken = urlParams.get("t") || "";
const tele = window.Telemetria;
tele.fijarToken(apiToken);

const state = {
  codigo: null,
  consentimiento: null,
  mode: "aprender",
  problems: [],
  currentProblem: null,
  finished: false,
  // esperando al servidor o a la tarjeta de confianza: se puede escribir, pero no enviar
  ocupado: false,
  modeloEstado: "cargando",
};

// valores de escritorio/config.py; llegan con /api/estado
const ajustes = { inactividadMin: 30, intervaloLatido: 15, cps: 60 };

let ultimaActividad = Date.now();
let chequeoEstadoTimer = null;
let completarEscritura = null;

// ------------------------------------------------------------------ Comunicación API
async function api(path, method = "GET", data = null) {
  // los eventos pendientes van antes que la acción: así quedan con el alumno, el intento y el orden correctos
  if (method === "POST" && path !== "/api/latido") await tele.vaciar();
  const opts = {
    method,
    headers: {
      "Accept": "application/json",
    },
  };
  if (apiToken) {
    opts.headers["X-Tutor-Token"] = apiToken;
  }
  if (data !== null) {
    opts.headers["Content-Type"] = "application/json; charset=utf-8";
    opts.body = JSON.stringify(data);
  }

  const res = await fetch(path, opts);
  if (res.status === 403) {
    setStatus("Acceso denegado: token inválido", false);
    throw new Error("403 Forbidden");
  }
  const json = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(json.error || `Error ${res.status}`);
  }
  return json;
}

// ------------------------------------------------------------------ Utilidades UI
function setStatus(text, busy = false) {
  const el = $("#status");
  if (!el) return;
  el.textContent = text;
  el.classList.toggle("busy", busy);
}

function mostrarEstadoModelo(estado) {
  if (estado) state.modeloEstado = estado;
  const [texto, ocupado] = ESTADOS_MODELO[state.modeloEstado] || ESTADOS_MODELO.sin_modelo;
  setStatus(texto, ocupado);
}

function show(id) {
  document.querySelectorAll(".screen").forEach((s) => {
    s.classList.toggle("active", s.id === id);
  });
  tele.desplazamientoAutomatico();
  window.scrollTo(0, 0);
}

function registrarActividad() {
  ultimaActividad = Date.now();
}

function actualizarUsuarioBox() {
  const box = $("#usuario-box");
  const badge = $("#codigo-badge");
  const btnExp = $("#btn-exportar");

  if (state.codigo) {
    box.style.display = "flex";
    badge.textContent = state.codigo;
    btnExp.style.display = state.consentimiento ? "inline-block" : "none";
  } else {
    box.style.display = "none";
  }
}

function escaparHtml(s) {
  return s.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Markdown mínimo del consentimiento: título, listas anidadas por sangría de 2 espacios y **negritas**.
function renderMarkdown(md) {
  const enLinea = (t) => escaparHtml(t).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  const out = [];
  let nivel = 0;
  const cerrarHasta = (n) => {
    while (nivel > n) {
      out.push("</li></ul>");
      nivel -= 1;
    }
  };
  for (const linea of md.split(/\r?\n/)) {
    const item = linea.match(/^( *)- (.*)$/);
    if (item) {
      const n = Math.floor(item[1].length / 2) + 1;
      if (n > nivel) {
        while (nivel < n) {
          out.push("<ul><li>");
          nivel += 1;
        }
      } else {
        cerrarHasta(n);
        out.push("</li><li>");
      }
      out.push(enLinea(item[2]));
      continue;
    }
    cerrarHasta(0);
    const titulo = linea.match(/^#+\s+(.*)$/);
    if (titulo) out.push(`<h2>${enLinea(titulo[1])}</h2>`);
    else if (linea.trim()) out.push(`<p>${enLinea(linea)}</p>`);
  }
  cerrarHasta(0);
  return out.join("");
}

async function mostrarConsentimiento() {
  try {
    const res = await api("/api/consentimiento");
    $("#consentimiento-texto").innerHTML = renderMarkdown(res.texto);
  } catch (err) {
    $("#consentimiento-texto").textContent = "No se pudo cargar el aviso: " + err.message;
  }
  show("pantalla-consentimiento");
}

async function entrarConEstado(codigo, consentimiento) {
  state.codigo = codigo;
  state.consentimiento = consentimiento;
  actualizarUsuarioBox();
  if (consentimiento === null || consentimiento === undefined) {
    await mostrarConsentimiento();
  } else {
    await cargarProblemas();
    show("pantalla-problemas");
  }
}

function salirALaPantallaDeCodigo() {
  state.codigo = null;
  state.consentimiento = null;
  state.currentProblem = null;
  state.ocupado = false;
  actualizarUsuarioBox();
  show("pantalla-codigo");
}

// ------------------------------------------------------------------ Pantalla 1: Código
function initPantallaCodigo() {
  $("#form-codigo").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const codigoInput = $("#codigo").value.trim();
    if (!codigoInput) return;

    setStatus("Verificando código…", true);
    try {
      const res = await api("/api/entrar", "POST", { codigo: codigoInput, pantalla: tele.pantalla() });
      $("#codigo").value = "";
      mostrarEstadoModelo();
      await entrarConEstado(res.codigo, res.requiere_consentimiento ? null : res.consentimiento);
    } catch (err) {
      mostrarEstadoModelo();
      alert(err.message || "Código no válido.");
    }
  });
}

// ------------------------------------------------------------------ Pantalla 2: Consentimiento
function initPantallaConsentimiento() {
  const responder = async (acepta) => {
    try {
      await api("/api/consentimiento", "POST", { acepta });
      await entrarConEstado(state.codigo, acepta);
    } catch (err) {
      alert("Error al registrar consentimiento: " + err.message);
    }
  };
  $("#btn-consent-acepto").addEventListener("click", () => responder(true));
  $("#btn-consent-rechazo").addEventListener("click", () => responder(false));
}

// ------------------------------------------------------------------ Pantalla 3: Problemas
function renderModes() {
  const box = $("#modes");
  box.innerHTML = "";
  const modos = [
    ["aprender", "Aprender"],
    ["practicar", "Practicar"],
    ["verificar", "Verificar"],
    ["simulador", "Simulador"],
  ];
  for (const [key, label] of modos) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = label;
    b.setAttribute("role", "radio");
    b.setAttribute("aria-checked", String(key === state.mode));
    b.addEventListener("click", () => {
      tele.clic("modo", { modo: key });
      state.mode = key;
      renderModes();
      renderProblems();
    });
    box.appendChild(b);
  }
  $("#mode-help").textContent = MODE_HELP[state.mode];
}

async function cargarProblemas() {
  try {
    const res = await api("/api/problemas");
    state.problems = res.problems || [];
    renderModes();
    renderProblems();
  } catch (err) {
    console.error("Error cargando problemas:", err);
  }
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
    b.innerHTML = `<strong></strong><span class="small muted"></span><div class="pcard-tags"></div>`;
    b.querySelector("strong").textContent = p.title.replace(/^[^·]+·\s*/, "");
    b.querySelector(".muted").textContent = p.source;

    const tagsDiv = b.querySelector(".pcard-tags");
    if (p.level) {
      const tagNivel = document.createElement("span");
      tagNivel.className = "tag";
      tagNivel.textContent = p.level;
      tagsDiv.appendChild(tagNivel);
    }

    const estadoModo = p.estados ? p.estados[state.mode] : "nuevo";
    if (estadoModo === "en_curso") {
      const tagEstado = document.createElement("span");
      tagEstado.className = "tag en-curso";
      tagEstado.textContent = "En curso";
      tagsDiv.appendChild(tagEstado);
    } else if (estadoModo === "terminado") {
      const tagEstado = document.createElement("span");
      tagEstado.className = "tag terminado";
      tagEstado.textContent = "Terminado";
      tagsDiv.appendChild(tagEstado);
    }

    b.addEventListener("click", () => {
      tele.clic("problema", { problema: p.id });
      startProblem(p.id);
    });
    grid.appendChild(b);
  }
}

// ------------------------------------------------------------------ Pantalla 4: Sesión y Chat
function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  div.textContent = text;
  $("#chat").appendChild(div);
  bajarA(div, true);
  if (role === "tutor") tele.tutorMostrado();
  return div;
}

function bajarA(el, suave = false) {
  tele.desplazamientoAutomatico();
  el.scrollIntoView(suave ? { block: "end", behavior: "smooth" } : { block: "end" });
}

function addMessageTutorConEfecto(texto, onComplete = null) {
  if (completarEscritura) completarEscritura();
  const div = document.createElement("div");
  div.className = "msg tutor";
  $("#chat").appendChild(div);
  // el tiempo de respuesta del alumno cuenta desde que el mensaje empieza a aparecer
  tele.tutorMostrado();

  let i = 0;
  let terminado = false;
  div.title = "Haz clic para mostrar el texto completo";

  const intervalo = setInterval(() => {
    if (i < texto.length) {
      div.textContent += texto[i];
      i += 1;
      bajarA(div);
    } else {
      finalizar();
    }
  }, 1000 / ajustes.cps);

  function finalizar() {
    if (terminado) return;
    terminado = true;
    clearInterval(intervalo);
    div.textContent = texto;
    div.removeAttribute("title");
    bajarA(div, true);
    if (completarEscritura === finalizar) completarEscritura = null;
    if (onComplete) onComplete();
  }

  completarEscritura = finalizar;
  div.addEventListener("click", finalizar);
  return div;
}

function abrirModalDibujo(src, titulo) {
  tele.dibujoAbierto();
  const modal = $("#modal-dibujo");
  const img = $("#modal-img");
  const modalTitulo = $("#modal-titulo");
  img.src = src;
  modalTitulo.textContent = titulo;
  modal.style.display = "flex";
}

function cerrarModalDibujo() {
  if ($("#modal-dibujo").style.display === "none") return;
  tele.dibujoCerrado();
  $("#modal-dibujo").style.display = "none";
}

function renderProblemCard(p) {
  const card = $("#problema");
  card.innerHTML = `
    <header><h2></h2><button type="button" class="link" id="cambiar">Cambiar problema</button></header>
    <p class="statement"></p>
    <details><summary>Elementos del circuito</summary><ul class="elements"></ul></details>`;
  card.querySelector("h2").textContent = p.title;
  card.querySelector(".statement").textContent = p.statement;
  card.querySelector("details").addEventListener("toggle", (e) => {
    if (e.target.open) tele.clic("elementos_circuito");
  });

  if (p.image) {
    const fig = document.createElement("figure");
    fig.className = "diagram";
    const imgUrl = "img/" + encodeURIComponent(p.image);
    const img = document.createElement("img");
    img.src = imgUrl;
    img.alt = "Diagrama del circuito: " + p.title;
    fig.appendChild(img);
    const cap = document.createElement("figcaption");
    cap.textContent = "Toca el dibujo para verlo ampliado.";
    fig.append(cap);
    fig.addEventListener("click", () => abrirModalDibujo(imgUrl, p.title));
    card.querySelector(".statement").before(fig);
  }

  const ul = card.querySelector(".elements");
  for (const line of [...p.elements, ...p.meshes]) {
    const li = document.createElement("li");
    li.textContent = line.replace(/^-\s*/, "");
    ul.appendChild(li);
  }

  $("#cambiar").addEventListener("click", async () => {
    tele.clic("cambiar_problema");
    if (completarEscritura) completarEscritura();
    await cargarProblemas();
    show("pantalla-problemas");
  });
}

async function startProblem(pid, custom = "", method = "") {
  setStatus("Iniciando problema…", true);
  try {
    const res = await api("/api/iniciar", "POST", {
      problema: pid,
      modo: state.mode,
      propio: custom,
      metodo: method,
    });
    mostrarEstadoModelo();

    state.currentProblem = res.problem;
    state.finished = res.finished;
    state.ocupado = false;
    $("#chat").innerHTML = "";
    $("#entrada").value = "";
    autoGrow();
    renderProblemCard(res.problem);
    show("pantalla-sesion");

    if (res.retomado && res.chat) {
      for (const m of res.chat) {
        addMessage(m.rol, m.texto);
      }
    } else if (res.message) {
      addMessageTutorConEfecto(res.message);
    }

    setComposer();
    if (!state.finished) $("#entrada").focus();
  } catch (err) {
    mostrarEstadoModelo();
    alert("Error al iniciar el problema: " + err.message);
  }
}

// El cuadro de texto sólo se bloquea al terminar el problema: mientras el tutor contesta se puede
// seguir escribiendo, y lo que se bloquea es enviar.
function setComposer() {
  const cerrado = state.finished;
  $("#entrada").disabled = cerrado;
  $("#enviar").disabled = cerrado || state.ocupado;
  $("#pista").disabled = cerrado || state.ocupado;
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
  again.addEventListener("click", async () => {
    await cargarProblemas();
    show("pantalla-problemas");
  });
  wrap.appendChild(again);
}

function mostrarIndicadorEscribiendo() {
  const typing = document.createElement("div");
  typing.className = "typing";
  typing.textContent = "El tutor está escribiendo…";
  $("#chat").appendChild(typing);
  bajarA(typing, true);
  return typing;
}

function mostrarRespuesta(res) {
  state.ocupado = false;
  if (res.terminado) state.finished = true;
  setComposer();
  addMessageTutorConEfecto(res.mensaje, () => {
    if (res.terminado && res.indicadores) showIndicators(res.indicadores);
  });
  if (!state.finished) $("#entrada").focus();
}

function pedirConfianza() {
  const t0Confianza = Date.now();

  const card = document.createElement("div");
  card.className = "tarjeta-confianza card";
  card.innerHTML = `
    <div class="confianza-titulo">Antes de revisarla: ¿qué tan seguro estás?</div>
    <div class="confianza-escala">
      <span class="escala-ayuda">1 = Nada</span>
      <div class="confianza-botones">
        <button type="button" data-val="1">1</button>
        <button type="button" data-val="2">2</button>
        <button type="button" data-val="3">3</button>
        <button type="button" data-val="4">4</button>
        <button type="button" data-val="5">5</button>
      </div>
      <span class="escala-ayuda">5 = Totalmente</span>
    </div>
    <button type="button" class="btn-prefiero ghost" data-val="">Prefiero no decir</button>
  `;

  $("#chat").appendChild(card);
  bajarA(card, true);

  const responderConfianza = async (valor) => {
    const ms = Date.now() - t0Confianza;
    card.remove();
    const typing = mostrarIndicadorEscribiendo();
    try {
      const res = await api("/api/confianza", "POST", { valor, ms });
      typing.remove();
      mostrarRespuesta(res);
    } catch (err) {
      typing.remove();
      addMessage("error", "Error al procesar la respuesta: " + err.message);
      state.ocupado = false;
      setComposer();
    }
  };

  card.querySelectorAll("button").forEach((b) => {
    b.addEventListener("click", () => {
      const v = b.getAttribute("data-val");
      responderConfianza(v ? parseInt(v, 10) : null);
    });
  });
}

async function send(text) {
  text = text.trim();
  if (!text || state.finished || state.ocupado) return;
  if (completarEscritura) completarEscritura();
  const composicion = tele.composicion();

  addMessage("alumno", text);
  $("#entrada").value = "";
  autoGrow();
  state.ocupado = true;
  setComposer();

  const typing = mostrarIndicadorEscribiendo();
  try {
    const res = await api("/api/responder", "POST", { texto: text, composicion });
    typing.remove();
    if (res.necesita_confianza) {
      pedirConfianza();
      return;
    }
    mostrarRespuesta(res);
  } catch (err) {
    typing.remove();
    addMessage("error", "Algo falló al revisar tu respuesta: " + err.message);
    state.ocupado = false;
    setComposer();
  }
}

async function pedirPista() {
  if (state.finished || state.ocupado) return;
  tele.clic("pista");
  if (completarEscritura) completarEscritura();
  addMessage("alumno", "Dame una pista");
  state.ocupado = true;
  setComposer();

  const typing = mostrarIndicadorEscribiendo();
  try {
    const res = await api("/api/pista", "POST", {});
    typing.remove();
    mostrarRespuesta(res);
  } catch (err) {
    typing.remove();
    addMessage("error", "No se pudo obtener la pista: " + err.message);
    state.ocupado = false;
    setComposer();
  }
}

function autoGrow() {
  const t = $("#entrada");
  t.style.height = "auto";
  const max = window.innerHeight * 0.4;
  t.style.height = Math.min(t.scrollHeight, max) + "px";
  t.style.overflowY = t.scrollHeight > max ? "auto" : "hidden";
}

function initSession() {
  $("#composer").addEventListener("submit", (ev) => {
    ev.preventDefault();
    send($("#entrada").value);
  });
  $("#entrada").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      send($("#entrada").value);
    }
  });
  $("#entrada").addEventListener("input", autoGrow);
  $("#pista").addEventListener("click", pedirPista);

  $("#propio-empezar").addEventListener("click", () => {
    const text = $("#propio").value.trim();
    if (text) startProblem("", text, $("#propio-metodo").value);
  });

  $("#btn-salir").addEventListener("click", async () => {
    tele.clic("salir");
    try {
      await api("/api/salir", "POST", { motivo: "boton" });
    } catch (_) {}
    salirALaPantallaDeCodigo();
  });

  $("#btn-exportar").addEventListener("click", async () => {
    tele.clic("exportar");
    try {
      await api("/api/exportar", "POST", {});
    } catch (e) {
      alert(e.message);
    }
  });

  // Modal
  $("#modal-cerrar").addEventListener("click", cerrarModalDibujo);
  $("#modal-overlay").addEventListener("click", cerrarModalDibujo);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") cerrarModalDibujo();
  });
}

// ------------------------------------------------------------------ Latidos e Inactividad
function iniciarLatidosEInactividad() {
  setInterval(async () => {
    try {
      const res = await api("/api/latido", "POST", {});
      if (res.modelo_estado && res.modelo_estado !== state.modeloEstado && !state.ocupado) {
        mostrarEstadoModelo(res.modelo_estado);
      }
    } catch (_) {
      setStatus("Sin conexión con el tutor", false);
    }
  }, ajustes.intervaloLatido * 1000);

  const eventos = ["mousemove", "keydown", "touchstart", "scroll", "click"];
  eventos.forEach((evt) => {
    window.addEventListener(evt, registrarActividad, { passive: true });
  });

  setInterval(async () => {
    if (state.codigo && Date.now() - ultimaActividad > ajustes.inactividadMin * 60 * 1000) {
      try {
        await api("/api/salir", "POST", { motivo: "inactividad" });
      } catch (_) {}
      salirALaPantallaDeCodigo();
      alert(`La sesión se cerró automáticamente por inactividad (${ajustes.inactividadMin} min).`);
    }
  }, 10000);
}

// ------------------------------------------------------------------ Arranque
async function arrancar() {
  initPantallaCodigo();
  initPantallaConsentimiento();
  initSession();

  let st = null;
  try {
    st = await api("/api/estado");
    ajustes.inactividadMin = st.inactividad_min || ajustes.inactividadMin;
    ajustes.intervaloLatido = st.intervalo_latido || ajustes.intervaloLatido;
    ajustes.cps = st.caracteres_por_segundo || ajustes.cps;
    mostrarEstadoModelo(st.modelo_estado);
  } catch (err) {
    setStatus("Sin conexión con el tutor", false);
  }
  iniciarLatidosEInactividad();

  // al recargar la página el alumno sigue dentro
  if (st && st.codigo) await entrarConEstado(st.codigo, st.consentimiento);

  const preparando = (e) => e === "cargando" || e === "detenido";
  if (preparando(state.modeloEstado)) {
    chequeoEstadoTimer = setInterval(async () => {
      try {
        const s = await api("/api/estado");
        if (!state.ocupado) mostrarEstadoModelo(s.modelo_estado);
        if (!preparando(s.modelo_estado)) clearInterval(chequeoEstadoTimer);
      } catch (_) {}
    }, 3000);
  }
}

arrancar();
