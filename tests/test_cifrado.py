"""Pruebas del cifrado (escritorio/cifrado.py). Sólo usan llaves temporales creadas en tmp_path."""
from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from nacl.exceptions import CryptoError

from escritorio.cifrado import (
    TSD_MAGIC,
    abrir_sellado,
    caja_estado,
    cargar_llave_privada,
    generar_llaves,
    guardar_llave_privada,
    guardar_llave_publica,
    huella_publica,
    leer_llave_publica,
    sellar,
    tsd_agregar,
    tsd_lotes,
    tsd_reparar,
)


@pytest.fixture(scope="module")
def par():
    return generar_llaves()


def test_la_llave_privada_ida_y_vuelta(tmp_path: Path, par):
    privada, publica = par
    ruta = tmp_path / "llave_privada.tsk"
    guardar_llave_privada(privada, "clave de prueba", ruta)

    doc = json.loads(ruta.read_text("utf-8"))
    assert doc["formato"] == "tutor-llave-1" and doc["huella"] == huella_publica(publica)
    assert {"sal", "opslimit", "memlimit", "cifrado"} <= set(doc)
    assert "clave de prueba" not in ruta.read_text("utf-8")

    cargada = cargar_llave_privada(ruta, "clave de prueba")
    assert bytes(cargada) == bytes(privada)
    assert huella_publica(cargada.public_key) == huella_publica(publica)


def test_contrasena_equivocada_da_un_error_claro(tmp_path: Path, par):
    ruta = tmp_path / "llave_privada.tsk"
    guardar_llave_privada(par[0], "la buena", ruta)
    with pytest.raises(ValueError, match="contraseña no corresponde"):
        cargar_llave_privada(ruta, "la mala")


def test_una_llave_danada_o_ajena_da_un_error_claro(tmp_path: Path, par):
    ruta = tmp_path / "llave_privada.tsk"
    guardar_llave_privada(par[0], "clave", ruta)
    doc = json.loads(ruta.read_text("utf-8"))

    ruta.write_text("esto no es json", encoding="utf-8")
    with pytest.raises(ValueError, match="No se pudo leer"):
        cargar_llave_privada(ruta, "clave")

    ruta.write_text(json.dumps({**doc, "formato": "otro"}), encoding="utf-8")
    with pytest.raises(ValueError, match="no es una llave privada"):
        cargar_llave_privada(ruta, "clave")

    ruta.write_text(json.dumps({**doc, "huella": "000000000000"}), encoding="utf-8")
    with pytest.raises(ValueError, match="dañada"):
        cargar_llave_privada(ruta, "clave")

    with pytest.raises(ValueError, match="No se pudo leer"):
        cargar_llave_privada(tmp_path / "no_existe.tsk", "clave")


def test_la_llave_publica_ida_y_vuelta(tmp_path: Path, par):
    ruta = tmp_path / "carpeta nueva" / "llave_publica.txt"
    guardar_llave_publica(par[1], ruta)
    assert bytes(leer_llave_publica(ruta)) == bytes(par[1])
    assert len(huella_publica(par[1])) == 12


def test_sellar_y_abrir_con_la_llave_correcta(par):
    privada, publica = par
    datos = ("Tengo dos mallas: I1 + I2 = 2 A, ¿está bien? " * 50).encode("utf-8")
    sellado = sellar(datos, publica)
    assert datos[:30] not in sellado and len(sellado) < len(datos)
    assert abrir_sellado(sellado, privada) == datos
    # cada sellado es distinto aunque el contenido sea igual
    assert sellar(datos, publica) != sellado

    otra, _ = generar_llaves()
    with pytest.raises(CryptoError):
        abrir_sellado(sellado, otra)
    # un byte cambiado se detecta: el sellado está autenticado
    alterado = bytearray(sellado)
    alterado[len(alterado) // 2] ^= 0x01
    with pytest.raises(CryptoError):
        abrir_sellado(bytes(alterado), privada)


def test_tsd_agregar_y_leer_lotes(tmp_path: Path, par):
    privada, publica = par
    ruta = tmp_path / "eventos.tsd"
    tsd_agregar(ruta, [sellar(b"uno", publica)])
    tsd_agregar(ruta, [sellar(b"dos", publica), sellar(b"tres", publica)])

    datos = ruta.read_bytes()
    assert datos.startswith(TSD_MAGIC)
    lotes, sobrante = tsd_lotes(datos)
    assert sobrante == 0
    assert [abrir_sellado(lote, privada) for lote in lotes] == [b"uno", b"dos", b"tres"]


def test_un_final_a_medias_se_detecta_y_se_repara(tmp_path: Path, par):
    privada, publica = par
    ruta = tmp_path / "eventos.tsd"
    tsd_agregar(ruta, [sellar(b"uno", publica), sellar(b"dos", publica)])
    completo = ruta.read_bytes()

    # el programa se cerró a la mitad de un lote: el encabezado de longitud promete más de lo que hay
    ruta.write_bytes(completo + struct.pack("<I", 500) + b"parte")
    lotes, sobrante = tsd_lotes(ruta.read_bytes())
    assert len(lotes) == 2 and sobrante == 4 + 5

    assert tsd_reparar(ruta) == 9 and ruta.read_bytes() == completo
    assert tsd_reparar(ruta) == 0

    # también cuando sobran menos de 4 bytes (ni siquiera cabe la longitud)
    ruta.write_bytes(completo + b"\x01\x02")
    assert tsd_lotes(ruta.read_bytes())[1] == 2 and tsd_reparar(ruta) == 2

    # después de reparar, lo que se agregue se puede leer
    tsd_agregar(ruta, [sellar(b"tres", publica)])
    assert [abrir_sellado(lote, privada) for lote in tsd_lotes(ruta.read_bytes())[0]] == [b"uno", b"dos", b"tres"]


def test_tsd_sin_encabezado(tmp_path: Path):
    with pytest.raises(ValueError, match="TSD1"):
        tsd_lotes(b"no es un archivo de eventos")
    ruta = tmp_path / "ajeno.tsd"
    ruta.write_bytes(b"no es un archivo de eventos")
    assert tsd_reparar(ruta) == 0 and ruta.read_bytes() == b"no es un archivo de eventos"
    assert tsd_reparar(tmp_path / "no_existe.tsd") == 0


def test_la_caja_del_estado_depende_del_codigo_y_de_la_sal():
    sal, otra_sal = bytes(range(16)), bytes(range(16, 32))
    mensaje = b'{"consentimiento": true}'
    caja = caja_estado("TS7K4P2", sal)

    assert caja_estado("TS7K4P2", sal).decrypt(caja.encrypt(mensaje)) == mensaje
    with pytest.raises(CryptoError):
        caja_estado("TS9X8Y1", sal).decrypt(caja.encrypt(mensaje))
    with pytest.raises(CryptoError):
        caja_estado("TS7K4P2", otra_sal).decrypt(caja.encrypt(mensaje))
    assert mensaje not in caja.encrypt(mensaje)
