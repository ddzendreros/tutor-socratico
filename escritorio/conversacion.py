"""Módulo de conversación y adaptación de mensajes con modelos de lenguaje.

Clasifica la intención del alumno y redacta mensajes pedagógicos cálidos
manteniendo la fidelidad determinista al mensaje base del motor.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from escritorio.config import (MAX_TOKENS_INTENCION, MAX_TOKENS_VOZ, TEMPERATURA_INTENCION, TEMPERATURA_VOZ,
                               TEMPERATURA_VOZ_REINTENTO, TIMEOUT_INTENCION, TIMEOUT_VOZ)
from tutor.llm import LLM, ajustar_veredicto, guard

# (alumno, mensaje base del motor, cómo lo diría el tutor); mensajes reales del motor
EJEMPLOS_VOZ = [
    ("dos",
     "Correcto. Todas las corrientes de malla girarán en sentido horario (FMR), como en el dibujo. "
     "¿Qué elementos recorre la corriente I1?",
     "Así es. Vamos a tomar todas las corrientes de malla en sentido horario (FMR), como en el dibujo. "
     "¿Por qué elementos pasa la corriente I1?"),
    ("no sé",
     "Una malla es un lazo que no encierra a otros lazos. Si miras el circuito como un plano, "
     "¿cuántas \"ventanas\" cerradas ves?",
     "Sin problema, vamos por partes: una malla es un lazo que no encierra a otros lazos. "
     "Si ves el circuito como un plano, ¿cuántas \"ventanas\" cerradas encuentras?"),
    ("I1 + I2",
     "Imagina que por esa rama I1 = 3 A intenta circular en un sentido e I2 = 1 A en sentido contrario. "
     "¿La corriente resultante sería 4 A o 2 A?",
     "Piénsalo con un caso: si por esa rama I1 = 3 A va en un sentido e I2 = 1 A en el contrario, "
     "¿qué corriente resulta, 4 A o 2 A?"),
]

PROMPT_VOZ = (
    "Eres el tutor de Circuitos Eléctricos de la clase. Hablas español de México, "
    "de tú, con calidez y naturalidad, como un buen asesor que platica con el alumno. "
    "Eres una IA; no lo ocultas, pero no lo mencionas si no te preguntan. "
    "Recibes lo que dijo el alumno y un MENSAJE BASE. Di ese mensaje con tus palabras: "
    "retoma lo que dijo el alumno; conserva todo el contenido y termina con la misma "
    "pregunta (puedes reformularla); no agregues números, fórmulas, pistas ni pasos; "
    "no repitas números que dijo el alumno si no están en el mensaje base; "
    "nunca digas que algo está bien o mal si el mensaje base no lo dice, y si lo dice, dilo también; "
    "no digas qué hizo, tiene o sabe el alumno; no expliques ni agregues datos, leyes o conceptos; "
    "termina siempre con la pregunta del mensaje base entre signos ¿?; "
    "máximo 3 oraciones cortas, sin emojis ni listas. Responde sólo con el mensaje.\n\n"
    + "\n\n".join(f"Ejemplo {i}:\nAlumno: {a}\nMensaje base: {b}\nTutor: {t}"
                  for i, (a, b, t) in enumerate(EJEMPLOS_VOZ, 1))
)

# definiciones en el mensaje de sistema y ejemplos como turnos de conversación: así los modelos
# pequeños reconocen mejor una respuesta escrita como pregunta
DEFINICIONES_INTENCION = {
    "respuesta": "propone una respuesta al paso, aunque la escriba como pregunta (¿la malla de abajo es real?, ¿van en serie?)",
    "ayuda": "pide una pista o no sabe cómo seguir",
    "pregunta_teoria": "pregunta por un concepto, ley o definición",
    "pregunta_paso": "pregunta qué se le está pidiendo en el paso actual",
    "animo": "expresa cansancio, frustración o desánimo",
    "fuera_de_tema": "habla de algo ajeno a la clase",
    "charla": "saluda, agradece o se despide",
}
EJEMPLOS_INTENCION = {
    "respuesta": ["5", "5 A", "¿2 A?", "i1 = 3"],
    "ayuda": ["¿me das una pista?", "¿me ayudas con esto?", "dame una pista", "ayuda por favor"],
    "pregunta_teoria": ["¿por qué se resta?", "¿qué es la ley de Ohm?", "¿cómo funciona una supermalla?",
                        "¿qué es un nodo esencial?"],
    "pregunta_paso": ["¿qué me pides?", "¿qué tengo que poner?", "¿cuál es el paso?", "¿qué cálculo sigue?"],
    "animo": ["ya me cansé", "está muy difícil", "no puedo con esto", "ya me frustré"],
    "fuera_de_tema": ["¿te gusta el fútbol?", "¿cuál es la capital de Francia?", "cuéntame un chiste",
                      "¿qué hora tienes?"],
    "charla": ["hola", "buenos días", "buenas tardes", "muchas gracias"],
}
_PASO_DE_EJEMPLO = "¿Qué tipo es cada malla: real, fantasma o supermalla?"

PROMPT_INTENCION = (
    "Clasifica el mensaje del alumno en una etiqueta. Etiquetas:\n"
    + "\n".join(f"- {k}: {v}" for k, v in DEFINICIONES_INTENCION.items())
    + "\nEn la duda: respuesta. Responde sólo con el JSON."
)


def mensajes_intencion(texto: str, pregunta_paso: str = "") -> list[dict]:
    mensajes = [{"role": "system", "content": PROMPT_INTENCION}]
    for etiqueta, ejemplos in EJEMPLOS_INTENCION.items():
        for ejemplo in ejemplos:
            mensajes += [
                {"role": "user", "content": f"Pregunta actual: {_PASO_DE_EJEMPLO}\nMensaje del alumno: {ejemplo}"},
                {"role": "assistant", "content": json.dumps({"intencion": etiqueta})},
            ]
    actual = f"Pregunta actual: {pregunta_paso}\n" if pregunta_paso else ""
    mensajes.append({"role": "user", "content": f"{actual}Mensaje del alumno: {texto}"})
    return mensajes

INTENCIONES_VALIDAS = [
    "respuesta",
    "ayuda",
    "pregunta_teoria",
    "pregunta_paso",
    "animo",
    "fuera_de_tema",
    "charla",
]

SCHEMA_INTENCION = {
    "name": "clasificacion_intencion",
    "schema": {
        "type": "object",
        "properties": {
            "intencion": {
                "type": "string",
                "enum": INTENCIONES_VALIDAS,
            }
        },
        "required": ["intencion"],
        "additionalProperties": False,
    },
}

PLANTILLAS_BASE = {
    "pregunta_paso": "Lo que te pido ahora es esto: {pregunta}",
    "animo": "Es normal que este paso cueste; vamos con calma. Si quieres, pide una pista. {pregunta}",
    "fuera_de_tema": "Eso se sale de lo que estamos viendo; sigamos con el circuito. {pregunta}",
    "charla": "¡Con gusto! {pregunta}",
}

PROMPT_TEORIA = (
    "Responde la duda usando SÓLO este material de clase. "
    "2 o 3 oraciones con la notación del profesor, en texto simple, sin LaTeX ni signos de dólar. "
    "No resuelvas el problema del alumno ni uses sus valores. "
    "Termina regresando al paso: «{pregunta_paso}»."
)

PROMPTS_HASH = hashlib.sha256(
    "\n".join([PROMPT_VOZ, PROMPT_TEORIA, json.dumps(mensajes_intencion(""), ensure_ascii=False),
               json.dumps(SCHEMA_INTENCION, sort_keys=True),
               json.dumps(PLANTILLAS_BASE, sort_keys=True, ensure_ascii=False)]).encode("utf-8")
).hexdigest()[:12]

# etiquetas que los modelos pequeños copian del formato de los ejemplos
_ETIQUETA_RE = re.compile(r"^\s*(tutor|salida|mensaje(\s+base)?|respuesta)\s*:\s*", re.I)


def plantilla_para_intencion(intencion: str, pregunta: str = "") -> str:
    """Devuelve la plantilla predeterminada para intenciones que no van al motor."""
    patron = PLANTILLAS_BASE.get(intencion, "{pregunta}")
    return patron.format(pregunta=pregunta).strip()


def contexto_guardia(sesion) -> dict:
    """Lo que la guardia necesita saber del motor después de un turno: resultados y
    ecuaciones que el alumno aún no obtiene, variables y valores del circuito."""
    return {
        "forbidden": sesion.forbidden_numbers(),
        "pending_eqs": sesion.pending_equations(),
        "permite_ecuaciones": sesion.ultimo_turno.get("permite_ecuaciones", sesion.level >= 4),
        "var_names": list(sesion.vars),
        "known": {float(e.value) for e in sesion.circuit.elements},
    }


def limpiar_salida(texto: str) -> str:
    texto = _ETIQUETA_RE.sub("", texto.strip())
    texto = texto.strip().strip("«»\"“”'").strip()
    # fórmulas en LaTeX a texto simple: $I_{\text{neta}} = I_1 - I_2$ → I_neta = I1 - I2
    texto = re.sub(r"\\(?:text|mathrm)\{([^}]*)\}", r"\1", texto)
    texto = re.sub(r"_\{([^}]*)\}", r"_\1", texto)
    texto = re.sub(r"\b([IVRP])_(\d+)\b", r"\1\2", texto)
    texto = texto.replace("\\cdot", "·").replace("\\,", " ").replace("$", "")
    return re.sub(r"\s+", " ", texto).strip()


def con_pregunta_del_paso(texto: str, pregunta_paso: str) -> str:
    """Una respuesta de teoría debe regresar al paso: si no termina con su pregunta, se le agrega."""
    if not pregunta_paso or texto.rstrip().endswith("?") or pregunta_paso in texto:
        return texto
    return f"{texto.rstrip()} {pregunta_paso}"


def _seisgramas(texto: str) -> set[tuple[str, ...]]:
    palabras = re.findall(r"\w+", texto.casefold())
    return {tuple(palabras[i:i + 6]) for i in range(len(palabras) - 5)}


def copia_un_ejemplo(texto: str, base: str) -> bool:
    """True si el texto repite seis palabras seguidas de la salida de un ejemplo del prompt que no
    están en el mensaje base. No aplica cuando el mensaje base es el del propio ejemplo."""
    propias = _seisgramas(texto) - _seisgramas(base)
    return any(propias & _seisgramas(salida) for _, b, salida in EJEMPLOS_VOZ if b != base)


class Conversador:
    """Gestiona la interacción lingüística: intención y redacción de voz."""

    def __init__(self, llm: LLM | None = None):
        self.llm = llm

    def entender(self, texto: str, pregunta_paso: str = "") -> dict | None:
        """Clasifica la intención del alumno usando el slot 1 a temperatura 0."""
        if self.llm is None or not self.llm.enabled:
            return None

        out, metricas = self.llm.chat(
            mensajes_intencion(texto, pregunta_paso),
            json_schema=SCHEMA_INTENCION,
            id_slot=1,
            seed=0,
            temperature=TEMPERATURA_INTENCION,
            max_tokens=MAX_TOKENS_INTENCION,
            timeout=TIMEOUT_INTENCION,
        )

        if not out:
            return None

        try:
            data = json.loads(out)
            intencion = data.get("intencion")
            if intencion in INTENCIONES_VALIDAS:
                return {
                    "intencion": intencion,
                    "ms": metricas.get("ms", 0),
                }
        except (json.JSONDecodeError, TypeError, AttributeError):
            pass

        return None

    def redactar(self, base: str, alumno: str, contexto: dict | None = None) -> dict[str, Any]:
        """Redacta el mensaje con voz pedagógica usando slot 0 y temperatura 0.4.

        `intentos` guarda cada texto del modelo con el veredicto de la guardia.
        """
        ctx = contexto or {}
        resultado = {
            "texto": base,
            "fuente": "plantilla",
            "guardia": {"ok": True, "motivo": ""},
            "reintentos": 0,
            "ms": 0,
            "tokens_prompt": 0,
            "tokens_salida": 0,
            "tokens_cache": 0,
            "intentos": [],
        }

        if self.llm is None or not self.llm.enabled:
            resultado["guardia"]["motivo"] = "sin modelo"
            return resultado

        messages = [
            {"role": "system", "content": PROMPT_VOZ},
            {"role": "user", "content": (f"{self._formatear_contexto(ctx)}Alumno: {alumno}\n"
                                         f"Mensaje base: {base}\nTutor:")},
        ]

        motivo = ""
        for n, temperatura in enumerate((TEMPERATURA_VOZ, TEMPERATURA_VOZ_REINTENTO)):
            out, metricas = self.llm.chat(messages, id_slot=0, seed=42, temperature=temperatura,
                                          max_tokens=MAX_TOKENS_VOZ, timeout=TIMEOUT_VOZ)
            resultado["ms"] += metricas.get("ms", 0)
            for clave in ("tokens_prompt", "tokens_salida", "tokens_cache"):
                resultado[clave] = metricas.get(clave, 0)
            resultado["reintentos"] = n
            if not out:
                motivo = "sin respuesta del modelo"
                resultado["intentos"].append({"texto": "", "ok": False, "motivo": motivo})
                continue
            texto = ajustar_veredicto(limpiar_salida(out), base)
            ok, motivo = guard(texto, base, ctx.get("forbidden", []), ctx.get("pending_eqs", []),
                               ctx.get("permite_ecuaciones", False), ctx.get("var_names", []),
                               ctx.get("known", set()))
            if ok and copia_un_ejemplo(texto, base):
                ok, motivo = False, "copia un ejemplo"
            resultado["intentos"].append({"texto": texto, "ok": ok, "motivo": "" if ok else motivo})
            if ok:
                resultado.update(texto=texto, fuente="modelo")
                return resultado

        resultado["guardia"] = {"ok": False, "motivo": motivo}
        return resultado

    def _formatear_contexto(self, ctx: dict) -> str:
        partes = []
        titulo = ctx.get("titulo_problema") or ctx.get("titulo")
        if titulo:
            partes.append(f"Problema: {titulo}")
        paso = ctx.get("descripcion_paso") or ctx.get("paso")
        if paso:
            partes.append(f"Paso actual: {paso}")

        intercambios = ctx.get("ultimos_intercambios") or ctx.get("historial")
        if intercambios:
            if isinstance(intercambios, list):
                ultimos = "\n".join(str(x) for x in intercambios[-6:])
            else:
                ultimos = str(intercambios)
            if len(ultimos) > 600:
                ultimos = ultimos[-600:]
            partes.append(f"Intercambios recientes:\n{ultimos}")

        ruta = ctx.get("ruta")
        if ruta:
            partes.append(f"Ruta: {ruta}")
        intencion = ctx.get("intencion")
        if intencion:
            partes.append(f"Intención: {intencion}")

        if not partes:
            return ""
        return "\n".join(partes) + "\n\n"

    def anotar(self, *args, **kwargs) -> None:
        """Se implementa en una fase posterior."""
        pass

    def responder_teoria(self, pregunta: str, pregunta_paso: str, contexto: dict | None = None) -> dict[str, Any]:
        """Responde la duda usando las 2 mejores fichas pedagógicas de clase.

        Pasa por guard() con contexto_guardia(sesion).
        Si no hay ficha sobre el umbral, usa la plantilla predeterminada y redactar().
        Devuelve {texto, fichas: [(id, puntaje)], encontro, fuente: modelo|ficha|plantilla,
        guardia: {ok, motivo}, intentos, ms}; `fuente` "ficha" es el texto de la ficha tal cual.
        """
        from escritorio.conocimiento import buscar, UMBRAL_TEORIA

        ctx = contexto or {}
        resultados = buscar(pregunta, k=2, umbral=UMBRAL_TEORIA)

        if not resultados:
            base = (f"Esa duda no viene en el material de clase que tengo; anótala para "
                    f"preguntarle a tu profesor. Mientras, {pregunta_paso}")
            red = self.redactar(base, pregunta, ctx)
            return {"texto": red["texto"], "fichas": [], "encontro": False, "fuente": red["fuente"],
                    "guardia": red["guardia"], "intentos": red["intentos"], "ms": red["ms"]}

        fichas_info = [(f.id, puntaje) for f, puntaje in resultados]
        texto_fichas = "\n\n".join(f"[{f.titulo}]\n{f.contenido}" for f, _ in resultados)
        resultado = {"texto": f"{resultados[0][0].contenido} {pregunta_paso}", "fichas": fichas_info,
                     "encontro": True, "fuente": "ficha", "guardia": {"ok": True, "motivo": "sin modelo"},
                     "intentos": [], "ms": 0}

        if self.llm is None or not self.llm.enabled:
            return resultado

        prompt_sistema = PROMPT_TEORIA.format(pregunta_paso=pregunta_paso)
        messages = [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": f"Material de clase:\n{texto_fichas}\n\nPregunta del alumno: {pregunta}"},
        ]

        motivo = ""
        for temperatura in (TEMPERATURA_VOZ, TEMPERATURA_VOZ_REINTENTO):
            out, metricas = self.llm.chat(messages, id_slot=0, seed=42, temperature=temperatura,
                                          max_tokens=MAX_TOKENS_VOZ, timeout=TIMEOUT_VOZ)
            resultado["ms"] += metricas.get("ms", 0)
            if not out:
                motivo = "sin respuesta del modelo"
                resultado["intentos"].append({"texto": "", "ok": False, "motivo": motivo})
                continue
            texto_candidato = con_pregunta_del_paso(limpiar_salida(out), pregunta_paso)
            ok, motivo = guard(
                texto_candidato,
                pregunta_paso,
                ctx.get("forbidden", []),
                ctx.get("pending_eqs", []),
                ctx.get("permite_ecuaciones", False),
                ctx.get("var_names", []),
                ctx.get("known", set()),
                texto_fichas=texto_fichas,
            )
            resultado["intentos"].append({"texto": texto_candidato, "ok": ok, "motivo": "" if ok else motivo})
            if ok:
                resultado.update(texto=texto_candidato, fuente="modelo", guardia={"ok": True, "motivo": ""})
                return resultado

        resultado["guardia"] = {"ok": False, "motivo": motivo}
        return resultado
