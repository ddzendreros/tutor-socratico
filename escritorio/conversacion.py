"""Módulo de conversación y adaptación de mensajes con modelos de lenguaje.

Clasifica la intención del alumno y redacta mensajes pedagógicos cálidos
manteniendo la fidelidad determinista al mensaje base del motor.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from escritorio.config import (MAX_TOKENS_ANOTACION, MAX_TOKENS_INTENCION, MAX_TOKENS_VOZ, RANURA_ANOTACION,
                               RANURA_INTENCION, RANURA_VOZ, TEMPERATURA_INTENCION, TEMPERATURA_VOZ,
                               TEMPERATURA_VOZ_REINTENTO, TIMEOUT_ANOTACION, TIMEOUT_INTENCION, TIMEOUT_VOZ)
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

SCHEMA_ANOTACION = {
    "name": "evaluacion_respuesta_abierta",
    "schema": {
        "type": "object",
        "properties": {
            "expresa_idea": {
                "type": "string",
                "enum": ["si", "parcial", "no"],
            },
            "claridad": {
                "type": "integer",
                "enum": [1, 2, 3],
            },
            "usa_terminos_tecnicos": {
                "type": "boolean",
            },
            "idea_erronea": {
                "type": "string",
                "maxLength": 100,
            },
            "tipo": {
                "type": "string",
                "enum": ["explicacion", "repeticion", "vago", "fuera_de_tema"],
            },
        },
        "required": ["expresa_idea", "claridad", "usa_terminos_tecnicos", "idea_erronea", "tipo"],
        "additionalProperties": False,
    },
}

# la rúbrica es sólo un dato para la investigación: nunca cambia lo que decide el motor
PROMPT_ANOTACION = (
    "Evalúas respuestas abiertas de alumnos de Circuitos Eléctricos para una investigación. "
    "No corriges ni le hablas al alumno. Recibes el tipo de respuesta, la pregunta del tutor, la idea "
    "esperada (referencia del profesor) y la respuesta del alumno. Devuelve:\n"
    "- expresa_idea: \"si\" si la respuesta contiene la idea esperada, aunque use otras palabras; "
    "\"parcial\" si contiene sólo una parte; \"no\" si no la contiene.\n"
    "- claridad: 1 confusa, 2 entendible, 3 clara y precisa.\n"
    "- usa_terminos_tecnicos: true si usa al menos un término de circuitos (corriente, voltaje, "
    "resistencia, potencia, fuente, malla, nodo, LVK, LCK, serie, paralelo…).\n"
    "- idea_erronea: si afirma algo falso sobre circuitos, descríbelo en una frase corta; si no, cadena "
    "vacía. Una respuesta incompleta o vaga no es una idea errónea.\n"
    "- tipo: \"explicacion\" si explica con sus palabras; \"repeticion\" si sólo repite la pregunta o los "
    "datos; \"vago\" si no dice nada concreto; \"fuera_de_tema\" si no tiene que ver con la pregunta.\n\n"
    "Ejemplo 1:\nPregunta: ¿Cómo podrías comprobar estos resultados?\n"
    "Idea esperada: balance de potencias (PE = PC), sustituir en las ecuaciones originales o simular.\n"
    "Respuesta: sumo las potencias de las fuentes y veo que dé lo mismo que las de las resistencias\n"
    '{"expresa_idea": "si", "claridad": 3, "usa_terminos_tecnicos": true, "idea_erronea": "", '
    '"tipo": "explicacion"}\n\n'
    "Ejemplo 2:\nPregunta: Antes de seguir: ¿por qué es así? Explícalo con tus palabras.\n"
    "Idea esperada: en la rama compartida circulan dos corrientes de malla en sentidos opuestos; la "
    "corriente neta es su diferencia.\n"
    "Respuesta: porque ahí se suman las corrientes\n"
    '{"expresa_idea": "no", "claridad": 2, "usa_terminos_tecnicos": true, '
    '"idea_erronea": "Cree que en la rama compartida las corrientes de malla se suman", "tipo": "explicacion"}'
)

PROMPTS_HASH = hashlib.sha256(
    "\n".join([PROMPT_VOZ, PROMPT_TEORIA, PROMPT_ANOTACION, json.dumps(mensajes_intencion(""), ensure_ascii=False),
               json.dumps(SCHEMA_INTENCION, sort_keys=True), json.dumps(SCHEMA_ANOTACION, sort_keys=True),
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
            id_slot=RANURA_INTENCION,
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
            out, metricas = self.llm.chat(messages, id_slot=RANURA_VOZ, seed=42, temperature=temperatura,
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

    def anotar(self, tipo: str, pregunta: str, idea_esperada: str, respuesta: str) -> dict | None:
        """Rúbrica de una respuesta abierta (justificación, concepto, verificación o metacognición).

        Usa su propia ranura para no borrar de la caché los prompts de la voz y la intención.
        Devuelve {rubrica, ms, tokens_prompt, tokens_salida}, o None si el modelo no respondió bien.
        """
        if self.llm is None or not self.llm.enabled:
            return None
        messages = [
            {"role": "system", "content": PROMPT_ANOTACION},
            {"role": "user", "content": (f"Tipo de respuesta: {tipo}\nPregunta: {pregunta}\n"
                                         f"Idea esperada: {idea_esperada}\nRespuesta: {respuesta}")},
        ]
        out, metricas = self.llm.chat(messages, json_schema=SCHEMA_ANOTACION, id_slot=RANURA_ANOTACION, seed=0,
                                      temperature=0.0, max_tokens=MAX_TOKENS_ANOTACION, timeout=TIMEOUT_ANOTACION)
        try:
            data = json.loads(out or "")
        except ValueError:
            return None
        valores = SCHEMA_ANOTACION["schema"]["properties"]
        if not (isinstance(data, dict)
                and data.get("expresa_idea") in valores["expresa_idea"]["enum"]
                and data.get("claridad") in valores["claridad"]["enum"]
                and isinstance(data.get("usa_terminos_tecnicos"), bool)
                and isinstance(data.get("idea_erronea"), str)
                and data.get("tipo") in valores["tipo"]["enum"]):
            return None
        rubrica = {k: data[k] for k in valores}
        rubrica["idea_erronea"] = rubrica["idea_erronea"].strip()[:100]
        return {"rubrica": rubrica, "ms": metricas.get("ms", 0),
                "tokens_prompt": metricas.get("tokens_prompt", 0), "tokens_salida": metricas.get("tokens_salida", 0)}

    def precalentar(self) -> None:
        """Deja en la caché de cada ranura el prefijo de su prompt. Sin esto, con pocos núcleos la
        primera clasificación de intención tarda más que su tiempo de espera y se pierde."""
        if self.llm is None or not self.llm.enabled:
            return
        voz = [{"role": "system", "content": PROMPT_VOZ},
               {"role": "user", "content": "Alumno: hola\nMensaje base: hola\nTutor:"}]
        anotacion = [{"role": "system", "content": PROMPT_ANOTACION}, {"role": "user", "content": "Respuesta: hola"}]
        for mensajes, ranura in ((mensajes_intencion("hola"), RANURA_INTENCION), (voz, RANURA_VOZ),
                                 (anotacion, RANURA_ANOTACION)):
            self.llm.chat(mensajes, id_slot=ranura, max_tokens=1, timeout=300.0)

    def responder_teoria(self, pregunta: str, pregunta_paso: str, contexto: dict | None = None) -> dict[str, Any]:
        """Responde la duda usando las 2 mejores fichas pedagógicas de clase.

        Pasa por guard() con contexto_guardia(sesion).
        Si no hay ficha sobre el umbral, usa la plantilla predeterminada y redactar().
        Devuelve {texto, base, fichas: [(id, puntaje)], encontro, fuente: modelo|ficha|plantilla,
        guardia: {ok, motivo}, reintentos, intentos, ms, tokens…}; `fuente` "ficha" es el texto de la
        ficha tal cual, y `base` lo que se muestra si el modelo no responde.
        """
        from escritorio.conocimiento import buscar, UMBRAL_TEORIA

        ctx = contexto or {}
        resultados = buscar(pregunta, k=2, umbral=UMBRAL_TEORIA)

        if not resultados:
            base = (f"Esa duda no viene en el material de clase que tengo; anótala para "
                    f"preguntarle a tu profesor. Mientras, {pregunta_paso}")
            return {**self.redactar(base, pregunta, ctx), "base": base, "fichas": [], "encontro": False}

        fichas_info = [(f.id, puntaje) for f, puntaje in resultados]
        texto_fichas = "\n\n".join(f"[{f.titulo}]\n{f.contenido}" for f, _ in resultados)
        base = f"{resultados[0][0].contenido} {pregunta_paso}"
        resultado = {"texto": base, "base": base, "fichas": fichas_info, "encontro": True, "fuente": "ficha",
                     "guardia": {"ok": True, "motivo": "sin modelo"}, "reintentos": 0, "intentos": [], "ms": 0,
                     "tokens_prompt": 0, "tokens_salida": 0, "tokens_cache": 0}

        if self.llm is None or not self.llm.enabled:
            return resultado

        prompt_sistema = PROMPT_TEORIA.format(pregunta_paso=pregunta_paso)
        messages = [
            {"role": "system", "content": prompt_sistema},
            {"role": "user", "content": f"Material de clase:\n{texto_fichas}\n\nPregunta del alumno: {pregunta}"},
        ]

        motivo = ""
        for n, temperatura in enumerate((TEMPERATURA_VOZ, TEMPERATURA_VOZ_REINTENTO)):
            out, metricas = self.llm.chat(messages, id_slot=RANURA_VOZ, seed=42, temperature=temperatura,
                                          max_tokens=MAX_TOKENS_VOZ, timeout=TIMEOUT_VOZ)
            resultado["ms"] += metricas.get("ms", 0)
            for clave in ("tokens_prompt", "tokens_salida", "tokens_cache"):
                resultado[clave] = metricas.get(clave, 0)
            resultado["reintentos"] = n
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
