"""Cliente LLM con cadena de respaldo, sólo para tareas de lenguaje.

Tareas:
  1. extract_circuit(): enunciado en texto -> netlist JSON (el alumno lo confirma).
  2. rephrase(): reescribe con naturalidad el mensaje que YA decidió el motor.
  3. judge_concept(): juzga respuestas conceptuales abiertas (opcional).

Si ningún proveedor responde, todo sigue funcionando con las plantillas.
Proveedores configurables con la variable LLM_CHAIN, en orden de preferencia:
  LLM_CHAIN="ollama:qwen3:8b,groq:openai/gpt-oss-20b,groq:qwen/qwen3.8-27b,gemini:gemini-2.5-flash"
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

import httpx

from .circuit import Circuit, CircuitError, Target, parse_rows
from .mathparse import ParseError, parse_linear_equation, parse_numbers
from .diagnose import close, proportional, _vec

ENDPOINTS = {
    "ollama": (os.getenv("OLLAMA_URL", "http://localhost:11434"), None),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai_compat": (os.getenv("OPENAI_COMPAT_URL", ""), "OPENAI_COMPAT_KEY"),
}
COOLDOWN_S = 60


@dataclass
class Provider:
    kind: str
    model: str
    cooldown_until: float = 0.0

    @property
    def available(self) -> bool:
        if time.time() < self.cooldown_until:
            return False
        key_env = ENDPOINTS[self.kind][1]
        return key_env is None or bool(os.getenv(key_env))


class LLM:
    def __init__(self, chain: str | None = None, timeout: float = 25.0,
                 transport: httpx.BaseTransport | None = None):
        chain = chain if chain is not None else os.getenv("LLM_CHAIN", "ollama:qwen3:8b")
        self.providers = []
        for item in filter(None, (x.strip() for x in chain.split(","))):
            kind, _, model = item.partition(":")
            if kind in ENDPOINTS and model:
                self.providers.append(Provider(kind, model))
        self.client = httpx.Client(timeout=timeout, transport=transport)
        self.last_provider: str | None = None

    @property
    def enabled(self) -> bool:
        return any(p.available for p in self.providers)

    # ------------------------------------------------------------ transporte
    def chat(self, system: str, user: str, json_mode: bool = False, max_tokens: int = 400) -> str | None:
        for p in self.providers:
            if not p.available:
                continue
            try:
                out = self._call(p, system, user, json_mode, max_tokens)
            except (httpx.HTTPError, KeyError, ValueError, IndexError):
                p.cooldown_until = time.time() + COOLDOWN_S
                continue
            if out:
                self.last_provider = f"{p.kind}:{p.model}"
                return _strip_think(out)
        return None

    def _call(self, p: Provider, system: str, user: str, json_mode: bool, max_tokens: int) -> str:
        base, key_env = ENDPOINTS[p.kind]
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if p.kind == "ollama":
            body = {"model": p.model, "messages": msgs, "stream": False, "think": False,
                    "options": {"temperature": 0.3, "num_predict": max_tokens}}
            if json_mode:
                body["format"] = "json"
            r = self.client.post(f"{base}/api/chat", json=body)
            self._raise(p, r)
            return r.json()["message"]["content"]
        body = {"model": p.model, "messages": msgs, "temperature": 0.3, "max_tokens": max_tokens}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = self.client.post(f"{base}/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {os.getenv(key_env, '')}"})
        self._raise(p, r)
        return r.json()["choices"][0]["message"]["content"]

    @staticmethod
    def _raise(p: Provider, r: httpx.Response) -> None:
        if r.status_code == 429:
            retry = float(r.headers.get("retry-after", COOLDOWN_S) or COOLDOWN_S)
            p.cooldown_until = time.time() + min(retry, 600)
        r.raise_for_status()

    # --------------------------------------------------------- 1. extracción
    def extract_circuit(self, statement: str) -> tuple[Circuit | None, str, str]:
        """Devuelve (circuito, método sugerido, mensaje de error)."""
        raw = self.chat(EXTRACT_SYSTEM, statement, json_mode=True, max_tokens=900)
        if raw is None:
            return None, "", "No hay modelo de lenguaje disponible: captura el circuito en la tabla."
        try:
            data = json.loads(_json_block(raw))
            c = parse_rows(data["elements"])
            c.targets = []
            for t in data.get("targets", []):
                q = str(t.get("quantity", "")).strip()
                if q in ("I", "V", "P", "Req"):
                    nodes = tuple(t["nodes"]) if t.get("nodes") else None
                    c.targets.append(Target(q, t.get("element"), nodes))
            c.statement = statement
            c.validate()
            method = data.get("method", "")
            return c, method if method in ("ohm", "serie_paralelo", "mallas", "nodos") else "", \
                str(data.get("notes", ""))
        except (json.JSONDecodeError, KeyError, TypeError, CircuitError) as exc:
            return None, "", f"No pude interpretar el circuito ({exc}). Revísalo o captúralo en la tabla."

    # ----------------------------------------------------------- 2. redacción
    def rephrase(self, base_msg: str, student_msg: str, forbidden: list[float],
                 pending_eqs: list, allow_equations: bool, var_names: list[str],
                 known: set[float] | None = None) -> str:
        if not self.enabled:
            return base_msg
        user = (f"Mensaje del estudiante: «{student_msg}»\n\n"
                f"Mensaje del tutor que debes reescribir (mismo contenido, misma pregunta final):\n«{base_msg}»")
        out = self.chat(REPHRASE_SYSTEM, user, max_tokens=220)
        if not out:
            return base_msg
        out = out.strip().strip("«»\"")
        ok, _why = guard(out, base_msg, forbidden, pending_eqs, allow_equations, var_names, known)
        return out if ok else base_msg

    # --------------------------------------------------- 3. juicio conceptual
    def judge_concept(self, question: str, expected_idea: str, answer: str) -> bool | None:
        raw = self.chat(JUDGE_SYSTEM, json.dumps({"pregunta": question, "idea_esperada": expected_idea,
                                                  "respuesta": answer}, ensure_ascii=False),
                        json_mode=True, max_tokens=80)
        if not raw:
            return None
        try:
            return bool(json.loads(_json_block(raw))["correcto"])
        except (json.JSONDecodeError, KeyError):
            return None


# ================================================================== guardia
def guard(out: str, base: str, forbidden: list[float], pending_eqs: list,
          allow_equations: bool, var_names: list[str],
          known: set[float] | None = None) -> tuple[bool, str]:
    """Rechaza la reescritura si filtra resultados, ecuaciones pendientes o
    si cambia la naturaleza del mensaje (p.ej. quita la pregunta)."""
    if len(out) > max(3 * len(base), 400):
        return False, "demasiado largo"
    if "?" in base and "?" not in out:
        return False, "perdió la pregunta"
    allowed = {round(n.value, 4) for n in parse_numbers(base)} | {0, 1, 2, 3}
    allowed |= {round(float(k), 4) for k in (known or set())}
    for n in parse_numbers(out):
        if any(close(abs(n.value), abs(f), 0.01) for f in forbidden):
            return False, "filtra un resultado"
        if round(n.value, 4) not in allowed:
            return False, "introduce un número nuevo"
    if not allow_equations and var_names:
        for piece in re.split(r"[\n;,]", out):
            if "=" not in piece:
                continue
            try:
                pe = parse_linear_equation(piece, var_names)
            except ParseError:
                continue
            order = list(pending_eqs[0].coefs.keys()) if pending_eqs else []
            for q in pending_eqs:
                s = _vec(pe.coefs, pe.rhs, order)
                r = _vec({k: float(v) for k, v in q.coefs.items()}, float(q.rhs), order)
                if proportional(s, r)[0]:
                    return False, "filtra una ecuación"
    return True, ""


def _strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def _json_block(text: str) -> str:
    m = re.search(r"\{.*\}", text, re.S)
    return m.group(0) if m else text


# ================================================================= prompts
EXTRACT_SYSTEM = """Eres un extractor de circuitos eléctricos de CD. Convierte el enunciado en JSON.
NO resuelvas el problema. NO inventes valores: si falta un dato, ponlo en "notes".

Formato de salida (sólo JSON):
{"elements": [["R1","a","b","4"], ["V1","a","0","12"], ["I1","0","b","2"]],
 "targets": [{"quantity":"I","element":"R2"}, {"quantity":"Req","element":"V1"}],
 "method": "mallas" | "nodos" | "serie_paralelo" | "ohm",
 "notes": ""}

Reglas:
- Cada elemento: [nombre, nodo1, nodo2, valor]. Usa los nombres del enunciado si existen (R1, R2, V1...).
- Resistencias R (ohms), fuentes de voltaje V (el PRIMER nodo es la terminal +), fuentes de corriente I
  (la flecha va del primer nodo al segundo).
- El nodo de referencia/tierra se llama "0". Si el enunciado no la indica, usa como "0" la terminal
  negativa de la fuente principal.
- Valores numéricos en unidades base o con prefijo: "4", "2.2k", "500m".
- targets: lo que pide el problema. quantity: "I" corriente, "V" voltaje, "P" potencia, "Req".
  Para corrientes de malla pedidas (I1, I2...) deja targets vacío y method "mallas".
- method: el que pida el enunciado; si no dice, "serie_paralelo" para una sola fuente con
  resistencias en serie/paralelo, "mallas" para varias mallas.

Ejemplo. Enunciado: "Malla 1: fuente de 12 V, resistencia de 4 Ω y resistencia compartida de 2 Ω.
Malla 2: fuente de 6 V, resistencia de 6 Ω y la compartida de 2 Ω. Corrientes en sentido horario.
Determina I1 e I2."
Salida: {"elements": [["V1","a","0","12"],["R1","a","b","4"],["R2","b","0","2"],["R3","b","c","6"],
["V2","0","c","6"]], "targets": [], "method": "mallas",
"notes": "Supuse que ambas fuentes empujan corriente en sentido horario; confirma la polaridad de V2."}"""

REPHRASE_SYSTEM = """Eres un tutor socrático de Circuitos Eléctricos que habla español de México, cálido y breve.
Reescribe el mensaje del tutor para que suene natural y conecte con lo que dijo el estudiante.
REGLAS ESTRICTAS:
- Conserva exactamente el contenido pedagógico y termina con la MISMA pregunta (puedes reformularla).
- No agregues pistas, fórmulas, números ni pasos que no estén en el mensaje original.
- Nunca des resultados ni la solución. Nunca digas que algo es correcto si el mensaje no lo dice.
- Máximo 3 oraciones. Responde sólo con el mensaje, sin comillas."""

JUDGE_SYSTEM = """Evalúas si la respuesta de un estudiante expresa la idea esperada (aunque use otras
palabras). Responde sólo JSON: {"correcto": true|false}."""
