"""Registro de eventos del alumno: sobre común, secuencia por arranque y almacenamiento.

El esquema está en docs/DATOS.md. Quien decide si un alumno se registra es la API: aquí sólo
se escribe lo que llega.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from escritorio.alumnos import formatear, normalizar
from escritorio.rutas import obtener_carpeta_datos
from tutor.privacidad import scrub

logger = logging.getLogger("tutor_socratico.registro")

ESQUEMA_VERSION = 1


def scrub_datos(obj: Any) -> Any:
    """Aplica scrub() a todas las cadenas de una estructura."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [scrub_datos(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_datos(v) for k, v in obj.items()}
    return obj


class Almacen:
    """Eventos de cada alumno en `datos/<código>/eventos.jsonl`, una línea por evento."""

    def __init__(self, carpeta_datos: str | Path | None = None):
        self.carpeta_datos = obtener_carpeta_datos(carpeta_datos)

    def ruta_eventos(self, codigo_norm: str) -> Path:
        return self.carpeta_datos / codigo_norm / "eventos.jsonl"

    def agregar(self, codigo_norm: str, eventos: list[dict]) -> None:
        """Agrega un lote de eventos con una sola escritura a disco."""
        ruta = self.ruta_eventos(codigo_norm)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        texto = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eventos)
        with open(ruta, "a", encoding="utf-8", newline="\n") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())

    def leer(self, codigo_norm: str) -> list[dict]:
        ruta = self.ruta_eventos(codigo_norm)
        if not ruta.exists():
            return []
        return [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines() if linea.strip()]


def leer_instalacion(carpeta_datos: Path) -> str:
    """Identificador de esta instalación (`datos/instalacion.json`); se crea la primera vez."""
    ruta = carpeta_datos / "instalacion.json"
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if isinstance(datos, dict) and datos.get("instalacion"):
            return str(datos["instalacion"])
    except (OSError, ValueError):
        pass
    nueva = str(uuid.uuid4())
    try:
        carpeta_datos.mkdir(parents=True, exist_ok=True)
        ruta.write_text(json.dumps({"instalacion": nueva, "creado": datetime.now().astimezone().isoformat()}),
                        encoding="utf-8")
    except OSError as e:
        logger.error("No se pudo guardar instalacion.json: %s", e)
    return nueva


class Registro:
    """Pone el sobre a cada evento y lo guarda.

    `seq` es contiguo por alumno dentro de un arranque: así el lector detecta huecos aunque varios
    alumnos usen el tutor sin cerrarlo. `mono` cuenta ms desde el arranque con el reloj monotónico.
    """

    def __init__(self, carpeta_datos: str | Path | None = None, almacen: Almacen | None = None):
        self.carpeta_datos = obtener_carpeta_datos(carpeta_datos)
        self.almacen = almacen or Almacen(self.carpeta_datos)
        self.instalacion = leer_instalacion(self.carpeta_datos)
        self.arranque = str(uuid.uuid4())
        self._t0 = time.monotonic()
        self._seq: dict[str, int] = {}
        self._lock = threading.Lock()

    def mono_ahora(self) -> float:
        return round((time.monotonic() - self._t0) * 1000, 1)

    def registrar(self, codigo: str, tipo: str, datos: dict, intento: str | None = None,
                  origen: str = "app") -> dict:
        return self.registrar_varios(codigo, [{"tipo": tipo, "datos": datos, "intento": intento,
                                               "origen": origen}])[0]

    def registrar_varios(self, codigo: str, eventos: list[dict]) -> list[dict]:
        """Cada evento: {tipo, datos, intento, origen, ms?, mono?}. Sin `ms`/`mono`, la hora actual."""
        codigo_norm = normalizar(codigo)
        sobres = []
        # la secuencia y la escritura van juntas: el archivo queda en el orden de `seq`
        with self._lock:
            for ev in eventos:
                ms = ev.get("ms")
                if ms is None:
                    ms = time.time() * 1000
                mono = ev.get("mono")
                self._seq[codigo_norm] = self._seq.get(codigo_norm, 0) + 1
                sobres.append({
                    "v": ESQUEMA_VERSION,
                    "seq": self._seq[codigo_norm],
                    "t": datetime.fromtimestamp(ms / 1000).astimezone().isoformat(timespec="milliseconds"),
                    "ms": round(ms),
                    "mono": self.mono_ahora() if mono is None else round(mono, 1),
                    "codigo": formatear(codigo_norm),
                    "instalacion": self.instalacion,
                    "arranque": self.arranque,
                    "intento": ev.get("intento"),
                    "origen": ev.get("origen", "app"),
                    "tipo": ev["tipo"],
                    "datos": scrub_datos(ev.get("datos") or {}),
                })
            try:
                self.almacen.agregar(codigo_norm, sobres)
            except OSError as e:
                logger.error("No se pudieron guardar %d eventos de %s: %s", len(sobres), codigo_norm, e)
        return sobres
