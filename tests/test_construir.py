"""Pruebas unitarias para las herramientas de construcción y diagnóstico."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch
import pytest

from escritorio import __version__, equipo
from escritorio.config import MODELO_ARCHIVO
from tools import construir
from tools.construir import (
    ARCHIVOS_LLAMA,
    armar_version_json,
    buscar_iscc,
    estado_git,
    formato_tamano,
    paso_0_verificar_repositorio,
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


# ------------------------------------------------------------------ commit de la construcción

def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-c", "user.name=prueba", "-c", "user.email=prueba@ejemplo.org", *args],
                          cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("ignorado.txt\n", encoding="utf-8")
    (repo / "a.txt").write_text("uno", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "inicial")
    return repo


def test_estado_git(repo, tmp_path):
    commit = _git(repo, "rev-parse", "HEAD")
    assert estado_git(repo) == (commit, [])
    (repo / "ignorado.txt").write_text("llave", encoding="utf-8")
    assert estado_git(repo) == (commit, [])
    (repo / "a.txt").write_text("dos", encoding="utf-8")
    (repo / "nuevo.txt").write_text("x", encoding="utf-8")
    assert sorted(estado_git(repo)[1]) == [" M a.txt", "?? nuevo.txt"]
    fuera = tmp_path / "fuera"
    fuera.mkdir()
    assert estado_git(fuera) == (None, [])


def test_no_construye_con_cambios_sin_commit(repo, monkeypatch, capsys):
    monkeypatch.setattr(construir, "RAIZ", repo)
    commit = _git(repo, "rev-parse", "HEAD")
    assert paso_0_verificar_repositorio() == (commit, False)

    (repo / "a.txt").write_text("dos", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        paso_0_verificar_repositorio()
    assert exc.value.code == 1 and "a.txt" in capsys.readouterr().out
    assert paso_0_verificar_repositorio(permitir_cambios=True) == (commit, True)


def test_el_commit_viaja_en_version_json_y_en_los_datos(tmp_path):
    dist_dir = tmp_path / "dist"
    (dist_dir / "modelo").mkdir(parents=True)
    (dist_dir / "llama").mkdir()
    (dist_dir / "modelo" / MODELO_ARCHIVO).write_bytes(b"m")
    (dist_dir / "llama" / "llama-server.exe").write_bytes(b"s")
    commit = "c982fd9" + "0" * 33

    doc = armar_version_json(dist_dir, "abc", commit=commit, cambios_sin_commit=False)
    assert (doc["commit"], doc["cambios_sin_commit"]) == (commit, False)
    (dist_dir / "version.json").write_text(json.dumps(doc), encoding="utf-8")
    assert equipo.construccion(dist_dir) == commit[:12]

    doc = armar_version_json(dist_dir, "abc", commit=commit, cambios_sin_commit=True)
    (dist_dir / "version.json").write_text(json.dumps(doc), encoding="utf-8")
    assert equipo.construccion(dist_dir) == commit[:12] + "+cambios"

    assert equipo.construccion(tmp_path) is None
    (tmp_path / "version.json").write_text("{dañado", encoding="utf-8")
    assert equipo.construccion(tmp_path) is None
