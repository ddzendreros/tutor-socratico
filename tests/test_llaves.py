"""Pruebas de tools/llaves.py: sólo escriben en carpetas temporales."""
from __future__ import annotations

import getpass
from pathlib import Path

from escritorio.cifrado import cargar_llave_privada, huella_publica, leer_llave_publica
from tools import llaves


def test_generar_y_verificar(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(getpass, "getpass", lambda *_: "contraseña de prueba")
    assert llaves.main(["generar", "--salida", str(tmp_path / "llaves")]) == 0
    huella = huella_publica(leer_llave_publica(tmp_path / "llaves" / "llave_publica.txt"))
    assert huella in capsys.readouterr().out
    privada = cargar_llave_privada(tmp_path / "llaves" / "llave_privada.tsk", "contraseña de prueba")
    assert huella_publica(privada.public_key) == huella

    assert llaves.main(["verificar", str(tmp_path / "llaves" / "llave_privada.tsk")]) == 0
    assert huella in capsys.readouterr().out
    monkeypatch.setattr(getpass, "getpass", lambda *_: "otra")
    assert llaves.main(["verificar", str(tmp_path / "llaves" / "llave_privada.tsk")]) == 1
    assert "contraseña" in capsys.readouterr().err


def test_las_contrasenas_deben_coincidir(tmp_path: Path, monkeypatch, capsys):
    respuestas = iter(["una", "otra"])
    monkeypatch.setattr(getpass, "getpass", lambda *_: next(respuestas))
    assert llaves.main(["generar", "--salida", str(tmp_path / "llaves")]) == 1
    assert "no coinciden" in capsys.readouterr().err and not (tmp_path / "llaves").exists()


def test_no_guarda_la_llave_dentro_del_repositorio(monkeypatch, capsys):
    monkeypatch.setattr(getpass, "getpass", lambda *_: "x")
    destino = llaves._RAIZ / "llaves_de_prueba"
    assert llaves.main(["generar", "--salida", str(destino)]) == 1
    assert "Guarda la llave fuera del repositorio." in capsys.readouterr().err and not destino.exists()


def test_no_sobrescribe_una_llave_existente(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setattr(getpass, "getpass", lambda *_: "contraseña de prueba")
    assert llaves.main(["generar", "--salida", str(tmp_path)]) == 0
    antes = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    capsys.readouterr()
    assert llaves.main(["generar", "--salida", str(tmp_path)]) == 1
    assert "ya hay una llave privada" in capsys.readouterr().err
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == antes
