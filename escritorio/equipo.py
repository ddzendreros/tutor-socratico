"""Datos del equipo y versiones de lo que usa el tutor, para el registro."""
from __future__ import annotations

import hashlib
import json
import logging
import platform
import sys
import threading
from pathlib import Path

import psutil

from escritorio import __version__
from escritorio.conversacion import PROMPTS_HASH
from escritorio.rutas import RAIZ, obtener_carpeta_app

logger = logging.getLogger("tutor_socratico.equipo")

_WEBVIEW2 = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"

_banco_hash: str | None = None
_huellas: dict[str, str] = {}
_lock_huellas = threading.Lock()


def version_webview2() -> str | None:
    """Versión del runtime de WebView2, o None si no está. Sin él, pywebview no falla: cae en silencio
    a MSHTML (Internet Explorer), donde la interfaz no funciona."""
    if sys.platform != "win32":
        return None
    import winreg
    claves = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2}"),
    ]
    for raiz, ruta in claves:
        try:
            with winreg.OpenKey(raiz, ruta) as clave:
                version, _ = winreg.QueryValueEx(clave, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return str(version)
    return None


def nombre_cpu() -> str:
    """`ProcessorNameString` del registro de Windows (sólo lectura)."""
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as clave:
                return str(winreg.QueryValueEx(clave, "ProcessorNameString")[0]).strip()
        except OSError:
            pass
    return platform.processor()


def info_equipo(carpeta_datos: Path, modo: str, pantalla: dict | None, variante_cpu: str) -> dict:
    mem = psutil.virtual_memory()
    try:
        libre = round(psutil.disk_usage(str(carpeta_datos)).free / 1024**3, 1)
    except OSError:
        libre = None
    w = sys.getwindowsversion() if hasattr(sys, "getwindowsversion") else None
    return {
        "so": platform.system(),
        "version_so": platform.release(),
        "build": w.build if w else None,
        "plataforma": platform.platform(),
        "cpu": nombre_cpu(),
        "nucleos_fisicos": psutil.cpu_count(logical=False),
        "nucleos_logicos": psutil.cpu_count(logical=True),
        "ram_total_gb": round(mem.total / 1024**3, 1),
        "ram_disponible_gb": round(mem.available / 1024**3, 1),
        "disco_libre_gb": libre,
        "pantalla": pantalla,
        "webview2": version_webview2(),
        "modo": modo,
        "variante_cpu": variante_cpu or None,
    }


def banco_hash() -> str:
    """SHA-256 corto de problemas/*.txt y data/banco_errores.csv."""
    global _banco_hash
    if _banco_hash is None:
        h = hashlib.sha256()
        for ruta in sorted((RAIZ / "problemas").glob("*.txt")) + [RAIZ / "data" / "banco_errores.csv"]:
            h.update(ruta.name.encode("utf-8"))
            h.update(ruta.read_bytes())
        _banco_hash = h.hexdigest()[:12]
    return _banco_hash


def _ruta_huellas() -> Path:
    return obtener_carpeta_app() / "huellas.json"


def _clave(ruta: Path) -> str:
    st = ruta.stat()
    return f"{ruta.resolve()}|{st.st_size}|{st.st_mtime_ns}"


def huella_modelo(ruta: Path) -> str | None:
    """SHA-256 corto del modelo. Leer 3 GB toma de segundos a minutos según el disco, así que se
    calcula una sola vez por archivo (se guarda con su tamaño y fecha) y nunca durante una petición."""
    try:
        clave = _clave(ruta)
    except OSError:
        return None
    with _lock_huellas:
        if clave in _huellas:
            return _huellas[clave]
        try:
            guardadas = json.loads(_ruta_huellas().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            guardadas = {}
        if isinstance(guardadas, dict) and isinstance(guardadas.get(clave), str):
            _huellas[clave] = guardadas[clave]
            return _huellas[clave]
    h = hashlib.sha256()
    try:
        with open(ruta, "rb") as f:
            while bloque := f.read(4 * 1024 * 1024):
                h.update(bloque)
    except OSError as e:
        logger.error("No se pudo leer el modelo para su huella: %s", e)
        return None
    with _lock_huellas:
        _huellas[clave] = h.hexdigest()[:12]
        try:
            _ruta_huellas().parent.mkdir(parents=True, exist_ok=True)
            _ruta_huellas().write_text(json.dumps({clave: _huellas[clave]}), encoding="utf-8")
        except OSError as e:
            logger.warning("No se pudo guardar la huella del modelo: %s", e)
        return _huellas[clave]


def huella_conocida(ruta: Path | None) -> str | None:
    """La huella ya calculada, sin leer el archivo."""
    if ruta is None:
        return None
    try:
        clave = _clave(ruta)
    except OSError:
        return None
    with _lock_huellas:
        return _huellas.get(clave)


def construccion(raiz: Path | None = None) -> str | None:
    """Commit con el que se construyó el programa instalado (de version.json); None al correr desde el código."""
    try:
        doc = json.loads(((raiz or RAIZ) / "version.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    commit = doc.get("commit")
    if not commit:
        return None
    return commit[:12] + ("+cambios" if doc.get("cambios_sin_commit") else "")


def versiones(ruta_modelo: Path | None) -> dict:
    return {
        "app": __version__,
        "construccion": construccion(),
        "banco_hash": banco_hash(),
        "prompts_hash": PROMPTS_HASH,
        "modelo": ruta_modelo.name if ruta_modelo else None,
        "modelo_sha256": huella_conocida(ruta_modelo),
    }
