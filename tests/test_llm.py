"""Guardia anti-filtraciones y comportamiento sin modelo."""
import json

import httpx
import pytest

from tutor import analysis as an
from tutor.circuit import parse_netlist
from tutor.llm import LLM, ajustar_veredicto, guard

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


def test_guard_no_cambia_el_veredicto_del_motor():
    pista = "Revisa el término de I1: ¿de qué elementos sale?"
    bien = "Correcto. ¿Qué elementos recorre la corriente I2?"
    assert not guard("Correcto. Revisa el término de I1: ¿de qué sale?", pista, [], [], False, [])[0]
    assert not guard("Muy bien, ¿de qué elementos sale el término de I1?", pista, [], [], False, [])[0]
    assert not guard("Está mal. ¿De qué elementos sale el término de I1?", pista, [], [], False, [])[0]
    assert guard("Así es. ¿Por qué elementos pasa I2?", bien, [], [], False, [])[0]
    assert not guard("¿Por qué elementos pasa la corriente I2?", bien, [], [], False, [])[0]   # quita el acierto
    polaridad = "Si sustituyes tus valores, ¿la fuente queda con la polaridad correcta?"
    assert guard("Sustituye tus valores: ¿la fuente queda con la polaridad adecuada?", polaridad, [], [], False, [])[0]


def test_guard_exige_la_misma_pregunta_los_mismos_datos_y_ninguna_ecuacion_nueva():
    base = "Correcto. Observa el circuito: ¿cuántas corrientes de malla necesitas definir?"
    assert guard("Correcto. ¿Cuántas corrientes de malla tienes que definir?", base, [], [], False, [])[0]
    assert not guard("Correcto. ¿Qué datos proporciona el circuito y qué variable necesitas?",
                     base, [], [], False, [])[0]
    assert not guard("Correcto. ¿Y luego?", base, [], [], False, [])[0]
    lazos = "Correcto. ¿Qué elementos recorre la corriente I2?"
    assert not guard("Correcto. ¿Qué elementos recorre esa corriente?", lazos, [], [], False, [])[0]
    malla2 = "Correcto. Plantea la ecuación de la malla 2 con LVK."
    assert not guard("Correcto. La de la malla 2 con LVK es 6*I2 - 2*I2 = 12.", malla2, [], [], False, ["I2"])[0]
    caso = ("Imagina que por esa rama I1 = 3 A va en un sentido e I2 = 1 A en el otro. "
            "¿La corriente resultante sería 4 A o 2 A?")
    assert guard("Piénsalo: si I1 = 3 A va en un sentido e I2 = 1 A en el otro, ¿resultan 4 A o 2 A?",
                 caso, [], [], False, ["I1", "I2"])[0]


def test_guard_no_deja_quitar_la_pista_ni_regalar_la_respuesta():
    # casos reales de un modelo de 0.8B que pasaban las reglas anteriores
    nodo = ("Un nodo es un punto donde se unen dos o más elementos. "
            "¿Cuántos puntos distintos ves, sin contar la referencia?")
    assert not guard("¿Cuántos puntos distintos ves, sin contar la referencia?", nodo, [], [], False, [])[0]
    assert guard("Piensa que un nodo es un punto donde se unen dos o más elementos. "
                 "¿Cuántos puntos distintos ves sin contar la referencia?", nodo, [], [], False, [])[0]
    vecino = ("Si el nodo vecino tuviera el mismo voltaje que tu nodo, "
              "¿cuánta corriente pasaría por la resistencia que los une?")
    assert not guard("¿Qué corriente pasaría por la resistencia que los une?", vecino, [], [], False, [])[0]
    restan = ("Muy bien: las corrientes se restan porque van en sentidos opuestos. Entonces, tomando el sentido "
              "de I1 como referencia, ¿cómo escribirías ahora la corriente de esa rama?")
    assert not guard("Muy bien: ¿Cómo se expresaría ahora la corriente de esa rama si la referencia es el sentido "
                     "de I1?", restan, [], [], False, [])[0]
    ley = "Revisa esa idea. ¿Qué ley o propiedad del circuito se aplica aquí?"
    assert not guard("Se aplica la Ley de Kirchhoff de nodos. ¿Qué ley o propiedad del circuito se aplica aquí?",
                     ley, [], [], False, [])[0]


def test_ajustar_veredicto_lo_deja_como_el_motor():
    pista = "Revisa el término de I1: ¿de qué elementos sale?"
    assert ajustar_veredicto("¡Correcto! Revisa el término de I1: ¿de qué sale?", pista) == \
        "Revisa el término de I1: ¿de qué sale?"
    bien = "Correcto. ¿Qué elementos recorre la corriente I2?"
    assert ajustar_veredicto("¿Por qué elementos pasa I2?", bien) == "Correcto. ¿Por qué elementos pasa I2?"
    assert ajustar_veredicto("Así es. ¿Por qué elementos pasa I2?", bien) == "Así es. ¿Por qué elementos pasa I2?"


def test_guard_rechaza_las_etiquetas_del_prompt():
    base = "¿Qué ley vas a aplicar?"
    assert not guard("¿Qué ley vas a aplicar? Ruta: motor Intención: respuesta", base, [], [], False, [])[0]
    assert not guard("Mensaje base: ¿Qué ley vas a aplicar?", base, [], [], False, [])[0]


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
