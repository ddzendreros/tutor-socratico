"""Pruebas del módulo de conocimiento y respuestas a dudas teóricas."""
from __future__ import annotations

import csv
from pathlib import Path

import httpx
import pytest

from escritorio.conocimiento import (
    BuscadorBM25,
    Ficha,
    UMBRAL_TEORIA,
    buscar,
    cargar_fichas,
    normalizar,
)
from escritorio.conversacion import Conversador
from tutor.llm import LLM, guard


def test_normalizar_bm25():
    tokens = normalizar("¿Cuáles son las leyes fundamentales y los voltajes en los nodos?")
    assert "cuales" not in tokens
    assert "son" not in tokens
    assert "las" not in tokens
    assert "en" not in tokens
    assert "los" not in tokens
    # Verificación de plurales reducidos y acentos eliminados
    assert "ley" in tokens
    assert "fundamental" in tokens
    assert "voltaje" in tokens
    assert "nodo" in tokens


def test_cargar_fichas_conceptos_minimos():
    fichas = cargar_fichas()
    assert len(fichas) >= 30

    ids = {f.id for f in fichas}
    conceptos_requeridos = {
        "ley_de_ohm",
        "potencia_electrica",
        "balance_potencia",
        "resistencias_serie",
        "resistencias_paralelo",
        "reduccion_resistencias",
        "resistencia_en_corto",
        "lvk",
        "lck",
        "malla",
        "malla_real",
        "malla_fantasma",
        "supermalla",
        "supermalla_ecuacion",
        "ecuacion_real",
        "rama_compartida_corriente_neta",
        "sentido_corriente_fmr_cmr",
        "nodo",
        "nodo_real",
        "nodo_fantasma",
        "supernodo",
        "supernodo_ecuacion",
        "nodo_referencia",
        "tabla_vip",
        "divisor_voltaje",
        "divisor_corriente",
        "superposicion",
        "desactivar_fuentes_voltaje",
        "desactivar_fuentes_corriente",
        "teorema_thevenin",
        "teorema_norton",
        "resistencia_rab",
        "verificacion_simulador",
        "conversion_delta_estrella",
    }
    assert conceptos_requeridos <= ids

    for f in fichas:
        assert f.id
        assert f.titulo
        assert f.claves
        assert f.fuente
        assert f.contenido
        assert f.tema


DATOS = Path(__file__).resolve().parent.parent / "tools" / "datos_evaluacion"


def _evaluar(nombre):
    """Top-1 y top-2 con el umbral, como se usa en la aplicación; concepto vacío = no viene en el material."""
    with open(DATOS / nombre, encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    buscador = BuscadorBM25(cargar_fichas())
    con_ficha = [f for f in filas if f["concepto"]]
    top1 = top2 = 0
    for fila in con_ficha:
        ids = [ficha.id for ficha, _ in buscador.buscar(fila["pregunta"], k=2, umbral=UMBRAL_TEORIA)]
        top1 += bool(ids) and ids[0] == fila["concepto"]
        top2 += fila["concepto"] in ids
    respondidas_sin_material = [f["pregunta"] for f in filas if not f["concepto"]
                                and buscador.buscar(f["pregunta"], k=2, umbral=UMBRAL_TEORIA)]
    return len(filas), top1 / len(con_ficha), top2 / len(con_ficha), respondidas_sin_material


@pytest.mark.parametrize("nombre", ["teoria.csv", "teoria_prueba.csv"])
def test_recuperacion_cumple_las_metas(nombre):
    """teoria.csv sirvió para ajustar el umbral; teoria_prueba.csv son preguntas nuevas."""
    n, top1, top2, sin_material = _evaluar(nombre)
    assert n >= 30
    # Metas del plan: top-1 >= 80 % y top-2 >= 90 %
    assert top1 >= 0.80, f"Top-1 fue {top1:.2%}, se esperaba >= 80 %"
    assert top2 >= 0.90, f"Top-2 fue {top2:.2%}, se esperaba >= 90 %"
    assert sin_material == [], "dudas que no vienen en el material recibieron una ficha"


def test_coincidir_solo_en_el_texto_no_basta_para_responder():
    # "funciona" aparece en el texto de una ficha, pero ninguna trata de transistores
    assert buscar("¿cómo funciona un transistor?", k=2, umbral=UMBRAL_TEORIA) == []
    assert buscar("¿qué es una fuente dependiente?", k=2, umbral=UMBRAL_TEORIA) == []


def test_umbral_teoria_filtra_fuera_de_tema():
    preguntas_ajenas = [
        "¿te gusta el fútbol?",
        "¿cuál es la capital de Francia?",
        "cuéntame un chiste",
        "¿qué hora tienes?",
        "hola",
        "buenos días",
    ]
    for pregunta in preguntas_ajenas:
        res = buscar(pregunta, k=2, umbral=UMBRAL_TEORIA)
        assert res == [], f"La pregunta '{pregunta}' no debió superar el umbral"


def test_responder_teoria_sin_ficha_usa_plantilla():
    conv = Conversador(llm=None)
    pregunta = "¿cuál es la capital de Francia?"
    paso = "¿Qué elementos recorre la corriente I1?"

    resultado = conv.responder_teoria(pregunta, paso, contexto={})
    assert resultado["encontro"] is False
    assert resultado["fichas"] == []
    assert "Esa duda no viene en el material de clase que tengo" in resultado["texto"]
    assert paso in resultado["texto"]


def test_responder_teoria_sin_modelo_usa_fallback_de_fichas():
    conv = Conversador(llm=None)
    pregunta = "¿Qué dice la ley de Ohm?"
    paso = "¿Qué elementos recorre la corriente I1?"

    resultado = conv.responder_teoria(pregunta, paso, contexto={})
    assert resultado["encontro"] is True
    assert len(resultado["fichas"]) > 0
    assert resultado["fichas"][0][0] == "ley_de_ohm"
    assert "ley de Ohm" in resultado["texto"]
    assert paso in resultado["texto"]


def test_responder_teoria_con_modelo_exitoso():
    respuesta_modelo = (
        "De acuerdo con el material de clase, la ley de Ohm relaciona el voltaje, "
        "la corriente y la resistencia mediante V = I · R. Nos indica cómo varía la "
        "tensión al cambiar la corriente. ¿Qué elementos recorre la corriente I1?"
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": respuesta_modelo}}],
            "usage": {"prompt_tokens": 150, "completion_tokens": 40},
            "timings": {"cache_n": 10},
        })

    llm = LLM(base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(mock_handler))
    conv = Conversador(llm=llm)

    pregunta = "¿Qué es la ley de Ohm?"
    paso = "¿Qué elementos recorre la corriente I1?"
    contexto = {
        "forbidden": [10.5],
        "pending_eqs": [],
        "permite_ecuaciones": False,
        "var_names": ["I1"],
        "known": {5.0},
    }

    res = conv.responder_teoria(pregunta, paso, contexto)
    assert res["encontro"] is True
    assert res["texto"] == respuesta_modelo
    assert res["fichas"][0][0] == "ley_de_ohm"
    assert res["fuente"] == "modelo" and res["guardia"] == {"ok": True, "motivo": ""}
    assert res["intentos"] == [{"texto": respuesta_modelo, "ok": True, "motivo": ""}]


def test_responder_teoria_agrega_la_pregunta_del_paso_si_falta():
    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": r"La ley de Ohm relaciona el voltaje, la corriente y la resistencia: $V = I \cdot R$."}}]})

    conv = Conversador(llm=LLM(base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(mock_handler)))
    paso = "¿Qué elementos recorre la corriente I1?"
    res = conv.responder_teoria("¿Qué es la ley de Ohm?", paso, {"var_names": ["I1"]})
    assert res["fuente"] == "modelo"
    assert res["texto"] == f"La ley de Ohm relaciona el voltaje, la corriente y la resistencia: V = I · R. {paso}"


def test_responder_teoria_modelo_falla_guardia_usa_fallback():
    # El modelo intenta filtrar un resultado prohibido (10.5)
    respuesta_invalida = (
        "La ley de Ohm dice que el resultado final es 10.5 A. "
        "¿Qué elementos recorre la corriente I1?"
    )

    def mock_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": respuesta_invalida}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        })

    llm = LLM(base_url="http://127.0.0.1:8080", transport=httpx.MockTransport(mock_handler))
    conv = Conversador(llm=llm)

    pregunta = "¿Qué es la ley de Ohm?"
    paso = "¿Qué elementos recorre la corriente I1?"
    contexto = {
        "forbidden": [10.5],
        "pending_eqs": [],
        "permite_ecuaciones": False,
        "var_names": ["I1"],
        "known": set(),
    }

    res = conv.responder_teoria(pregunta, paso, contexto)
    assert res["encontro"] is True
    # Al ser rechazada la salida del modelo, debe usarse el texto base de la ficha
    assert res["texto"] != respuesta_invalida
    assert paso in res["texto"]
    assert "ley de Ohm" in res["texto"]
    assert res["fuente"] == "ficha" and res["guardia"]["ok"] is False
    assert [i["motivo"] for i in res["intentos"]] == ["filtra un resultado"] * 2


def test_guard_contenido_tecnico_compara_contra_fichas_mas_base():
    """Prueba requerida por el corte 3: la regla 'agrega contenido técnico' se compara

    contra el texto de las fichas más el mensaje base, no sólo contra el base.
    """
    base = "¿Qué elementos recorre la corriente I1?"
    texto_teoria = (
        "La ley de Ohm relaciona el voltaje, la corriente y la resistencia en un circuito. "
        "¿Qué elementos recorre la corriente I1?"
    )
    fichas_texto = (
        "La ley de Ohm establece la relación fundamental entre el voltaje, la corriente "
        "y la resistencia en un elemento resistivo."
    )

    # 1. Sin texto de fichas: se rechaza porque 'base' no contiene conceptos técnicos
    ok_sin_fichas, motivo_sin = guard(
        out=texto_teoria,
        base=base,
        forbidden=[],
        pending_eqs=[],
        allow_equations=False,
        var_names=[],
        known=set(),
        texto_fichas="",
    )
    assert not ok_sin_fichas
    assert motivo_sin == "agrega contenido técnico"

    # 2. Con texto de fichas: se aprueba porque los conceptos provienen de las fichas
    ok_con_fichas, motivo_con = guard(
        out=texto_teoria,
        base=base,
        forbidden=[],
        pending_eqs=[],
        allow_equations=False,
        var_names=[],
        known=set(),
        texto_fichas=fichas_texto,
    )
    assert ok_con_fichas
    assert motivo_con == ""

    # 3. Si introduce un concepto técnico ajeno (ej. 'supermalla' no presente en la ficha ni en base)
    texto_con_concepto_ajeno = (
        "La ley de Ohm relaciona el voltaje y la supermalla del circuito. "
        "¿Qué elementos recorre la corriente I1?"
    )
    ok_ajeno, motivo_ajeno = guard(
        out=texto_con_concepto_ajeno,
        base=base,
        forbidden=[],
        pending_eqs=[],
        allow_equations=False,
        var_names=[],
        known=set(),
        texto_fichas=fichas_texto,
    )
    assert not ok_ajeno
    assert motivo_ajeno == "agrega contenido técnico"
