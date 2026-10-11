"""Guardia anti-filtraciones y comportamiento sin modelo."""
import json

import httpx
import pytest

from tutor import analysis as an
from tutor.circuit import parse_netlist
from tutor.llm import LLM, guard

DOCX = "V1 a 0 12\nR1 a b 4\nR2 b 0 2\nR3 b c 6\nV2 0 c 6"


def test_sin_modelo_devuelve_none():
    llm = LLM()
    assert not llm.enabled
    texto, metricas = llm.chat([{"role": "user", "content": "hola"}])
    assert texto is None
    assert metricas == {}


def test_guard_blocks_leaks():
    c = parse_netlist(DOCX)
    refs = an.mesh_system(c, an.default_meshes(c))
    base = "Revisa el término de I1: ¿de qué elementos sale?"
    forb = [27 / 11, 15 / 11]
    assert guard("Casi. ¿De dónde sale el término de I1?", base, forb, refs, False, ["I1", "I2"])[0]
    assert not guard("Ojo, I1 = 2.45 A. ¿De dónde sale?", base, forb, refs, False, ["I1", "I2"])[0]
    assert not guard("La ecuación es 6I1 - 2I2 = 12. ¿Ves?", base, forb, refs, False, ["I1", "I2"])[0]
    assert not guard("Revisa el término de I1.", base, forb, refs, False, ["I1", "I2"])[0]  # sin pregunta
    assert not guard("Suma 7 ohms. ¿Qué obtienes?", base, forb, refs, False, ["I1", "I2"])[0]


# ------------------------------------------------------------------ chat()

def test_chat_envia_cuerpo_y_llave():
    vistas = []

    def handler(request):
        vistas.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "<think>x</think>Hola"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            "timings": {"cache_n": 7},
        })

    llm = LLM(transport=httpx.MockTransport(handler))
    llm.configurar("http://127.0.0.1:9", "k")
    texto, m = llm.chat(
        [{"role": "user", "content": "hola"}],
        json_schema={"name": "n", "schema": {}},
        id_slot=1, seed=3, temperature=0.0, max_tokens=50,
    )
    peticion = vistas[0]
    assert peticion.url.path == "/v1/chat/completions"
    assert peticion.headers["authorization"] == "Bearer k"
    cuerpo = json.loads(peticion.content)
    assert cuerpo["id_slot"] == 1 and cuerpo["cache_prompt"] is True and cuerpo["seed"] == 3
    assert cuerpo["temperature"] == 0.0 and cuerpo["max_tokens"] == 50
    assert cuerpo["response_format"] == {"type": "json_schema",
                                         "json_schema": {"name": "n", "schema": {}}}
    assert texto == "Hola"
    assert m["tokens_prompt"] == 10
    assert m["tokens_salida"] == 2
    assert m["tokens_cache"] == 7
    assert isinstance(m["ms"], int) and m["ms"] >= 0


@pytest.mark.parametrize("respuesta", [
    httpx.Response(500),
    httpx.Response(200, text="<html>"),
    httpx.Response(200, json={"error": {"message": "x"}}),
    httpx.Response(200, json={"choices": [{"message": {"content": None}}]}),
    httpx.Response(200, json={"choices": [{"message": {"content": ""}}]}),
], ids=["http_500", "html", "sin_choices", "content_null", "content_vacio"])
def test_chat_respuesta_defectuosa(respuesta):
    llm = LLM(transport=httpx.MockTransport(lambda request: respuesta))
    llm.configurar("http://127.0.0.1:9", "k")
    texto, m = llm.chat([{"role": "user", "content": "hola"}])
    assert texto is None
    assert m == {}
