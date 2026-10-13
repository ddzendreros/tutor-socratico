"""Cliente local de modelos de lenguaje, compatible con la API OpenAI.

El servidor de inferencia corre en 127.0.0.1 (llama-server).
Si no hay servidor, todo sigue funcionando con las plantillas del motor.
"""
from __future__ import annotations

import re
import time

import httpx

from .mathparse import ParseError, parse_linear_equation, parse_numbers
from .diagnose import close, proportional, _vec


class LLM:
    def __init__(self, base_url: str = "", timeout: float = 25.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(timeout=timeout, transport=transport)
        self._llave: str = ""

    def configurar(self, base_url: str, llave: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self._llave = llave

    @property
    def enabled(self) -> bool:
        return bool(self.base_url)

    def chat(self, messages: list[dict], *, json_schema: dict | None = None,
             id_slot: int = 0, seed: int = 0, temperature: float = 0.4,
             max_tokens: int = 400) -> tuple[str | None, dict]:
        """Llama a /v1/chat/completions y devuelve (texto, métricas).

        Métricas: ms, tokens_prompt, tokens_salida, tokens_cache.
        """
        if not self.enabled:
            return None, {}
        body: dict = {
            "messages": messages,
            "id_slot": id_slot,
            "cache_prompt": True,
            "seed": seed,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": json_schema,
            }
        headers = {}
        if self._llave:
            headers["Authorization"] = f"Bearer {self._llave}"
        inicio = time.perf_counter()
        try:
            r = self.client.post(
                f"{self.base_url}/v1/chat/completions",
                json=body,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()
            texto = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
            return None, {}
        fin = time.perf_counter()
        if not isinstance(texto, str):
            return None, {}
        texto = _strip_think(texto).strip()
        if not texto:
            return None, {}
        uso = data.get("usage") or {}
        timings = data.get("timings") or {}
        metricas = {
            "ms": round((fin - inicio) * 1000),
            "tokens_prompt": uso.get("prompt_tokens", 0),
            "tokens_salida": uso.get("completion_tokens", 0),
            "tokens_cache": timings.get("cache_n", 0),
        }
        if "predicted_per_second" in timings:
            metricas["predicted_per_second"] = timings["predicted_per_second"]
        if "prompt_n" in timings:
            metricas["prompt_n"] = timings["prompt_n"]
        if "prompt_ms" in timings:
            metricas["prompt_ms"] = timings["prompt_ms"]
        if "predicted_ms" in timings:
            metricas["predicted_ms"] = timings["predicted_ms"]
        return texto, metricas


# ================================================================== guardia
# el veredicto lo decide el motor: la reescritura no puede agregarlo, cambiarlo ni quitarlo
# "correcto", "bien", "exacto" o "perfecto" sólo son veredicto al abrir la frase o con "es/está";
# como adjetivo ("la polaridad correcta") no lo son. El motor usa "Bien." para un acierto parcial
_ACIERTO_RE = re.compile(r"(^|[.!¡¿,;:]\s*)(correct[oa]s?|bien|exact[oa](mente)?|perfect[oa]|excelente)\b"
                         r"|\b(muy bien|bien hecho|así es|buen trabajo|acertaste|tienes razón|eso es"
                         r"|(es|est[aá]n?)\s+correct[oa]s?)\b", re.I)
_ERROR_RE = re.compile(r"\b(incorrect[oa]s?|te equivocaste|est[aá] mal|no es correct[oa]|equivocad[oa]s?"
                       r"|err[oó]ne[oa]s?)\b", re.I)
_ETIQUETA_PROMPT_RE = re.compile(r"\b(ruta|intenci[oó]n|mensaje base|paso actual|alumno"
                                 r"|intercambios recientes)\s*:", re.I)
# nombres de elementos, variables y leyes que la reescritura debe conservar
_IDENTIFICADOR_RE = re.compile(r"\b(?:[IVRP][A-Z]?\d+|[IV][XYZ]|R_?T\d+|Req|LVK|LCK|FMR|CMR|PE|PC)\b")
_IGUALDAD_RE = re.compile(r"([\w.]+)\s*\)?\s*=\s*\(?\s*(-?[\w.]+)")
_VACIAS = {"puedes", "tienes", "tiene", "estas", "vamos", "ahora", "entonces", "sobre", "porque",
           "cuando", "donde", "quieres", "hacer", "seria", "podrias", "crees", "para", "pero", "como",
           "esta", "este", "esto", "esos", "esas", "cual", "cuál"}
# conceptos que una reescritura no puede introducir: si el mensaje base no los nombra, nombrarlos
# suele ser dar la respuesta ("Se aplica la ley de Kirchhoff de nodos")
_CONCEPTO_RE = re.compile(r"\b(ohm|kirchhoff|lvk|lck|nodos?|mallas?|supermallas?|supernodos?|fantasmas?|lazos?"
                          r"|ramas?|serie|paralelo|referencia|tierra|voltajes?|tensi[oó]n|corrientes?|resistencias?"
                          r"|potencias?|equivalentes?|fuentes?|suman?|restan?|divide|misma|igual)\b", re.I)


def _raices(texto: str, minimo: int = 5) -> set[str]:
    """Palabras de contenido reducidas a sus primeras cinco letras, sin acentos, más los identificadores."""
    plano = texto.casefold().translate(str.maketrans("áéíóúü", "aeiouu"))
    raices = {p[:5] for p in re.findall(rf"[a-zñ]{{{minimo},}}", plano) if p not in _VACIAS}
    return raices | {i.casefold() for i in _IDENTIFICADOR_RE.findall(texto)}


def _conserva_el_contenido(base: str, out: str) -> bool:
    """La reescritura conserva al menos la mitad de las palabras de contenido del mensaje base, y
    el 60 % de cada frase que no es pregunta y tiene peso (la explicación de una pista)."""
    sin_veredicto = lambda t: _ERROR_RE.sub(" ", _ACIERTO_RE.sub(" ", t))  # noqa: E731
    base, out = sin_veredicto(base), sin_veredicto(out)
    quedan, total = _raices(out, 4), _raices(base, 4)
    if total and len(total & quedan) < 0.5 * len(total):
        return False
    for frase in re.findall(r"[^.:;?!]+[.:;!]", base):
        r = _raices(frase, 4)
        if len(r) >= 3 and len(r & quedan) < 0.6 * len(r):
            return False
    return True


def _ultima_pregunta(texto: str) -> str:
    preguntas = re.findall(r"¿?[^.?!¿]*\?", texto)
    return preguntas[-1] if preguntas else ""


_APERTURA_RE = re.compile(r"^\s*[¡¿]?([^.!?,:;]{0,30})[.!?,:;]\s*")


def ajustar_veredicto(texto: str, base: str) -> str:
    """Deja en la reescritura el veredicto del motor: quita una apertura con veredicto que el
    mensaje base no tiene ("¡Correcto!" ante una pista) o repone la del mensaje base si falta."""
    for patron in (_ACIERTO_RE, _ERROR_RE):
        en_base, en_texto = bool(patron.search(base)), bool(patron.search(texto))
        if en_texto and not en_base:
            m = _APERTURA_RE.match(texto)
            if m and patron.search(m.group(0)):
                resto = texto[m.end():]
                texto = resto[:1].upper() + resto[1:]
        elif en_base and not en_texto:
            m = _APERTURA_RE.match(base)
            if m and patron.search(m.group(0)):
                texto = f"{m.group(0).strip()} {texto}"
    return texto


def guard(out: str, base: str, forbidden: list[float], pending_eqs: list,
          allow_equations: bool, var_names: list[str],
          known: set[float] | None = None,
          texto_fichas: str = "") -> tuple[bool, str]:
    """Rechaza la reescritura si filtra resultados, ecuaciones pendientes o
    si cambia la naturaleza del mensaje (p.ej. quita la pregunta o cambia el veredicto)."""
    limite = max(3 * len(base), len(texto_fichas), 600) if texto_fichas else max(3 * len(base), 400)
    if len(out) > limite:
        return False, "demasiado largo"
    if "?" in base and "?" not in out:
        return False, "perdió la pregunta"
    for patron in (_ACIERTO_RE, _ERROR_RE):
        if bool(patron.search(out)) != bool(patron.search(base)):
            return False, "cambia el veredicto"
    if _ETIQUETA_PROMPT_RE.search(out):
        return False, "copia el formato del prompt"
    if not set(_IDENTIFICADOR_RE.findall(base)) <= set(_IDENTIFICADOR_RE.findall(out)):
        return False, "pierde un dato"
    if "?" in base:
        # la pregunta final debe salir del mensaje base, no ser otra
        propias = _raices(_ultima_pregunta(out))
        if (propias or _raices(_ultima_pregunta(base))) and len(propias & _raices(base)) < 0.5 * len(propias or {1}):
            return False, "cambia la pregunta"
    base_eqs = base + (" " + texto_fichas if texto_fichas else "")
    if not allow_equations and not set(_IGUALDAD_RE.findall(out)) <= set(_IGUALDAD_RE.findall(base_eqs)):
        return False, "agrega una ecuación"
    conceptos = lambda t: {c.casefold().rstrip("s") for c in _CONCEPTO_RE.findall(t)}  # noqa: E731
    base_conceptos = base + (" " + texto_fichas if texto_fichas else "")
    if not conceptos(out) <= conceptos(base_conceptos):
        return False, "agrega contenido técnico"
    if not _conserva_el_contenido(base, out):
        return False, "omite parte del mensaje"
    allowed = {round(n.value, 4) for n in parse_numbers(base + (" " + texto_fichas if texto_fichas else ""))} | {0, 1, 2, 3}
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
