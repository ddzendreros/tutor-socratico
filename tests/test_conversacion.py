"""Pruebas del módulo escritorio.conversacion con un servidor HTTP falso."""
from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx

from tutor.engine import Session, reconstruir
from tutor.llm import LLM, guard
from tutor.problems import load_bank
from escritorio.conversacion import (
    Conversador,
    EJEMPLOS_INTENCION,
    EJEMPLOS_VOZ,
    INTENCIONES_VALIDAS,
    PROMPT_INTENCION,
    PROMPT_VOZ,
    contexto_guardia,
    copia_un_ejemplo,
    limpiar_salida,
    mensajes_intencion,
    plantilla_para_intencion,
)

DATOS = Path(__file__).resolve().parent.parent / "tools" / "datos_evaluacion"


# ------------------------------------------------------------------ mock HTTP


def _respuesta_intencion(intencion: str = "respuesta") -> httpx.Response:
    cuerpo = {
        "choices": [
            {"message": {"content": json.dumps({"intencion": intencion})}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "timings": {"cache_n": 0, "predicted_per_second": 30.0},
    }
    return httpx.Response(200, json=cuerpo)


def _respuesta_voz(texto: str) -> httpx.Response:
    cuerpo = {
        "choices": [{"message": {"content": texto}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        "timings": {"cache_n": 8, "predicted_per_second": 25.0},
    }
    return httpx.Response(200, json=cuerpo)


class TransporteFalso(httpx.BaseTransport):
    """Responde a /v1/chat/completions según la secuencia configurada."""

    def __init__(self, respuestas: list[httpx.Response]):
        self._respuestas = list(respuestas)
        self._llamadas = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self._llamadas < len(self._respuestas):
            resp = self._respuestas[self._llamadas]
        else:
            resp = self._respuestas[-1]
        self._llamadas += 1
        return resp

    @property
    def llamadas(self) -> int:
        return self._llamadas


def _llm(respuestas: list[httpx.Response]) -> tuple[LLM, TransporteFalso]:
    transporte = TransporteFalso(respuestas)
    llm = LLM(base_url="http://127.0.0.1:9999", transport=transporte)
    return llm, transporte


# ------------------------------------------------------------------ entender


def test_entender_devuelve_intencion_valida():
    llm, _ = _llm([_respuesta_intencion("pregunta_teoria")])
    conv = Conversador(llm=llm)
    resultado = conv.entender("¿qué es la ley de Ohm?", "Escribe la ecuación de la malla 1")
    assert resultado is not None
    assert resultado["intencion"] == "pregunta_teoria"
    assert "ms" in resultado


def test_entender_intencion_ayuda():
    llm, _ = _llm([_respuesta_intencion("ayuda")])
    conv = Conversador(llm=llm)
    resultado = conv.entender("dame una pista")
    assert resultado is not None
    assert resultado["intencion"] == "ayuda"


def test_entender_todas_las_intenciones_validas():
    for intencion in INTENCIONES_VALIDAS:
        llm, _ = _llm([_respuesta_intencion(intencion)])
        conv = Conversador(llm=llm)
        res = conv.entender("texto de prueba")
        assert res is not None
        assert res["intencion"] == intencion


def test_entender_sin_modelo_devuelve_none():
    conv = Conversador(llm=None)
    assert conv.entender("¿qué hago?") is None


def test_entender_respuesta_malformada_devuelve_none():
    cuerpo_malo = {
        "choices": [{"message": {"content": "esto no es JSON"}}],
        "usage": {},
        "timings": {},
    }
    llm, _ = _llm([httpx.Response(200, json=cuerpo_malo)])
    conv = Conversador(llm=llm)
    assert conv.entender("texto") is None


def test_entender_intencion_fuera_de_enum_devuelve_none():
    cuerpo = {
        "choices": [{"message": {"content": json.dumps({"intencion": "inventada"})}}],
        "usage": {},
        "timings": {},
    }
    llm, _ = _llm([httpx.Response(200, json=cuerpo)])
    conv = Conversador(llm=llm)
    assert conv.entender("texto") is None


# ------------------------------------------------------------------ redactar


def test_redactar_usa_modelo_cuando_guardia_aprueba():
    texto_modelo = "Checa el signo de la fuente. ¿Con qué signo entra la de 12 V?"
    llm, _ = _llm([_respuesta_voz(texto_modelo)])
    conv = Conversador(llm=llm)
    res = conv.redactar(
        base="Revisa el signo. ¿Qué signo tiene la fuente de 12 V?",
        alumno="creo que es positivo",
        contexto={},
    )
    assert res["fuente"] == "modelo"
    assert res["texto"] == texto_modelo
    assert res["reintentos"] == 0
    assert res["guardia"]["ok"] is True


def test_redactar_fallback_a_plantilla_cuando_guardia_rechaza():
    # La guardia rechaza porque el modelo inventa un número no permitido
    texto_modelo = "El resultado es 42 A. ¿Qué signo tiene la fuente?"
    base = "Revisa el signo. ¿Qué signo tiene la fuente?"
    llm, transporte = _llm([_respuesta_voz(texto_modelo), _respuesta_voz(texto_modelo)])
    conv = Conversador(llm=llm)
    res = conv.redactar(
        base=base,
        alumno="creo que es positivo",
        contexto={"forbidden": [42.0]},
    )
    assert res["fuente"] == "plantilla"
    assert res["texto"] == base
    assert res["reintentos"] == 1
    assert res["guardia"]["ok"] is False
    assert transporte.llamadas == 2


def test_redactar_reintento_aprueba_guardia():
    base = "Revisa el signo. ¿Qué signo tiene la fuente?"
    primer_intento = "El valor es 99. ¿Signo?"      # rechazado (número inventado)
    segundo_intento = "Checa el signo. ¿Qué signo tiene la fuente?"  # aprobado
    llm, transporte = _llm([
        _respuesta_voz(primer_intento),
        _respuesta_voz(segundo_intento),
    ])
    conv = Conversador(llm=llm)
    res = conv.redactar(
        base=base,
        alumno="no sé",
        contexto={"forbidden": [99.0]},
    )
    assert res["fuente"] == "modelo"
    assert res["texto"] == segundo_intento
    assert res["reintentos"] == 1
    assert transporte.llamadas == 2


def test_redactar_sin_modelo_devuelve_base():
    conv = Conversador(llm=None)
    base = "Escribe la ecuación de la malla 1."
    res = conv.redactar(base=base, alumno="no sé", contexto={})
    assert res["fuente"] == "plantilla"
    assert res["texto"] == base
    assert res["guardia"]["motivo"] == "sin modelo"


def test_redactar_forma_dict_de_retorno():
    texto_modelo = "Revisa la dirección. ¿Qué signo tiene la fuente?"
    llm, _ = _llm([_respuesta_voz(texto_modelo)])
    conv = Conversador(llm=llm)
    res = conv.redactar(
        base="Revisa la dirección. ¿Qué signo tiene la fuente?",
        alumno="me da 5",
    )
    campos = {"texto", "fuente", "guardia", "reintentos", "ms", "tokens_prompt", "tokens_salida", "tokens_cache"}
    assert campos.issubset(res.keys()), f"Faltan campos: {campos - res.keys()}"
    assert isinstance(res["guardia"], dict)
    assert "ok" in res["guardia"]
    assert "motivo" in res["guardia"]


# --------------------------------------------------------- ruta ambigua + intención


def test_ruta_ambigua_con_modelo_clasifica_correctamente():
    """Si ruta_rapida devuelve 'ambiguo', entender debería clasificar."""
    from tutor.engine import Session
    from tutor.problems import load_bank

    banco = {p.id: p for p in load_bank()}
    pid = next(k for k in banco if "mallas-01" in k)
    p = banco[pid]
    s = Session(p.circuit, p.method, mode="aprender")
    s.start()

    ruta = s.ruta_rapida("¿qué me pides exactamente?")
    assert ruta == "ambiguo"

    llm, _ = _llm([_respuesta_intencion("pregunta_paso")])
    conv = Conversador(llm=llm)
    res = conv.entender("¿qué me pides exactamente?", s.pregunta_actual())
    assert res is not None
    assert res["intencion"] == "pregunta_paso"


# ------------------------------------------------------------------ plantillas


def test_plantillas_todas_las_intenciones_con_pregunta():
    for intencion in ("pregunta_paso", "animo", "fuera_de_tema", "charla"):
        texto = plantilla_para_intencion(intencion, pregunta="¿Cuántas mallas hay?")
        assert "¿Cuántas mallas hay?" in texto


def test_plantilla_intencion_desconocida_devuelve_pregunta():
    texto = plantilla_para_intencion("desconocida", pregunta="¿Cuántas mallas hay?")
    assert "¿Cuántas mallas hay?" in texto


# ------------------------------------------------- limpieza, intentos y contexto del motor


def test_limpiar_salida_quita_etiquetas_y_comillas():
    assert limpiar_salida('Tutor: "Checa el signo. ¿Con qué signo entra?"') == "Checa el signo. ¿Con qué signo entra?"
    assert limpiar_salida("Salida: «Bien.  ¿Y luego?»") == "Bien. ¿Y luego?"
    assert limpiar_salida("Mensaje base: ¿Qué sigue?") == "¿Qué sigue?"


def test_redactar_limpia_antes_de_la_guardia_y_registra_intentos():
    llm, _ = _llm([_respuesta_voz('Tutor: "Checa el signo. ¿Qué signo tiene la fuente?"')])
    res = Conversador(llm=llm).redactar("Revisa el signo. ¿Qué signo tiene la fuente?", "no sé", {})
    assert res["fuente"] == "modelo"
    assert res["texto"] == "Checa el signo. ¿Qué signo tiene la fuente?"
    assert res["intentos"] == [{"texto": res["texto"], "ok": True, "motivo": ""}]


def test_redactar_registra_los_intentos_rechazados():
    llm, _ = _llm([_respuesta_voz("Son 42 A. ¿Signo?")])
    res = Conversador(llm=llm).redactar("Revisa el signo. ¿Qué signo tiene?", "no sé", {"forbidden": [42.0]})
    assert res["fuente"] == "plantilla"
    assert [i["ok"] for i in res["intentos"]] == [False, False]
    assert all(i["motivo"] for i in res["intentos"])


def _sesion_mallas(modo):
    banco = {p.id: p for p in load_bank()}
    p = banco["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, modo)
    s.start()
    return s


def test_contexto_guardia_sale_del_motor():
    s = _sesion_mallas("practicar")
    ctx = contexto_guardia(s)
    assert len(ctx["forbidden"]) == 2                   # I1 e I2, que el alumno aún no obtiene
    assert len(ctx["pending_eqs"]) == 2                 # las dos ecuaciones de malla
    assert ctx["var_names"] == ["I1", "I2"]
    assert 100.0 in ctx["known"] and 20.0 in ctx["known"]
    assert ctx["permite_ecuaciones"] is False


def test_guardia_con_contexto_del_motor_bloquea_resultados_y_ecuaciones():
    s = _sesion_mallas("practicar")
    ctx = contexto_guardia(s)
    base = s.pregunta_actual()
    for fuga in ("Plantea la de la malla 1: 30I1 - 20I2 = 0. ¿La escribes?",
                 "Ya casi: I1 = 1.82 A. ¿Puedes plantear la ecuación?"):
        llm, _ = _llm([_respuesta_voz(fuga)])
        assert Conversador(llm=llm).redactar(base, "no sé", ctx)["fuente"] == "plantilla"


def test_copia_de_un_ejemplo_del_prompt_se_rechaza():
    _, base_1, salida_1 = EJEMPLOS_VOZ[0]
    pista = "Revisa la corriente de malla I1: ¿de qué elementos sale?"
    assert copia_un_ejemplo("Vamos a tomar todas las corrientes de malla en sentido horario. ¿De qué sale?", pista)
    assert not copia_un_ejemplo(salida_1, base_1)            # con su propio mensaje base sí vale
    llm, _ = _llm([_respuesta_voz("Vamos a tomar todas las corrientes de malla en sentido horario. "
                                  "¿De qué elementos sale la corriente de malla I1?")])
    res = Conversador(llm=llm).redactar(pista, "no sé", {})
    assert res["fuente"] == "plantilla" and res["intentos"][0]["motivo"] == "copia un ejemplo"


def test_los_ejemplos_del_prompt_pasan_la_guardia():
    for _, base, salida in EJEMPLOS_VOZ:
        assert salida in PROMPT_VOZ
        assert guard(salida, base, [], [], False, [], set())[0]


# ------------------------------------------------------------ datos de evaluación


def _ejemplos_del_prompt():
    return {e.casefold() for ejemplos in EJEMPLOS_INTENCION.values() for e in ejemplos}


def test_intenciones_csv_llegan_al_modelo_y_no_repiten_el_prompt():
    filas = list(csv.DictReader(open(DATOS / "intenciones.csv", encoding="utf-8")))
    s = _sesion_mallas("aprender")
    ejemplos = _ejemplos_del_prompt()
    assert len(filas) >= 100
    assert {f["intencion"] for f in filas} == set(INTENCIONES_VALIDAS)
    assert sum(f["intencion"] == "ayuda" for f in filas) >= 10
    assert [f["texto"] for f in filas if s.ruta_rapida(f["texto"]) != "ambiguo"] == []
    assert [f["texto"] for f in filas if f["texto"].casefold() in ejemplos] == []
    assert [f["texto"] for f in filas if f["texto"].casefold() in PROMPT_INTENCION.casefold()] == []


def test_intencion_lleva_los_ejemplos_como_turnos():
    mensajes = mensajes_intencion("¿es real?", "¿Qué tipo es cada malla?")
    assert mensajes[0]["role"] == "system" and mensajes[-1]["content"].endswith("¿es real?")
    pares = mensajes[1:-1]
    assert len(pares) == 2 * sum(map(len, EJEMPLOS_INTENCION.values()))
    assert {json.loads(m["content"])["intencion"] for m in pares[1::2]} == set(INTENCIONES_VALIDAS)


def test_turnos_jsonl_siguen_dando_el_mismo_mensaje_base():
    banco = {p.id: p for p in load_bank()}
    turnos = [json.loads(linea) for linea in open(DATOS / "turnos.jsonl", encoding="utf-8")]
    assert len(turnos) >= 40 and len({t["problema"] for t in turnos}) == len(banco)
    for t in turnos:
        p = banco[t["problema"]]
        s = reconstruir(p.circuit, p.method, t["modo"], t["entradas"])
        assert s.ultimo_turno["mensaje_base"] == t["base"]
