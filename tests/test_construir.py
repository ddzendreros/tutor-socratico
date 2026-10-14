"""Pruebas unitarias para las herramientas de construcción y diagnóstico."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
import pytest

from escritorio import __version__
from escritorio.config import MODELO_ARCHIVO
from tools.construir import (
    ARCHIVOS_LLAMA,
    armar_version_json,
    buscar_iscc,
    formato_tamano,
    paso_1_verificar_llave_y_pruebas,
    sha256_archivo,
)


def test_buscar_iscc_encuentra_o_falla_con_instruccion(monkeypatch):
    # Con ruta válida
    try:
        ruta = buscar_iscc()
        assert ruta.is_file()
        assert ruta.name.lower() == "iscc.exe"
    except FileNotFoundError as e:
        assert "winget install JRSoftware.InnoSetup" in str(e)

    # Simulando que no existe en ningún lado
    monkeypatch.setenv("LOCALAPPDATA", "")
    monkeypatch.setenv("ProgramFiles(x86)", "")
    monkeypatch.setenv("ProgramFiles", "")
    with patch("shutil.which", return_value=None):
        with pytest.raises(FileNotFoundError) as exc_info:
            buscar_iscc()
        assert "winget install JRSoftware.InnoSetup" in str(exc_info.value)


def test_lista_archivos_llama():
    # la lista se comprueba siempre: deben ser exactamente los 24 archivos requeridos
    assert len(ARCHIVOS_LLAMA) == 24
    assert "llama-server.exe" in ARCHIVOS_LLAMA
    assert "LICENSE-LLVM-OpenMP" in ARCHIVOS_LLAMA
    assert "llama.dll" in ARCHIVOS_LLAMA
    assert "llama-common.dll" in ARCHIVOS_LLAMA
    assert "llama-server-impl.dll" in ARCHIVOS_LLAMA
    assert "mtmd.dll" in ARCHIVOS_LLAMA
    assert "libomp.dll" in ARCHIVOS_LLAMA

    # Todos los ggml-cpu-*.dll necesarios
    cpus = ["alderlake", "cannonlake", "cascadelake", "cooperlake", "haswell", "icelake",
            "ivybridge", "piledriver", "sandybridge", "sapphirerapids", "skylakex", "sse42", "x64", "zen4"]
    for c in cpus:
        assert f"ggml-cpu-{c}.dll" in ARCHIVOS_LLAMA

    # Archivos no usados que NO deben estar
    assert "llama-b11429-bin-win-cpu-x64.zip" not in ARCHIVOS_LLAMA
    assert "llama-bench.exe" not in ARCHIVOS_LLAMA
    assert "llama-cli.exe" not in ARCHIVOS_LLAMA
    assert "ggml-rpc-server.exe" not in ARCHIVOS_LLAMA

    # vendor/ no está en git ni en la integración continua: sólo se exige donde existe
    base_llama = Path(__file__).resolve().parent.parent / "vendor" / "llama"
    if not base_llama.is_dir():
        pytest.skip("no hay vendor/llama en esta máquina")
    for nom in ARCHIVOS_LLAMA:
        assert (base_llama / nom).is_file(), f"Falta {nom} en vendor/llama"


def test_armar_version_json(tmp_path):
    dist_dir = tmp_path / "dist"
    mod_dir = dist_dir / "modelo"
    llama_dir = dist_dir / "llama"
    mod_dir.mkdir(parents=True)
    llama_dir.mkdir(parents=True)

    fake_mod = mod_dir / MODELO_ARCHIVO
    fake_mod.write_bytes(b"modelo_de_prueba_12345")
    fake_srv = llama_dir / "llama-server.exe"
    fake_srv.write_bytes(b"llama_server_de_prueba_67890")

    doc = armar_version_json(dist_dir, huella_llave="abc123def456")

    assert doc["version"] == __version__
    assert doc["llave_publica_huella"] == "abc123def456"
    assert doc["modelo"]["archivo"] == MODELO_ARCHIVO
    assert doc["modelo"]["sha256"] == sha256_archivo(fake_mod)
    assert doc["modelo"]["tamano_bytes"] == fake_mod.stat().st_size
    assert doc["llama_server"]["archivo"] == "llama-server.exe"
    assert doc["llama_server"]["sha256"] == sha256_archivo(fake_srv)
    assert doc["llama_server"]["tamano_bytes"] == fake_srv.stat().st_size
    assert "fecha_construccion" in doc


def test_formato_tamano():
    assert formato_tamano(500) == "500.00 B"
    assert formato_tamano(1024) == "1.00 KB"
    assert formato_tamano(1048576) == "1.00 MB"
    assert formato_tamano(1073741824) == "1.00 GB"


def test_paso_1_falta_llave(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.construir.RAIZ", tmp_path)
    with pytest.raises(SystemExit) as exc:
        paso_1_verificar_llave_y_pruebas(saltar_pruebas=True)
    assert exc.value.code == 1
