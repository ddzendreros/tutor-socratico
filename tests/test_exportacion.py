"""Pruebas de la exportación (escritorio/exportar.py) y del lector (tools/abrir_paquetes.py).

Sólo usan llaves temporales y carpetas temporales: nada toca Documentos ni %LOCALAPPDATA%.
"""
from __future__ import annotations

import csv
import getpass
import json
import struct
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey

from escritorio.alumnos import generar, normalizar
from escritorio.api import ApiTutor
from escritorio.cifrado import (TSD_MAGIC, abrir_sellado, caja_estado, cargar_llave_privada, generar_llaves,
                                guardar_llave_privada, guardar_llave_publica, leer_llave_publica, sellar,
                                tsd_lotes)
from escritorio.conversacion import Conversador
from escritorio.exportar import exportar
from escritorio.registro import Registro, leer_eventos, leer_instalacion
from tests.test_registro import COMPOSICION, ModeloFalso, _entrar, _responder
from tests.test_servidor import ENTRADAS_MALLAS, MALLAS
from tools import abrir_paquetes
from tools.abrir_paquetes import INDICADORES, TABLAS, abrir_paquete, procesar
from tutor.llm import LLM

ARCHIVOS_SALIDA = ["eventos.jsonl", "eventos.csv", "sesiones_uso.csv", "intentos.csv", "turnos.csv", "teclas.csv",
                   "anotaciones.csv", "equipos.csv", "cuestionarios.csv", "integridad.csv", "diccionario.csv",
                   "LEEME.md"]
TEXTO_SECRETO = "zeta quasar 7"


# ------------------------------------------------------------------ auxiliares

def _llaves(base: Path) -> tuple[PrivateKey, Path]:
    privada, publica = generar_llaves()
    ruta = base / "llaves" / "llave_publica.txt"
    guardar_llave_publica(publica, ruta)
    return privada, ruta


def _api(base: Path, ruta_publica: Path, equipo: str = "equipo") -> ApiTutor:
    llm = LLM(base_url="http://modelo-falso", transport=ModeloFalso())
    return ApiTutor(carpeta_datos=base / equipo / "datos", conversador=Conversador(llm=llm),
                    llave_publica=ruta_publica, carpeta_exportaciones=base / equipo / "exportaciones")


def _tsd(api: ApiTutor, codigo: str) -> bytes:
    return api.registro.almacen.ruta_eventos(normalizar(codigo)).read_bytes()


def _eventos(api: ApiTutor, codigo: str, privada: PrivateKey) -> list[dict]:
    return leer_eventos(_tsd(api, codigo), privada)


def _csv(ruta: Path) -> list[dict]:
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _encabezado(ruta: Path) -> list[str]:
    with open(ruta, encoding="utf-8-sig", newline="") as f:
        return next(csv.reader(f))


def _tipos(filas: list[dict]) -> list[str]:
    return [f["tipo"] for f in filas]


def _con_tsd(origen: Path, destino: Path, tsd: bytes) -> Path:
    """Copia el .zip cambiando sólo eventos.tsd (el resto, incluido el manifiesto, queda igual)."""
    with zipfile.ZipFile(origen) as zi, zipfile.ZipFile(destino, "w", zipfile.ZIP_STORED) as zo:
        for nombre in zi.namelist():
            zo.writestr(nombre, tsd if nombre == "eventos.tsd" else zi.read(nombre))
    return destino


def _tsd_del_zip(ruta: Path) -> bytes:
    with zipfile.ZipFile(ruta) as zf:
        return zf.read("eventos.tsd")


def _tsd_de(lotes: list[bytes]) -> bytes:
    return TSD_MAGIC + b"".join(struct.pack("<I", len(lote)) + lote for lote in lotes)


def _eventos_de_lote(lote: bytes, privada: PrivateKey) -> list[dict]:
    return [json.loads(linea) for linea in abrir_sellado(lote, privada).decode("utf-8").splitlines()]


def _exportar(api: ApiTutor) -> Path:
    st, r = api.post_exportar({})
    assert st == 200, r
    return Path(r["archivo"])


def _resolver(api: ApiTutor, hasta: int | None = None, ui: bool = False) -> None:
    """Abre mallas-01 en Aprender y manda las entradas; con `ui`, también eventos de la interfaz."""
    assert api.post_iniciar({"problema": MALLAS, "modo": "aprender"})[0] == 200
    if ui:
        ahora = int(time.time() * 1000)
        api.post_eventos({"eventos": [
            {"tipo": "ventana", "datos": {"estado": "desenfocada"}, "ms": ahora - 5000},
            {"tipo": "ventana", "datos": {"estado": "enfocada"}, "ms": ahora - 2000},
            {"tipo": "copiar", "datos": {"origen": "enunciado", "caracteres": 4, "texto": "hola"}},
            {"tipo": "clic", "datos": {"control": "pista"}},
        ]})
    for i, texto in enumerate(ENTRADAS_MALLAS[:hasta]):
        r = _responder(api, texto, composicion=COMPOSICION if i == 0 else None)
    if hasta is None:
        assert r["terminado"]


@pytest.fixture(scope="module")
def corrida(tmp_path_factory):
    """Un alumno que resuelve mallas-01 completo en dos sesiones y exporta."""
    base = tmp_path_factory.mktemp("corrida")
    privada, ruta_publica = _llaves(base)
    api = _api(base, ruta_publica)
    codigo = _entrar(api)
    _resolver(api, ui=True)
    while api.anotar_pendiente(reposo=0):
        pass
    api.post_salir({"motivo": "boton"})
    _entrar(api, acepta=None, codigo=codigo)
    zip_ = _exportar(api)
    return SimpleNamespace(base=base, privada=privada, ruta_publica=ruta_publica, api=api, codigo=codigo, zip=zip_)


# ------------------------------------------------------------------ 1. ida y vuelta completa

def test_ida_y_vuelta_completa(tmp_path: Path, corrida):
    salida = tmp_path / "salida"
    resumen = procesar([corrida.zip], corrida.privada, salida)

    assert sorted(p.name for p in salida.iterdir()) == sorted(ARCHIVOS_SALIDA)
    assert resumen["paquetes"] == 1 and resumen["alumnos"] == 1
    for archivo in ARCHIVOS_SALIDA:
        if archivo.endswith(".csv"):
            assert (salida / archivo).read_bytes().startswith(b"\xef\xbb\xbf"), archivo

    en_disco = _eventos(corrida.api, corrida.codigo, corrida.privada)
    por_tipo = lambda tipo: [e for e in en_disco if e["tipo"] == tipo]  # noqa: E731
    jsonl = (salida / "eventos.jsonl").read_text("utf-8").splitlines()
    # la exportación se registra después de generar el paquete: sólo está en el disco
    assert len(jsonl) == len(en_disco) - 1 == resumen["eventos"]
    assert [json.loads(linea)["seq"] for linea in jsonl] == list(range(1, len(jsonl) + 1))
    assert len(_csv(salida / "eventos.csv")) == len(jsonl)

    turnos = _csv(salida / "turnos.csv")
    assert len(turnos) == len(por_tipo("turno")) == len(ENTRADAS_MALLAS)
    assert turnos[0]["comp_teclas"] == "6" and turnos[0]["alumno_texto"] == ENTRADAS_MALLAS[0]
    assert {t["intento"] for t in turnos} == {f"{MALLAS}:aprender"}

    intentos = _csv(salida / "intentos.csv")
    assert len(intentos) == 1
    fila = intentos[0]
    assert fila["terminado"] == "true" and fila["problema"] == MALLAS and fila["modo"] == "aprender"
    assert fila["turnos"] == str(len(ENTRADAS_MALLAS)) and fila["sesiones"] == "1"
    assert fila["salidas_ventana"] == "1" and fila["ms_fuera"] == "3000" and fila["copias"] == "1"
    indicadores = [c for c in fila if c.startswith("ind_")]
    assert len(indicadores) == 16 and all(fila[c] != "" for c in indicadores)
    assert fila["ind_completado"] == "Sí" and fila["ind_duracion_min"] != ""

    # la calibración y el Brier, recalculados a mano desde turnos.csv
    pares = [((int(t["confianza_valor"]) - 1) / 4, t["motor_correcto"] == "true")
             for t in turnos if t["confianza_valor"] and t["motor_correcto"]]
    assert pares and int(fila["confianza_n"]) == sum(1 for t in turnos if t["confianza_valor"])
    assert float(fila["calibracion"]) == pytest.approx(
        sum(p for p, _ in pares) / len(pares) - sum(c for _, c in pares) / len(pares), abs=1e-3)
    assert float(fila["brier"]) == pytest.approx(sum((p - c) ** 2 for p, c in pares) / len(pares), abs=1e-3)

    assert [t["alumno_texto"] for t in turnos].count("") == 0 and len(_csv(salida / "teclas.csv")) == 3
    sesiones = _csv(salida / "sesiones_uso.csv")
    assert len(sesiones) == 2 and sesiones[0]["motivo"] == "boton" and sesiones[0]["fin"] and not sesiones[1]["fin"]
    assert sesiones[0]["intentos_iniciados"] == "1" and sesiones[0]["turnos"] == str(len(ENTRADAS_MALLAS))
    anotaciones = _csv(salida / "anotaciones.csv")
    assert anotaciones and all(a["respuesta"] and a["rubrica_expresa_idea"] == "si" for a in anotaciones)
    equipos = _csv(salida / "equipos.csv")
    assert len(equipos) == 1 and equipos[0]["sesiones"] == "2" and equipos[0]["pantalla_ancho"] == "1920"
    assert _csv(salida / "cuestionarios.csv") == [] and _encabezado(salida / "cuestionarios.csv")[:2] == [
        "codigo", "instalacion"]

    integridad = _csv(salida / "integridad.csv")
    assert _tipos(integridad) == ["paquete"] and integridad[0]["detalle"].startswith(f"{len(jsonl)} eventos")

    # el diccionario describe toda columna de toda tabla, y sólo columnas que existen
    diccionario = {(r["archivo"], r["columna"]) for r in _csv(salida / "diccionario.csv")}
    for archivo in (a for a in ARCHIVOS_SALIDA if a.endswith(".csv")):
        encabezado = _encabezado(salida / archivo)
        assert encabezado == [c for c, *_ in TABLAS[archivo]], archivo
        assert {(archivo, c) for c in encabezado} <= diccionario, archivo
    assert {a for a, _ in diccionario} == set(TABLAS) | {"eventos.jsonl"}
    assert all(r["descripcion"] for r in _csv(salida / "diccionario.csv"))
    leeme = (salida / "LEEME.md").read_text("utf-8")
    assert "Paquetes: 1" in leeme and "Alumnos: 1" in leeme and "intentos.csv" in leeme


# ------------------------------------------------------------------ 2. contraseña equivocada

def test_contrasena_equivocada(tmp_path: Path, corrida, monkeypatch, capsys):
    ruta = tmp_path / "llave_privada.tsk"
    guardar_llave_privada(corrida.privada, "la buena", ruta)
    with pytest.raises(ValueError, match="contraseña"):
        cargar_llave_privada(ruta, "la mala")

    entrada = tmp_path / "entrada"
    entrada.mkdir()
    (entrada / corrida.zip.name).write_bytes(corrida.zip.read_bytes())
    argv = ["--llave", str(ruta), "--entrada", str(entrada), "--salida", str(tmp_path / "salida")]
    monkeypatch.setattr(getpass, "getpass", lambda *_: "la mala")
    assert abrir_paquetes.main(argv) == 1
    assert "contraseña" in capsys.readouterr().err and not (tmp_path / "salida").exists()

    monkeypatch.setattr(getpass, "getpass", lambda *_: "la buena")
    assert abrir_paquetes.main(argv) == 0
    assert (tmp_path / "salida" / "turnos.csv").exists()

    # sin .zip no hay nada que abrir
    vacia = tmp_path / "vacia"
    vacia.mkdir()
    assert abrir_paquetes.main(["--llave", str(ruta), "--entrada", str(vacia), "--salida", str(tmp_path / "x")]) == 1


def test_una_llave_que_no_es_la_del_paquete(tmp_path: Path, corrida):
    otra, _ = generar_llaves()
    eventos, problemas, manifiesto = abrir_paquete(corrida.zip, otra)
    assert eventos == [] and manifiesto is None and _tipos(problemas) == ["llave"]


# ------------------------------------------------------------------ 3 a 5. integridad

def test_un_zip_alterado_se_marca(tmp_path: Path, corrida):
    tsd = bytearray(_tsd_del_zip(corrida.zip))
    tsd[len(tsd) // 2] ^= 0xFF
    alterado = _con_tsd(corrida.zip, tmp_path / "alterado.zip", bytes(tsd))

    _, problemas, manifiesto = abrir_paquete(alterado, corrida.privada)
    assert manifiesto is not None and "sha256" in _tipos(problemas)
    assert all(p["zip"] == "alterado.zip" and p["codigo"] == manifiesto["codigo"] for p in problemas)

    procesar([alterado], corrida.privada, tmp_path / "salida")
    assert "sha256" in _tipos(_csv(tmp_path / "salida" / "integridad.csv"))


def test_un_lote_borrado_de_en_medio(tmp_path: Path, corrida):
    lotes, _ = tsd_lotes(_tsd_del_zip(corrida.zip))
    assert len(lotes) >= 5
    k = len(lotes) // 2
    borrados = _eventos_de_lote(lotes[k], corrida.privada)
    sin_lote = _con_tsd(corrida.zip, tmp_path / "sin_lote.zip", _tsd_de(lotes[:k] + lotes[k + 1:]))

    procesar([sin_lote], corrida.privada, tmp_path / "salida")
    integridad = _csv(tmp_path / "salida" / "integridad.csv")
    assert {"lotes", "hueco", "sha256"} <= set(_tipos(integridad))
    hueco = next(f for f in integridad if f["tipo"] == "hueco")
    assert (int(hueco["desde"]), int(hueco["hasta"])) == (borrados[0]["seq"], borrados[-1]["seq"])
    assert hueco["codigo"] == borrados[0]["codigo"] and hueco["arranque"] == borrados[0]["arranque"]
    assert "faltan_al_final" not in _tipos(integridad)


def test_el_ultimo_lote_borrado(tmp_path: Path, corrida):
    lotes, _ = tsd_lotes(_tsd_del_zip(corrida.zip))
    borrados = _eventos_de_lote(lotes[-1], corrida.privada)
    recortado = _con_tsd(corrida.zip, tmp_path / "recortado.zip", _tsd_de(lotes[:-1]))

    procesar([recortado], corrida.privada, tmp_path / "salida")
    integridad = _csv(tmp_path / "salida" / "integridad.csv")
    assert {"faltan_al_final", "lotes"} <= set(_tipos(integridad)) and "hueco" not in _tipos(integridad)
    fila = next(f for f in integridad if f["tipo"] == "faltan_al_final")
    assert (int(fila["desde"]), int(fila["hasta"])) == (borrados[0]["seq"], borrados[-1]["seq"])


def test_otros_problemas_de_un_paquete(tmp_path: Path, corrida):
    tsd = _tsd_del_zip(corrida.zip)
    a_medias = _con_tsd(corrida.zip, tmp_path / "a_medias.zip", tsd + struct.pack("<I", 900) + b"parte")
    _, problemas, _ = abrir_paquete(a_medias, corrida.privada)
    assert {"bytes_incompletos", "sha256"} <= set(_tipos(problemas))

    otro_lote = bytearray(tsd)
    otro_lote[-3] ^= 0xFF  # dentro del último lote: el sellado ya no abre
    _, problemas, _ = abrir_paquete(_con_tsd(corrida.zip, tmp_path / "ilegible.zip", bytes(otro_lote)),
                                    corrida.privada)
    assert "lote_ilegible" in _tipos(problemas)

    sin_manifiesto = tmp_path / "sin_manifiesto.zip"
    with zipfile.ZipFile(corrida.zip) as zi, zipfile.ZipFile(sin_manifiesto, "w") as zo:
        for nombre in zi.namelist():
            if nombre != "manifiesto.tsd":
                zo.writestr(nombre, zi.read(nombre))
    eventos, problemas, manifiesto = abrir_paquete(sin_manifiesto, corrida.privada)
    assert manifiesto is None and eventos and _tipos(problemas) == ["formato"]

    (tmp_path / "no_es_zip.zip").write_bytes(b"no es un zip")
    assert _tipos(abrir_paquete(tmp_path / "no_es_zip.zip", corrida.privada)[1]) == ["formato"]

    # un manifiesto de otro alumno con los mismos eventos
    otro_codigo = tmp_path / "otro_codigo.zip"
    manifiesto_ajeno = abrir_paquete(corrida.zip, corrida.privada)[2] | {"codigo": "TS-AAAA-A"}
    publica = leer_llave_publica(corrida.ruta_publica)
    with zipfile.ZipFile(corrida.zip) as zi, zipfile.ZipFile(otro_codigo, "w") as zo:
        for nombre in zi.namelist():
            zo.writestr(nombre, sellar(json.dumps(manifiesto_ajeno).encode(), publica)
                        if nombre == "manifiesto.tsd" else zi.read(nombre))
    _, problemas, _ = abrir_paquete(otro_codigo, corrida.privada)
    assert "codigo" in _tipos(problemas)


# ------------------------------------------------------------------ 6. exportaciones acumulativas

def test_dos_exportaciones_acumulativas_sin_duplicados(tmp_path: Path):
    privada, ruta_publica = _llaves(tmp_path)
    api = _api(tmp_path, ruta_publica)
    codigo = _entrar(api)
    _resolver(api, hasta=6)
    primero = _exportar(api)
    for texto in ENTRADAS_MALLAS[6:12]:
        _responder(api, texto)
    segundo = _exportar(api)
    assert primero != segundo and primero.parent == segundo.parent == tmp_path / "equipo" / "exportaciones"

    en_primero = abrir_paquete(primero, privada)[0]
    en_segundo = abrir_paquete(segundo, privada)[0]
    assert 0 < len(en_primero) < len(en_segundo)
    assert [e for e in en_segundo if e["tipo"] == "exportacion"][0]["datos"]["archivo"] == primero.name
    assert not [e for e in en_primero if e["tipo"] == "exportacion"]

    resumen = procesar([primero, segundo], privada, tmp_path / "salida")
    assert resumen["eventos"] == len(en_segundo) and resumen["paquetes"] == 2
    assert _tipos(_csv(tmp_path / "salida" / "integridad.csv")) == ["paquete", "paquete"]
    assert len(_csv(tmp_path / "salida" / "turnos.csv")) == 12


# ------------------------------------------------------------------ 7. el mismo código en dos equipos

def test_el_mismo_codigo_en_dos_equipos_se_junta(tmp_path: Path):
    privada, ruta_publica = _llaves(tmp_path)
    a, b = _api(tmp_path, ruta_publica, "equipo_a"), _api(tmp_path, ruta_publica, "equipo_b")
    codigo = _entrar(a)
    _entrar(b, codigo=codigo)
    assert a.registro.instalacion != b.registro.instalacion
    _resolver(a, hasta=4)
    _resolver(b, hasta=9)
    zips = [_exportar(a), _exportar(b)]

    resumen = procesar(zips, privada, tmp_path / "salida")
    assert resumen["alumnos"] == 1 and resumen["problemas"] == 0
    assert len(_csv(tmp_path / "salida" / "turnos.csv")) == 4 + 9
    assert {f["instalacion"] for f in _csv(tmp_path / "salida" / "eventos.csv")} == {
        a.registro.instalacion, b.registro.instalacion}
    equipos = _csv(tmp_path / "salida" / "equipos.csv")
    assert len(equipos) == 2 and {e["codigos"] for e in equipos} == {_csv(tmp_path / "salida" / "turnos.csv")[0]["codigo"]}
    assert {i["intento"] for i in _csv(tmp_path / "salida" / "intentos.csv")} == {f"{MALLAS}:aprender"}
    assert len(_csv(tmp_path / "salida" / "intentos.csv")) == 2  # uno por equipo
    assert _tipos(_csv(tmp_path / "salida" / "integridad.csv")) == ["paquete", "paquete"]


# ------------------------------------------------------------------ 8. la app no puede leer lo que escribe

def test_la_app_no_puede_leer_eventos_tsd(tmp_path: Path):
    privada, ruta_publica = _llaves(tmp_path)
    api = _api(tmp_path, ruta_publica)
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    _responder(api, f"Tengo dos mallas, {TEXTO_SECRETO}")
    carpeta = tmp_path / "equipo" / "datos" / normalizar(codigo)

    tsd = (carpeta / "eventos.tsd").read_bytes()
    assert TEXTO_SECRETO.encode() not in tsd and b"turno" not in tsd and tsd.startswith(TSD_MAGIC)
    assert any(e["datos"].get("alumno", {}).get("texto", "").endswith(TEXTO_SECRETO)
               for e in leer_eventos(tsd, privada))
    for archivo in carpeta.iterdir():
        assert TEXTO_SECRETO.encode() not in archivo.read_bytes(), archivo.name

    otra, _ = generar_llaves()
    with pytest.raises(CryptoError):
        leer_eventos(tsd, otra)
    # el programa sólo guarda la llave pública
    for objeto in (api, api.registro, api.registro.almacen):
        assert not any(isinstance(v, PrivateKey) for v in vars(objeto).values())
    assert not hasattr(api, "privada") and ruta_publica.read_text().strip() != ""


# ------------------------------------------------------------------ 9. el estado cifrado

def test_el_estado_va_cifrado_y_se_migra(tmp_path: Path):
    _, ruta_publica = _llaves(tmp_path)
    api = _api(tmp_path, ruta_publica)
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    carpeta = tmp_path / "equipo" / "datos" / normalizar(codigo)
    estado_bin = carpeta / "estado.bin"

    crudo = estado_bin.read_bytes()
    assert b"consentimiento" not in crudo and normalizar(codigo).encode() not in crudo and b"mallas" not in crudo
    assert not (carpeta / "estado.json").exists()
    claro = caja_estado(normalizar(codigo), api.registro.sal).decrypt(crudo)
    assert json.loads(claro)["consentimiento"] is True
    api.post_salir({"motivo": "boton"})

    # una instalación anterior dejó el estado en claro: se lee y el primer guardado lo borra
    estado_bin.unlink()
    (carpeta / "estado.json").write_bytes(claro)
    nueva = _api(tmp_path, ruta_publica)
    st, r = nueva.post_entrar({"codigo": codigo})
    assert st == 200 and r["requiere_consentimiento"] is False and r["consentimiento"] is True
    assert estado_bin.exists() and not (carpeta / "estado.json").exists()
    assert b"consentimiento" not in estado_bin.read_bytes()
    nueva.post_salir({"motivo": "boton"})

    # con otra sal el estado no se puede descifrar: se aparta y se empieza de cero
    instalacion = tmp_path / "equipo" / "datos" / "instalacion.json"
    datos = json.loads(instalacion.read_text("utf-8"))
    datos["sal"] = "AAECAwQFBgcICQoLDA0ODw=="
    instalacion.write_text(json.dumps(datos), encoding="utf-8")
    otra_sal = _api(tmp_path, ruta_publica)
    st, r = otra_sal.post_entrar({"codigo": codigo})
    assert st == 200 and r["requiere_consentimiento"] is True
    apartados = list(carpeta.glob("estado-ilegible-*.bin"))
    assert len(apartados) == 1
    assert not list(carpeta.glob("*.json"))


def test_instalacion_json_trae_sal_y_conserva_el_identificador(tmp_path: Path):
    carpeta = tmp_path / "datos"
    instalacion, sal = leer_instalacion(carpeta)
    assert len(sal) == 16 and leer_instalacion(carpeta) == (instalacion, sal)

    # un archivo de una versión anterior, sin sal: se le agrega sin cambiar el identificador
    ruta = carpeta / "instalacion.json"
    ruta.write_text(json.dumps({"instalacion": "id-anterior", "creado": "2026-01-01T00:00:00-06:00"}), "utf-8")
    nuevo, sal_nueva = leer_instalacion(carpeta)
    assert nuevo == "id-anterior" and len(sal_nueva) == 16 and sal_nueva != sal
    assert json.loads(ruta.read_text("utf-8"))["creado"] == "2026-01-01T00:00:00-06:00"
    assert leer_instalacion(carpeta) == ("id-anterior", sal_nueva)


# ------------------------------------------------------------------ 10. sin llave pública

def test_sin_llave_publica_no_se_registra_nada(tmp_path: Path):
    api = _api(tmp_path, tmp_path / "no_existe" / "llave_publica.txt")
    assert api.publica is None and api.registro.activo is False
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    for texto in ENTRADAS_MALLAS[:5]:
        _responder(api, texto)
    api.post_eventos({"eventos": [{"tipo": "clic", "datos": {"control": "pista"}}]})

    st, r = api.post_exportar({})
    assert st == 409 and "llave del estudio" in r["error"]
    carpeta = tmp_path / "equipo" / "datos" / normalizar(codigo)
    assert sorted(p.name for p in carpeta.iterdir()) == ["estado.bin"]
    assert b"consentimiento" not in (carpeta / "estado.bin").read_bytes()
    assert not (tmp_path / "equipo" / "exportaciones").exists()


def test_exportar_sin_consentimiento_o_sin_datos(tmp_path: Path):
    _, ruta_publica = _llaves(tmp_path)
    api = _api(tmp_path, ruta_publica)
    _entrar(api, acepta=False)
    st, r = api.post_exportar({})
    assert st == 409 and "aceptó participar" in r["error"]
    api.post_salir({"motivo": "boton"})

    # acepta, pero su archivo de eventos no existe
    otro = _api(tmp_path, ruta_publica, "otro")
    _entrar(otro)
    otro.registro.almacen.ruta_eventos(otro.codigo_activo).unlink()
    st, r = otro.post_exportar({})
    assert st == 409 and r["error"] == "Todavía no hay datos que exportar."


# ------------------------------------------------------------------ 11. un lote a medias

def test_un_lote_a_medias_se_repara_al_arrancar(tmp_path: Path):
    privada, ruta_publica = _llaves(tmp_path)
    api = _api(tmp_path, ruta_publica)
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    for texto in ENTRADAS_MALLAS[:3]:
        _responder(api, texto)
    api.post_salir({"motivo": "cierre"})
    antes = _eventos(api, codigo, privada)

    ruta = api.registro.almacen.ruta_eventos(normalizar(codigo))
    ruta.write_bytes(ruta.read_bytes() + struct.pack("<I", 4000) + b"un lote que no se termino de escribir")
    assert tsd_lotes(ruta.read_bytes())[1] > 0

    # el siguiente arranque: un Registro nuevo escribe encima sin perder lo anterior
    nuevo = Registro(tmp_path / "equipo" / "datos", leer_llave_publica(ruta_publica))
    nuevo.registrar(codigo, "clic", {"control": "salir"}, origen="ui")
    despues = leer_eventos(ruta.read_bytes(), privada)
    assert tsd_lotes(ruta.read_bytes())[1] == 0
    assert despues[:len(antes)] == antes and len(despues) == len(antes) + 1
    assert despues[-1]["seq"] == 1 and despues[-1]["arranque"] == nuevo.arranque != antes[0]["arranque"]

    # el índice sólo trae conteos
    indice = json.loads(ruta.with_name("eventos.idx").read_text("utf-8"))
    assert set(indice) == {antes[0]["arranque"], nuevo.arranque}
    assert all(set(v) == {"seq_hasta", "lotes"} for v in indice.values())


# ------------------------------------------------------------------ 12. el evento exportacion

def test_el_evento_exportacion_guarda_solo_el_nombre(tmp_path: Path):
    privada, ruta_publica = _llaves(tmp_path)
    api = _api(tmp_path, ruta_publica)
    codigo = _entrar(api)
    _resolver(api, hasta=5)
    st, r = api.post_exportar({})
    assert st == 200 and r["carpeta"] == str(tmp_path / "equipo" / "exportaciones")
    primero = Path(r["archivo"])

    en_disco = _eventos(api, codigo, privada)
    evento = en_disco[-1]
    assert evento["tipo"] == "exportacion" and evento["datos"]["archivo"] == primero.name
    assert set(evento["datos"]) == {"archivo", "lotes", "bytes", "seq_hasta"}
    assert evento["datos"]["bytes"] == primero.stat().st_size
    assert evento["datos"]["seq_hasta"] == evento["seq"] - 1
    assert evento["datos"]["lotes"] == len(tsd_lotes(zipfile.ZipFile(primero).read("eventos.tsd"))[0])
    assert str(tmp_path) not in json.dumps(evento) and "\\" not in evento["datos"]["archivo"]
    assert "exportacion" not in [e["tipo"] for e in abrir_paquete(primero, privada)[0]]

    segundo = _exportar(api)
    assert [e["datos"]["archivo"] for e in abrir_paquete(segundo, privada)[0] if e["tipo"] == "exportacion"] == [
        primero.name]


# ------------------------------------------------------------------ tablas con eventos armados a mano

def _turno(ms: int, tokens: int = 20, fuente: str = "modelo", valor: int | None = None,
           correcto: bool = True) -> dict:
    llamo_al_modelo = fuente == "modelo"
    return {
        "alumno": {"texto": "x", "caracteres": 1, "boton": False}, "composicion": None, "ruta": "motor",
        "intencion": {"etiqueta": "respuesta", "modelo": None, "ms": ms // 10},
        "confianza": {"valor": valor, "ms": 500} if valor else None,
        "motor": {"respondio": True, "paso_id": "a", "paso_tipo": "values", "correcto": correcto, "codigo": "OK",
                  "nivel_antes": 0, "nivel_despues": 0, "pista": False, "intentos_paso": 1, "terminado": False},
        "tutor": {"texto_base": "b", "texto_mostrado": "m", "fuente": fuente,
                  "guardia": {"ok": True, "motivo": ""}, "reintentos": 0,
                  "intentos": [{"texto": "m", "ok": True, "motivo": ""}] if llamo_al_modelo else [],
                  "ms": ms, "tokens_prompt": 100, "tokens_salida": tokens, "tokens_cache": 0},
        "teoria": None,
    }


def _sembrar(base: Path):
    """Un alumno con dos sesiones, un intento retomado y otro que se abre de nuevo después de terminar."""
    privada, ruta_publica = _llaves(base)
    publica = leer_llave_publica(ruta_publica)
    reg = Registro(base / "datos", publica)
    codigo = generar(1)[0]
    clave = "p1:aprender"
    descripcion = {"problema": "p1", "tema": "ohm", "metodo": "ohm", "modo": "aprender", "segundos_activos": 0.0}
    ahora = int(time.time() * 1000)

    def ev(tipo, datos, intento=clave, ms=None, origen="app"):
        return {"tipo": tipo, "datos": datos, "intento": intento, "origen": origen, **({"ms": ms} if ms else {})}

    inicio = {"equipo": {"so": "Windows", "pantalla": {"ancho": 1366, "alto": 768, "escala": 1.0}, "modo": "ventana"},
              "versiones": {"app": "1.0.0", "modelo": "m.gguf", "modelo_sha256": "abc123"},
              "modelo": {"estado": "cargando", "ms_carga": None}, "version_consentimiento": "1.0"}
    reg.registrar_varios(codigo, [
        ev("sesion_iniciada", inicio, None),
        ev("modelo_estado", {"estado": "listo", "ms_carga": 8000, "ms_precalentar": 20000, "hilos": 4, "ctx": 4096},
           None),
        ev("intento_iniciado", {**descripcion, "retomado": False, "paso_id": "a", "paso_tipo": "values"}),
        # confianza 5 y acierto, 1 y fallo, 5 y fallo
        ev("turno", _turno(1000, valor=5)), ev("turno", _turno(2000, valor=1, correcto=False)),
        ev("turno", _turno(3000, valor=5, correcto=False)), ev("turno", _turno(4000)), ev("turno", _turno(10000)),
        ev("turno", _turno(0, fuente="plantilla")),
        ev("ventana", {"estado": "oculta"}, ms=ahora - 9000, origen="ui"),
        ev("ventana", {"estado": "desenfocada"}, ms=ahora - 8500, origen="ui"),
        ev("ventana", {"estado": "visible"}, ms=ahora - 7000, origen="ui"),
        ev("ventana", {"estado": "enfocada"}, ms=ahora - 6000, origen="ui"),
        ev("intento_pausado", {**descripcion, "motivo": "cierre", "segundos_activos": 50.0}),
        ev("sesion_terminada", {"motivo": "cierre", "ms": 60000}, None),
        ev("sesion_iniciada", inicio, None),
        ev("intento_iniciado", {**descripcion, "retomado": True, "paso_id": "a", "paso_tipo": "values",
                                "segundos_activos": 50.0}),
        ev("turno", _turno(5000)),
        ev("intento_terminado", {**descripcion, "segundos_activos": 80.0,
                                 "indicadores": {n: i for i, (n, *_) in enumerate(INDICADORES)}}),
        ev("clic", {"control": "pista"}, None, origen="ui"),
        ev("intento_iniciado", {**descripcion, "retomado": False, "paso_id": "a", "paso_tipo": "values"}),
        ev("turno", _turno(1000)),
    ])
    zip_ = exportar(base / "datos", normalizar(codigo), reg.instalacion, publica, {}, base / "zips")["ruta"]
    return privada, ruta_publica, zip_


def test_intentos_y_equipos_con_eventos_armados(tmp_path: Path):
    privada, _, zip_ = _sembrar(tmp_path)
    procesar([zip_], privada, tmp_path / "salida")

    primero, segundo = _csv(tmp_path / "salida" / "intentos.csv")
    assert (primero["terminado"], primero["sesiones"], primero["turnos"]) == ("true", "2", "7")
    assert (segundo["terminado"], segundo["sesiones"], segundo["turnos"]) == ("false", "1", "1")
    assert primero["segundos_activos"] == "80.0" and segundo["segundos_activos"] == "0.0"
    assert primero["ind_intentos_del_estudiante"] == "0" and primero["ind_duracion_min"] == "15"
    assert segundo["ind_completado"] == "" and segundo["ind_duracion_min"] == ""
    assert primero["turnos_modelo"] == "6" and primero["pct_plantilla"] == "14.3" and segundo["pct_plantilla"] == "0.0"
    # la salida cuenta desde `oculta` hasta el primer `visible` (2000 ms); `enfocada` ya no suma nada
    assert (primero["salidas_ventana"], primero["ms_fuera"]) == ("1", "2000")
    # calibración y Brier con los pares (1, acierto), (0, fallo) y (1, fallo)
    assert (primero["confianza_n"], primero["confianza_media"]) == ("3", "3.67")
    assert float(primero["calibracion"]) == pytest.approx(1 / 3, abs=1e-3)
    assert float(primero["brier"]) == pytest.approx(1 / 3, abs=1e-3)

    (equipo,) = _csv(tmp_path / "salida" / "equipos.csv")
    assert (equipo["sesiones"], equipo["modelo"], equipo["modelo_sha256"], equipo["app"]) == (
        "2", "m.gguf", "abc123", "1.0.0")
    assert (equipo["hilos"], equipo["ctx"], equipo["pantalla_ancho"]) == ("4", "4096", "1366")
    assert (equipo["ms_carga_mediana"], equipo["ms_precalentar_mediana"]) == ("8000.0", "20000.0")
    # 7 turnos con voz: 1000, 2000, 3000, 4000, 10000, 5000 y 1000 ms
    assert (equipo["voz_ms_mediana"], equipo["voz_ms_p90"]) == ("3000.0", "10000.0")
    assert equipo["voz_tokens_s_mediana"] == "6.7" and equipo["pct_plantilla"] == "12.5"
    # 8 turnos, con intención de 0 (la plantilla), 100, 100, 200, 300, 400, 500 y 1000 ms
    assert equipo["intencion_ms_mediana"] == "250.0"

    sesiones = _csv(tmp_path / "salida" / "sesiones_uso.csv")
    assert [(s["motivo"], s["ms"], s["intentos_iniciados"], s["turnos"]) for s in sesiones] == [
        ("cierre", "60000", "1", "6"), ("", "", "2", "2")]


def test_un_evento_repetido_con_otro_contenido_es_un_conflicto(tmp_path: Path):
    privada, ruta_publica, zip_ = _sembrar(tmp_path)
    eventos = abrir_paquete(zip_, privada)[0]
    eventos[3]["datos"]["alumno"]["texto"] = "otra cosa"
    publica = leer_llave_publica(ruta_publica)
    lote = sellar(("".join(json.dumps(e) + "\n" for e in eventos)).encode(), publica)
    editado = _con_tsd(zip_, tmp_path / "editado.zip", _tsd_de([lote]))

    resumen = procesar([zip_, editado], privada, tmp_path / "salida")
    integridad = _csv(tmp_path / "salida" / "integridad.csv")
    conflictos = [f for f in integridad if f["tipo"] == "conflicto"]
    assert len(conflictos) == 1 and conflictos[0]["zip"] == "editado.zip"
    assert conflictos[0]["desde"] == str(eventos[3]["seq"])
    # se conserva el primero que llegó
    assert resumen["eventos"] == len(eventos)
    assert "otra cosa" not in (tmp_path / "salida" / "eventos.jsonl").read_text("utf-8")


# ------------------------------------------------------------------ tiempos del modelo y celdas de texto

def test_un_cero_de_carga_no_pisa_el_tiempo_medido():
    def ev(ms, tipo, datos):
        return {"instalacion": "i1", "codigo": "c1", "arranque": "a1", "ms": ms, "t": str(ms),
                "tipo": tipo, "datos": datos}

    eventos = [
        ev(1, "sesion_iniciada", {"modelo": {"estado": "cargando", "ms_carga": 0, "ms_precalentar": None}}),
        ev(2, "modelo_estado", {"estado": "listo", "ms_carga": 8000, "ms_precalentar": 20000}),
        # otro alumno entra en el mismo arranque con el modelo ya listo, y uno más en uno nuevo aún cargando
        ev(3, "sesion_iniciada", {"modelo": {"estado": "listo", "ms_carga": 8000, "ms_precalentar": 20000}}),
        {**ev(4, "sesion_iniciada", {"modelo": {"estado": "cargando", "ms_carga": 0, "ms_precalentar": 0}}),
         "arranque": "a2"},
    ]
    (equipo,) = abrir_paquetes._filas_equipos(eventos)
    assert (equipo["ms_carga_mediana"], equipo["ms_precalentar_mediana"]) == (8000, 20000)


@pytest.mark.parametrize("texto, celda", [
    ("=HYPERLINK(\"http://x\")", "'=HYPERLINK(\"http://x\")"), ("+1+1", "'+1+1"), ("-2+3", "'-2+3"),
    ("@SUMA(A1)", "'@SUMA(A1)"), ("\tx", "'\tx"), ("\rx", "'\rx"),
    ("-2.5", "-2.5"), ("+3", "+3"), ("I1 = 2 A", "I1 = 2 A"), ("", ""),
])
def test_las_celdas_de_texto_no_se_abren_como_formula(texto, celda):
    assert abrir_paquetes._celda(texto) == celda


def test_los_numeros_y_el_jsonl_quedan_intactos(tmp_path: Path):
    assert abrir_paquetes._celda(-3) == "-3" and abrir_paquetes._celda(-0.5) == "-0.5"
    assert abrir_paquetes._celda(True) == "true" and abrir_paquetes._celda(None) == ""
    privada, ruta_publica = _llaves(tmp_path)
    api = _api(tmp_path, ruta_publica)
    _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    _responder(api, "=1+1")
    zip_ = api.post_exportar({})[1]["archivo"]
    procesar([Path(zip_)], privada, tmp_path / "salida")
    textos = [t["alumno_texto"] for t in _csv(tmp_path / "salida" / "turnos.csv")]
    assert "'=1+1" in textos and "=1+1" not in textos
    assert '"texto": "=1+1"' in (tmp_path / "salida" / "eventos.jsonl").read_text(encoding="utf-8")


def test_una_llave_publica_danada_no_tumba_la_app(tmp_path: Path):
    for i, contenido in enumerate([b"no es base64 !!", b"QUJD", b"\xff\xfe"]):
        ruta = tmp_path / f"llave{i}.txt"
        ruta.write_bytes(contenido)
        api = _api(tmp_path, ruta, equipo=f"equipo{i}")
        assert api.publica is None and api.registro.activo is False
        _entrar(api)
        assert api.post_exportar({})[0] == 409
