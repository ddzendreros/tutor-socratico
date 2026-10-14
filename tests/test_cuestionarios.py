"""Cuestionarios: carga de los archivos, disparadores, respuestas y registro."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

import pytest

from escritorio.alumnos import normalizar
from escritorio.api import ApiTutor
from escritorio.conversacion import Conversador
from escritorio.cuestionarios import (Cuestionario, Pregunta, cargar_cuestionario, cargar_cuestionarios, toca,
                                      validar_respuesta)
from escritorio.registro import leer_eventos
from escritorio.rutas import RAIZ
from tests.test_exportacion import _llaves
from tests.test_registro import ModeloFalso, _entrar, _responder
from tests.test_servidor import ENTRADAS_MALLAS, MALLAS, OHM
from tools.abrir_paquetes import procesar
from tutor.llm import LLM

EJEMPLO = RAIZ / "tests" / "fixtures" / "ejemplo.txt"
TEMAS = {"ohm", "serie_paralelo", "mallas", "nodos"}
_LLAVES: dict = {}


def _archivo(id_: str, cuando: str = "primer_ingreso", version: int = 1, activo: str = "si",
             preguntas: str = "P1 | abierta | ¿Qué esperas?\nP2 | likert5 | Me gusta | Nada; Mucho") -> str:
    return f"ID: {id_}\nTITULO: T {id_}\nVERSION: {version}\nACTIVO: {activo}\nCUANDO: {cuando}\n{preguntas}\n"


def _api(base: Path, archivos: dict[str, str]):
    carpeta = base / "cuestionarios"
    carpeta.mkdir(exist_ok=True)
    for viejo in carpeta.glob("*.txt"):
        viejo.unlink()
    for nombre, texto in archivos.items():
        (carpeta / nombre).write_text(texto, encoding="utf-8")
    if base not in _LLAVES:
        _LLAVES[base] = _llaves(base)
    privada, ruta_publica = _LLAVES[base]
    llm = LLM(base_url="http://modelo-falso", transport=ModeloFalso())
    api = ApiTutor(carpeta_datos=base / "datos", conversador=Conversador(llm=llm), llave_publica=ruta_publica,
                   carpeta_exportaciones=base / "exportaciones", carpeta_cuestionarios=carpeta)
    api.privada_prueba = privada
    return api


def _eventos(api: ApiTutor, codigo: str, tipo_prefijo: str = "cuestionario_") -> list[dict]:
    ruta = api.registro.almacen.ruta_eventos(normalizar(codigo))
    if not ruta.exists():
        return []
    return [e for e in leer_eventos(ruta.read_bytes(), api.privada_prueba) if e["tipo"].startswith(tipo_prefijo)]


def _contestar(api: ApiTutor, respuesta, ms: int = 700) -> dict:
    st, p = api.get_cuestionario_pendiente()
    assert st == 200 and p["pendiente"], p
    st, r = api.post_cuestionario_respuesta({"cuestionario": p["cuestionario"], "version": p["version"],
                                             "item": p["item"]["id"], "respuesta": respuesta, "ms": ms})
    assert st == 200, r
    return {**p, **r}


def _resolver_mallas(api: ApiTutor) -> None:
    assert api.post_iniciar({"problema": MALLAS, "modo": "aprender"})[0] == 200
    for texto in ENTRADAS_MALLAS:
        r = _responder(api, texto)
    assert r["terminado"]


# ------------------------------------------------------------------ carga

def test_carga_el_ejemplo():
    c = cargar_cuestionario(EJEMPLO, TEMAS)
    assert (c.id, c.version, c.activo, c.cuando, c.parametro) == ("ejemplo", 1, True, "primer_ingreso", None)
    assert [(p.id, p.tipo) for p in c.preguntas] == [("P1", "likert5"), ("P2", "opcion"), ("P3", "abierta"),
                                                     ("P4", "numero")]
    assert c.preguntas[0].etiquetas == ["Totalmente inseguro", "Totalmente seguro"]
    assert c.preguntas[1].opciones == ["A) Mallas", "B) Nodos", "C) Reducción"]


def test_el_repositorio_no_trae_cuestionarios():
    assert cargar_cuestionarios(None, TEMAS) == {}
    assert sorted(p.name for p in (RAIZ / "cuestionarios").iterdir()) == ["LEEME.md"]


ERRORES = {
    "falta_id.txt": ("TITULO: T\nVERSION: 1\nACTIVO: si\nCUANDO: manual\nP1 | abierta | x", "faltan campos: ID"),
    "id_invalido.txt": (_archivo("Mi Id"), "línea 1: ID inválido"),
    "version_cero.txt": (_archivo("a", version=0), "línea 3: VERSION"),
    "activo.txt": (_archivo("b", activo="tal vez"), "línea 4: ACTIVO"),
    "cuando.txt": (_archivo("c", cuando="en_la_luna"), "línea 5: CUANDO desconocido"),
    "sin_tema.txt": (_archivo("d", cuando="antes_de_tema"), "línea 5: antes_de_tema necesita el tema"),
    "tema_ajeno.txt": (_archivo("e", cuando="al_terminar_tema:thevenin"), "línea 5: el tema 'thevenin'"),
    "n_invalido.txt": (_archivo("f", cuando="despues_de_problemas:x"), "línea 5: despues_de_problemas"),
    "con_parametro.txt": (_archivo("g", cuando="primer_ingreso:2"), "línea 5: primer_ingreso no lleva"),
    "tipo.txt": (_archivo("h", preguntas="P1 | escala | x"), "línea 6: tipo de pregunta desconocido"),
    "repetida.txt": (_archivo("i", preguntas="P1 | abierta | x\nP1 | abierta | y"), "línea 7: pregunta repetida"),
    "likert.txt": (_archivo("j", preguntas="P1 | likert5 | x"), "línea 6: likert5 necesita"),
    "opcion.txt": (_archivo("k", preguntas="P1 | opcion | x | Sola"), "línea 6: opcion necesita"),
    "abierta.txt": (_archivo("l", preguntas="P1 | abierta | x | A; B"), "línea 6: abierta no lleva"),
    "renglon.txt": (_archivo("m") + "texto suelto\n", "línea 8: renglón no reconocido"),
    "doble.txt": (_archivo("n") + "VERSION: 2\n", "línea 8: VERSION está repetido"),
    "vacio.txt": (_archivo("o", preguntas=""), "no hay preguntas"),
}


@pytest.mark.parametrize("nombre", ERRORES)
def test_un_archivo_con_error_se_informa_y_se_omite(tmp_path: Path, caplog, nombre):
    texto, mensaje = ERRORES[nombre]
    (tmp_path / nombre).write_text(texto, encoding="utf-8")
    (tmp_path / "zz_bueno.txt").write_text(_archivo("bueno"), encoding="utf-8")
    with caplog.at_level(logging.ERROR, logger="tutor_socratico.cuestionarios"):
        assert list(cargar_cuestionarios(tmp_path, TEMAS)) == ["bueno"]
    assert f"{nombre}, {mensaje}" in caplog.text or f"{nombre}: {mensaje}" in caplog.text, caplog.text


def test_un_id_repetido_en_otro_archivo_se_omite(tmp_path: Path, caplog):
    (tmp_path / "a.txt").write_text(_archivo("mismo"), encoding="utf-8")
    (tmp_path / "b.txt").write_text(_archivo("mismo", cuando="manual"), encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        cargados = cargar_cuestionarios(tmp_path, TEMAS)
    assert cargados["mismo"].cuando == "primer_ingreso" and "b.txt: el ID 'mismo' ya lo usa otro archivo" in caplog.text


def test_carpeta_vacia_o_inexistente(tmp_path: Path):
    assert cargar_cuestionarios(tmp_path, TEMAS) == {}
    assert cargar_cuestionarios(tmp_path / "no_existe", TEMAS) == {}


def test_un_archivo_danado_no_impide_arrancar(tmp_path: Path):
    api = _api(tmp_path, {"roto.txt": "esto no es un cuestionario", "bien.txt": _archivo("bien")})
    assert list(api.cuestionarios) == ["bien"]


# ------------------------------------------------------------------ respuestas

LIKERT = Pregunta("L", "likert5", "x", etiquetas=["a", "b"])
OPCION = Pregunta("O", "opcion", "x", opciones=["Mallas", "Nodos"])
ABIERTA = Pregunta("A", "abierta", "x")
NUMERO = Pregunta("N", "numero", "x")


@pytest.mark.parametrize("pregunta, respuesta, esperado", [
    (LIKERT, 1, 1), (LIKERT, 5, 5), (LIKERT, "3", 3), (LIKERT, 4.0, 4),
    (LIKERT, 0, None), (LIKERT, 6, None), (LIKERT, 2.5, None), (LIKERT, True, None), (LIKERT, None, None),
    (OPCION, "Nodos", "Nodos"), (OPCION, " Mallas ", "Mallas"), (OPCION, "mallas", None), (OPCION, "C", None),
    (OPCION, 1, None),
    (ABIERTA, " Me cuesta el supernodo ", "Me cuesta el supernodo"), (ABIERTA, "   ", None), (ABIERTA, 5, None),
    (ABIERTA, "x" * 2001, None),
    (NUMERO, 6, 6), (NUMERO, "6,5", 6.5), (NUMERO, "-2", -2), (NUMERO, "seis", None), (NUMERO, "nan", None),
    (NUMERO, float("inf"), None), (NUMERO, False, None),
])
def test_validar_respuesta(pregunta, respuesta, esperado):
    assert validar_respuesta(pregunta, respuesta) == ((esperado is not None), esperado)


# ------------------------------------------------------------------ disparadores

def _c(cuando: str, parametro: str | None = None, version: int = 1, activo: bool = True) -> Cuestionario:
    return Cuestionario("c", "T", version, activo, cuando, parametro, "", [Pregunta("P1", "abierta", "x")])


def test_cada_disparador():
    nuevo = {"cuestionarios_respondidos": {}, "terminados": []}
    assert toca(_c("primer_ingreso"), "primer_ingreso", nuevo)
    assert not toca(_c("primer_ingreso"), "despues_de_problemas", nuevo)
    assert not toca(_c("primer_ingreso", activo=False), "primer_ingreso", nuevo)
    assert toca(_c("antes_de_tema", "mallas"), "antes_de_tema", nuevo, "mallas")
    assert not toca(_c("antes_de_tema", "mallas"), "antes_de_tema", nuevo, "nodos")
    assert toca(_c("al_terminar_tema", "nodos"), "al_terminar_tema", nuevo, "nodos")
    assert not toca(_c("al_terminar_tema", "nodos"), "antes_de_tema", nuevo, "nodos")
    dos = _c("despues_de_problemas", "2")
    assert not toca(dos, "despues_de_problemas", {**nuevo, "terminados": ["a:aprender"]})
    assert toca(dos, "despues_de_problemas", {**nuevo, "terminados": ["a:aprender", "a:practicar"]})
    assert not any(toca(_c("manual"), m, nuevo, "mallas") for m in
                   ("primer_ingreso", "antes_de_tema", "al_terminar_tema", "despues_de_problemas", "manual"))


def test_lo_respondido_no_se_repite_y_una_version_nueva_si():
    respondido = {"cuestionarios_respondidos": {"c": 1}, "terminados": []}
    assert not toca(_c("primer_ingreso"), "primer_ingreso", respondido)
    assert toca(_c("primer_ingreso", version=2), "primer_ingreso", respondido)


# ------------------------------------------------------------------ por la API

def test_flujo_completo_registro_y_lector(tmp_path: Path):
    api = _api(tmp_path, {"ejemplo.txt": EJEMPLO.read_text(encoding="utf-8")})
    codigo = _entrar(api)

    st, p = api.get_cuestionario_pendiente()
    assert (p["cuestionario"], p["version"], p["indice_item"], p["total_items"], p["item"]["id"]) == (
        "ejemplo", 1, 1, 4, "P1")
    assert p["item"]["etiquetas"] == ["Totalmente inseguro", "Totalmente seguro"]

    # inválida, de otra pregunta, de otra versión o de otro cuestionario: nada se registra
    base = {"cuestionario": "ejemplo", "version": 1, "item": "P1", "ms": 10}
    assert api.post_cuestionario_respuesta({**base, "respuesta": 9})[0] == 400
    assert api.post_cuestionario_respuesta({**base, "item": "P2", "respuesta": "B) Nodos"})[0] == 409
    assert api.post_cuestionario_respuesta({**base, "version": "1", "respuesta": 4})[0] == 409
    assert api.post_cuestionario_respuesta({**base, "cuestionario": "otro", "respuesta": 4})[0] == 409
    assert [e["tipo"] for e in _eventos(api, codigo)] == ["cuestionario_mostrado"]

    secreto = "zeta quasar 7 en el bachillerato"
    assert _contestar(api, 4, ms=1200)["completado"] is False
    # la misma pregunta otra vez ya no se acepta
    assert api.post_cuestionario_respuesta({**base, "respuesta": 5})[0] == 409
    _contestar(api, "B) Nodos")
    _contestar(api, secreto, ms=3000)
    assert _contestar(api, "6,5")["completado"] is True
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})
    assert api.post_cuestionario_respuesta({**base, "respuesta": 4})[0] == 409

    eventos = _eventos(api, codigo)
    respuestas = [e["datos"] for e in eventos if e["tipo"] == "cuestionario_respuesta"]
    assert [(r["item"], r["respuesta"], r["version"]) for r in respuestas] == [
        ("P1", 4, 1), ("P2", "B) Nodos", 1), ("P3", secreto, 1), ("P4", 6.5, 1)]
    # P1 se pidió dos veces (al principio y al contestarla): cada entrega cuenta como mostrada
    assert [e["datos"]["item"] for e in eventos if e["tipo"] == "cuestionario_mostrado"] == [
        "P1", "P1", "P2", "P3", "P4"]
    assert {e["intento"] for e in eventos} == {None}

    carpeta = tmp_path / "datos" / normalizar(codigo)
    for archivo in carpeta.iterdir():
        assert secreto.encode() not in archivo.read_bytes() and b"ejemplo" not in archivo.read_bytes()

    st, r = api.post_exportar({})
    assert st == 200
    procesar([Path(r["archivo"])], api.privada_prueba, tmp_path / "salida")
    with open(tmp_path / "salida" / "cuestionarios.csv", encoding="utf-8-sig", newline="") as f:
        filas = list(csv.DictReader(f))
    assert [(f["cuestionario"], f["version"], f["item"], f["respuesta"], f["ms"]) for f in filas] == [
        ("ejemplo", "1", "P1", "4", "1200"), ("ejemplo", "1", "P2", "B) Nodos", "700"),
        ("ejemplo", "1", "P3", secreto, "3000"), ("ejemplo", "1", "P4", "6.5", "700")]


def test_quien_no_acepta_no_ve_cuestionarios_ni_deja_eventos(tmp_path: Path):
    api = _api(tmp_path, {"ejemplo.txt": EJEMPLO.read_text(encoding="utf-8")})
    codigo = _entrar(api, acepta=False)
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})
    assert api.post_cuestionario_respuesta({"cuestionario": "ejemplo", "version": 1, "item": "P1",
                                            "respuesta": 4})[0] == 409
    _resolver_mallas(api)
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})
    assert sorted(p.name for p in (tmp_path / "datos" / normalizar(codigo)).iterdir()) == ["estado.bin"]


def test_antes_de_tema_bloquea_el_problema_hasta_contestar(tmp_path: Path):
    api = _api(tmp_path, {"previo.txt": _archivo("previo", cuando="antes_de_tema:mallas",
                                                    preguntas="P1 | numero | ¿Cuántas mallas?")})
    codigo = _entrar(api)
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})
    # otro tema no lo dispara
    assert api.post_iniciar({"problema": OHM, "modo": "aprender"})[0] == 200
    st, r = api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    assert st == 409 and r["cuestionario_pendiente"] is True
    # el intento de OHM quedó en pausa y no hay ninguno abierto
    assert api.clave_intento_activa is None
    assert api.post_iniciar({"problema": OHM, "modo": "aprender"})[0] == 409
    _contestar(api, 2)
    assert api.post_iniciar({"problema": MALLAS, "modo": "aprender"})[0] == 200
    assert [e["datos"]["respuesta"] for e in _eventos(api, codigo) if e["tipo"] == "cuestionario_respuesta"] == [2]


def test_al_terminar_un_tema_y_despues_de_problemas(tmp_path: Path):
    api = _api(tmp_path, {
        "a_fin.txt": _archivo("fin_mallas", cuando="al_terminar_tema:mallas", preguntas="P1 | abierta | ¿Qué tal?"),
        "b_conteo.txt": _archivo("tras_uno", cuando="despues_de_problemas:1", preguntas="P1 | numero | ¿Horas?"),
    })
    _entrar(api)
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})
    _resolver_mallas(api)
    # los dos tocan, pero sólo hay uno abierto a la vez
    assert _contestar(api, "Bien")["cuestionario"] == "fin_mallas"
    assert _contestar(api, 3)["cuestionario"] == "tras_uno"
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})


def test_se_retoma_tras_reiniciar_y_una_version_nueva_reaparece(tmp_path: Path):
    archivos = {"ejemplo.txt": EJEMPLO.read_text(encoding="utf-8")}
    api = _api(tmp_path, archivos)
    codigo = _entrar(api)
    _contestar(api, 4)
    api.post_salir({"motivo": "cierre"})

    api = _api(tmp_path, archivos)
    _entrar(api, acepta=None, codigo=codigo)
    st, p = api.get_cuestionario_pendiente()
    assert p["item"]["id"] == "P2" and p["indice_item"] == 2
    for r in ("A) Mallas", "nada", 1):
        _contestar(api, r)
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})
    api.post_salir({"motivo": "cierre"})

    archivos = {"ejemplo.txt": archivos["ejemplo.txt"].replace("VERSION: 1", "VERSION: 2")}
    api = _api(tmp_path, archivos)
    _entrar(api, acepta=None, codigo=codigo)
    st, p = api.get_cuestionario_pendiente()
    assert (p["version"], p["item"]["id"]) == (2, "P1")


def test_si_el_cuestionario_cambia_se_descarta_lo_que_estaba_a_medias(tmp_path: Path):
    archivos = {"ejemplo.txt": EJEMPLO.read_text(encoding="utf-8")}
    api = _api(tmp_path, archivos)
    codigo = _entrar(api)
    _contestar(api, 4)
    api.post_salir({"motivo": "cierre"})

    api = _api(tmp_path, {"ejemplo.txt": archivos["ejemplo.txt"].replace("ACTIVO: si", "ACTIVO: no")})
    _entrar(api, acepta=None, codigo=codigo)
    assert api.get_cuestionario_pendiente() == (200, {"pendiente": False})
    assert api.post_iniciar({"problema": OHM, "modo": "aprender"})[0] == 200
    assert api.estado_alumno["cuestionario_en_curso"] is None


def test_los_eventos_no_se_pegan_al_intento_abierto(tmp_path: Path):
    api = _api(tmp_path, {"ejemplo.txt": EJEMPLO.read_text(encoding="utf-8")})
    codigo = _entrar(api)
    assert api.post_iniciar({"problema": OHM, "modo": "aprender"})[0] == 200
    assert api.clave_intento_activa
    _contestar(api, 5)
    assert {e["intento"] for e in _eventos(api, codigo)} == {None}
