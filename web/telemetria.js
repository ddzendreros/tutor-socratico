// Eventos de la interfaz para el registro del alumno (docs/DATOS.md). Sólo lo que pasa dentro de la
// ventana del tutor. El servidor descarta todo si el alumno no aceptó participar.
"use strict";

(function (window) {
  const MAX_COLA = 1000;
  const PAUSA_MS = 2000;
  const INACTIVIDAD_MS = 60000;
  const INTERVALO_DESPLAZAMIENTO_MS = 2000;

  const cola = [];
  let token = "";

  function ahora() {
    return performance.now();
  }

  function registrar(tipo, datos) {
    if (cola.length >= MAX_COLA) cola.shift();
    cola.push({ tipo, datos: datos || {}, ms: Date.now(), mono: Math.round(ahora()) });
  }

  // Manda lo acumulado. `alSalir` usa keepalive para que el envío sobreviva al cierre de la página.
  async function vaciar(alSalir = false) {
    if (!cola.length || !token) return;
    const lote = cola.splice(0, cola.length);
    try {
      await fetch("/api/eventos", {
        method: "POST",
        headers: { "Content-Type": "application/json; charset=utf-8", "X-Tutor-Token": token },
        body: JSON.stringify({ eventos: lote, mono_envio: Math.round(ahora()) }),
        keepalive: alSalir,
      });
    } catch (_) {
      // sin conexión: se reintenta en el siguiente envío
      const cabe = MAX_COLA - cola.length;
      if (cabe > 0) cola.unshift(...lote.slice(-cabe));
    }
  }

  // ---------------------------------------------------------------- composición del mensaje
  let tTutor = null;
  let comp = null;

  function compNueva() {
    return {
      tPrimeraTecla: null, tInicio: null, tUltimaEdicion: null, teclas: 0, borrados: 0, pegados: 0,
      caracteresPegados: 0, pausas: 0, focoPerdido: 0, eventos: [],
    };
  }
  comp = compNueva();

  function tutorMostrado() {
    tTutor = ahora();
  }

  function editado(t) {
    if (comp.tInicio === null) comp.tInicio = t;
    if (comp.tUltimaEdicion !== null && t - comp.tUltimaEdicion >= PAUSA_MS) comp.pausas += 1;
    comp.tUltimaEdicion = t;
  }

  function composicion() {
    const t = ahora();
    const c = comp;
    comp = compNueva();
    return {
      ms_desde_tutor: tTutor === null ? null : Math.round(t - tTutor),
      // negativo si empezó a escribir mientras esperaba la respuesta del tutor
      ms_primera_tecla: tTutor === null || c.tPrimeraTecla === null ? null : Math.round(c.tPrimeraTecla - tTutor),
      ms_escribiendo: c.tInicio === null ? 0 : Math.round(t - c.tInicio),
      teclas: c.teclas,
      borrados: c.borrados,
      pegados: c.pegados,
      caracteres_pegados: c.caracteresPegados,
      pausas_2s: c.pausas,
      foco_perdido: c.focoPerdido,
      eventos: c.eventos,
    };
  }

  function escribiendo() {
    const entrada = document.querySelector("#entrada");
    return comp.tInicio !== null && entrada && entrada.value.length > 0;
  }

  function initComposicion() {
    const entrada = document.querySelector("#entrada");
    entrada.addEventListener("keydown", (e) => {
      // Enter envía el mensaje; app.js lo atiende antes y para entonces ya empezó la composición siguiente
      if (e.key === "Enter" && !e.shiftKey) return;
      if (comp.tPrimeraTecla === null) comp.tPrimeraTecla = ahora();
      comp.teclas += 1;
      if (e.key === "Backspace" || e.key === "Delete") comp.borrados += 1;
    });
    entrada.addEventListener("input", (e) => {
      const t = ahora();
      editado(t);
      comp.eventos.push([Math.round(t - comp.tInicio), e.inputType || "", e.data || "", entrada.selectionStart || 0]);
    });
    entrada.addEventListener("paste", (e) => {
      const t = ahora();
      const texto = (e.clipboardData && e.clipboardData.getData("text")) || "";
      editado(t);
      comp.pegados += 1;
      comp.caracteresPegados += texto.length;
      comp.eventos.push([Math.round(t - comp.tInicio), "paste", texto.slice(0, 2000), entrada.selectionStart || 0]);
    });
  }

  // ---------------------------------------------------------------- portapapeles
  function origen(nodo) {
    const el = nodo && (nodo.nodeType === 1 ? nodo : nodo.parentElement);
    if (!el || !el.closest) return "otro";
    if (el.closest("#entrada")) return "entrada";
    if (el.closest("#chat .msg.tutor")) return "tutor";
    if (el.closest("#chat .msg.alumno")) return "alumno";
    if (el.closest("#problema")) return "enunciado";
    return "otro";
  }

  function textoSeleccionado(e) {
    const el = e.target;
    // en un cuadro de texto la selección no aparece en getSelection()
    if (el && (el.tagName === "TEXTAREA" || el.tagName === "INPUT") && typeof el.selectionStart === "number") {
      return el.value.slice(el.selectionStart, el.selectionEnd);
    }
    return String(window.getSelection() || "");
  }

  function initPortapapeles() {
    for (const tipo of ["copy", "cut"]) {
      document.addEventListener(tipo, (e) => {
        const texto = textoSeleccionado(e);
        const sel = window.getSelection();
        const nodo = e.target && (e.target.tagName === "TEXTAREA" || e.target.tagName === "INPUT")
          ? e.target : sel && sel.anchorNode;
        registrar(tipo === "copy" ? "copiar" : "cortar",
          { origen: origen(nodo), caracteres: texto.length, texto: texto.slice(0, 500) });
      });
    }
    document.addEventListener("paste", (e) => {
      const texto = (e.clipboardData && e.clipboardData.getData("text")) || "";
      registrar("pegar", { origen: origen(e.target), caracteres: texto.length, texto: texto.slice(0, 500) });
    });
  }

  // ---------------------------------------------------------------- ventana e inactividad
  let ultimaActividad = Date.now();
  let inactivo = false;
  let timerInactividad = null;

  function actividad() {
    const t = Date.now();
    if (inactivo) {
      inactivo = false;
      registrar("inactividad", { estado: "fin", ms_sin_actividad: t - ultimaActividad });
    }
    ultimaActividad = t;
    clearTimeout(timerInactividad);
    timerInactividad = setTimeout(() => {
      inactivo = true;
      registrar("inactividad", { estado: "inicio", ms_sin_actividad: Date.now() - ultimaActividad });
    }, INACTIVIDAD_MS);
  }

  function initVentana() {
    document.addEventListener("visibilitychange", () => {
      registrar("ventana", { estado: document.hidden ? "oculta" : "visible" });
      if (document.hidden) vaciar(true);
    });
    window.addEventListener("blur", () => {
      if (escribiendo()) comp.focoPerdido += 1;
      registrar("ventana", { estado: "desenfocada" });
    });
    window.addEventListener("focus", () => registrar("ventana", { estado: "enfocada" }));
    window.addEventListener("pagehide", () => vaciar(true));
    for (const evt of ["mousemove", "mousedown", "keydown", "wheel", "touchstart"]) {
      window.addEventListener(evt, actividad, { passive: true });
    }
    actividad();
  }

  // ---------------------------------------------------------------- desplazamiento del chat
  let autoHasta = 0;
  let ultimoDesplazamiento = 0;
  let timerDesplazamiento = null;

  // los desplazamientos que hace la propia interfaz (mensaje nuevo, efecto de escritura) no cuentan
  function desplazamientoAutomatico() {
    autoHasta = ahora() + 800;
  }

  // el chat no tiene barra propia: se desplaza la página de la sesión (problema y chat)
  function initDesplazamiento() {
    const pagina = document.scrollingElement || document.documentElement;
    const anotar = () => {
      timerDesplazamiento = null;
      ultimoDesplazamiento = ahora();
      const max = pagina.scrollHeight - pagina.clientHeight;
      registrar("chat_desplazamiento", { posicion: max > 0 ? Math.round((pagina.scrollTop / max) * 1000) / 1000 : 1 });
    };
    window.addEventListener("scroll", () => {
      if (!document.querySelector("#pantalla-sesion.active")) return;
      if (ahora() < autoHasta || timerDesplazamiento) return;
      // como mucho uno cada 2 s, con la posición en la que quedó
      timerDesplazamiento = setTimeout(anotar, Math.max(0, INTERVALO_DESPLAZAMIENTO_MS - (ahora() - ultimoDesplazamiento)));
    }, { passive: true });
  }

  // ---------------------------------------------------------------- clics, dibujo y pantalla
  let tDibujo = null;

  function clic(control, extra) {
    registrar("clic", Object.assign({ control }, extra || {}));
  }

  function dibujoAbierto() {
    tDibujo = ahora();
    registrar("dibujo", { accion: "abrir" });
  }

  function dibujoCerrado() {
    if (tDibujo === null) return;
    registrar("dibujo", { accion: "cerrar", ms_abierto: Math.round(ahora() - tDibujo) });
    tDibujo = null;
  }

  function pantalla() {
    return {
      ancho: window.screen.width, alto: window.screen.height, escala: window.devicePixelRatio || 1,
      ancho_ventana: window.innerWidth, alto_ventana: window.innerHeight,
    };
  }

  function initTamano() {
    let antes = pantalla();
    let timer = null;
    window.addEventListener("resize", () => {
      // al cambiar el tamaño, el navegador recoloca la página: no es un desplazamiento del alumno
      desplazamientoAutomatico();
      clearTimeout(timer);
      timer = setTimeout(() => {
        const p = pantalla();
        if (p.ancho !== antes.ancho || p.alto !== antes.alto || p.escala !== antes.escala) {
          registrar("pantalla", { ancho: p.ancho, alto: p.alto, escala: p.escala });
        }
        if (p.ancho_ventana !== antes.ancho_ventana || p.alto_ventana !== antes.alto_ventana) {
          registrar("tamano_ventana", { ancho: p.ancho_ventana, alto: p.alto_ventana });
        }
        antes = p;
      }, 1000);
    });
  }

  function init() {
    initComposicion();
    initPortapapeles();
    initVentana();
    initDesplazamiento();
    initTamano();
    setInterval(() => vaciar(), 2000);
  }

  window.Telemetria = {
    fijarToken: (t) => { token = t; },
    vaciar,
    tutorMostrado,
    composicion,
    clic,
    dibujoAbierto,
    dibujoCerrado,
    pantalla,
    desplazamientoAutomatico,
  };

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})(window);
