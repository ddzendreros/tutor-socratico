"""El LLM es opcional: cadena de respaldo, guardia anti-filtraciones y extracción."""
import json

import httpx

from tutor import analysis as an
from tutor.circuit import parse_netlist
from tutor.llm import LLM, guard

DOCX = "V1 a 0 12\nR1 a b 4\nR2 b 0 2\nR3 b c 6\nV2 0 c 6"


def _transport(handler_by_host):
    def handler(request: httpx.Request):
        return handler_by_host[request.url.host](request)
    return httpx.MockTransport(handler)


def test_fallback_when_first_provider_is_rate_limited(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "x")
    calls = []

    def ollama(req):
        calls.append("ollama")
        return httpx.Response(429, headers={"retry-after": "30"})

    def groq(req):
        calls.append("groq")
        return httpx.Response(200, json={"choices": [{"message": {"content": "hola ¿qué ley aplicas?"}}]})

    llm = LLM("ollama:qwen3:8b,groq:openai/gpt-oss-20b",
              transport=_transport({"localhost": ollama, "api.groq.com": groq}))
    assert llm.chat("s", "u") == "hola ¿qué ley aplicas?"
    assert calls == ["ollama", "groq"]
    llm.chat("s", "u")                      # ollama queda en enfriamiento
    assert calls == ["ollama", "groq", "groq"]


def test_no_provider_means_templates():
    llm = LLM("")
    assert not llm.enabled
    assert llm.rephrase("¿Qué ley usarías?", "no sé", [], [], False, []) == "¿Qué ley usarías?"


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


def test_extraction_is_validated():
    payload = {"elements": [["V1", "a", "0", "12"], ["R1", "a", "b", "4"], ["R2", "b", "0", "8"]],
               "targets": [{"quantity": "I", "element": "R2"}], "method": "serie_paralelo"}
    ok = _transport({"localhost": lambda r: httpx.Response(
        200, json={"message": {"content": json.dumps(payload)}})})
    c, method, _ = LLM("ollama:qwen3:8b", transport=ok).extract_circuit("…")
    assert c is not None and method == "serie_paralelo" and len(c.elements) == 3

    bad = _transport({"localhost": lambda r: httpx.Response(
        200, json={"message": {"content": '{"elements": [["R1","a","b","4"]]}'}})})
    c, _, err = LLM("ollama:qwen3:8b", transport=bad).extract_circuit("…")
    assert c is None and "tierra" in err or "referencia" in err
