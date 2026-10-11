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
        return texto, metricas


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
