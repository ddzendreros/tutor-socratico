"""Resolución de rutas de la aplicación y configuración de bitácoras."""
from __future__ import annotations

import ctypes
import logging
import os
import sys
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from escritorio.config import MODELO_ARCHIVO

if sys.platform == "win32":
    from ctypes import wintypes


def obtener_carpeta_documentos() -> Path:
    """Obtiene la carpeta Documentos del usuario vía SHGetKnownFolderPath o fallback."""
    if sys.platform == "win32":
        try:
            class GUID(ctypes.Structure):
                _fields_ = [
                    ("Data1", wintypes.DWORD),
                    ("Data2", wintypes.WORD),
                    ("Data3", wintypes.WORD),
                    ("Data4", wintypes.BYTE * 8),
                ]

            folderid_documents = uuid.UUID("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}").bytes_le
            guid = GUID.from_buffer_copy(folderid_documents)
            path_ptr = wintypes.LPWSTR()
            res = ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path_ptr))
            if res == 0 and path_ptr.value:
                ruta = Path(path_ptr.value)
                ctypes.windll.ole32.CoTaskMemFree(path_ptr)
                return ruta
        except Exception:
            pass
    return Path.home() / "Documents"


def obtener_carpeta_base() -> Path:
    """Devuelve la carpeta raíz de la aplicación según el modo de ejecución."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


RAIZ = obtener_carpeta_base()


def obtener_carpeta_app() -> Path:
    """Carpeta base de datos y configuración en LocalAppData."""
    base_local = os.environ.get("LOCALAPPDATA")
    if base_local:
        return Path(base_local) / "TutorSocratico"
    return Path.home() / "AppData" / "Local" / "TutorSocratico"


def obtener_carpeta_datos(carpeta_personalizada: str | Path | None = None) -> Path:
    """Carpeta para almacenamiento de datos de alumnos."""
    if carpeta_personalizada:
        return Path(carpeta_personalizada).resolve()
    return obtener_carpeta_app() / "datos"


def buscar_llama(base: Path | None = None) -> Path:
    """Localiza el ejecutable de llama-server."""
    raiz = base or RAIZ
    posibles = [
        raiz / "llama" / "llama-server.exe",
        raiz / "vendor" / "llama" / "llama-server.exe",
        raiz / "llama-server.exe",
    ]
    for p in posibles:
        if p.exists():
            return p.resolve()
    return posibles[0].resolve()


def buscar_modelo(base: Path | None = None, archivo: str = MODELO_ARCHIVO) -> Path | None:
    """Localiza el GGUF del modelo elegido; en vendor/modelos conviven otros de la evaluación."""
    raiz = base or RAIZ
    for carpeta in (raiz / "modelo", raiz / "vendor" / "modelos", raiz):
        ruta = carpeta / archivo
        if ruta.is_file():
            return ruta.resolve()
    return None


def configurar_logging(ruta_log: Path | None = None) -> logging.Logger:
    """Configura la bitácora técnica rotativa del sistema."""
    if ruta_log is None:
        carpeta_app = obtener_carpeta_app()
        carpeta_app.mkdir(parents=True, exist_ok=True)
        ruta_log = carpeta_app / "registro-app.log"
    else:
        ruta_log.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("tutor_socratico")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(
            ruta_log,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        formateador = logging.Formatter(
            "%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formateador)
        logger.addHandler(handler)

    return logger
