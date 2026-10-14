"""Registro de eventos del alumno: sobre común, secuencia por arranque y almacenamiento cifrado.

El esquema está en docs/DATOS.md. Quien decide si un alumno se registra es la API: aquí sólo
se escribe lo que llega.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import nacl.pwhash.argon2id as argon2id
from nacl.public import PrivateKey, PublicKey

from escritorio.alumnos import formatear, normalizar
from escritorio.cifrado import abrir_sellado, sellar, tsd_agregar, tsd_lotes, tsd_reparar
from escritorio.rutas import obtener_carpeta_datos
from tutor.privacidad import scrub

logger = logging.getLogger("tutor_socratico.registro")

ESQUEMA_VERSION = 1
# eventos por lote sellado
LOTE_MAX = 50


def scrub_datos(obj: Any) -> Any:
    """Aplica scrub() a todas las cadenas de una estructura."""
    if isinstance(obj, str):
        return scrub(obj)
    if isinstance(obj, list):
        return [scrub_datos(x) for x in obj]
    if isinstance(obj, dict):
        return {k: scrub_datos(v) for k, v in obj.items()}
    return obj


def _escribir_json(ruta: Path, obj: dict) -> None:
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, ruta)


class Almacen:
    """Eventos de cada alumno en `datos/<código>/eventos.tsd`, sellados con la llave pública.

    Junto a él, `eventos.idx` lleva en claro, por arranque, el último `seq` y los lotes escritos: el
    programa no puede leer sus propios eventos, y el manifiesto de la exportación los necesita.
    """

    def __init__(self, carpeta_datos: Path, publica: PublicKey):
        self.carpeta_datos = carpeta_datos
        self.publica = publica
        self._revisados: set[str] = set()

    def ruta_eventos(self, codigo_norm: str) -> Path:
        return self.carpeta_datos / codigo_norm / "eventos.tsd"

    def ruta_indice(self, codigo_norm: str) -> Path:
        return self.carpeta_datos / codigo_norm / "eventos.idx"

    def _revisar(self, codigo_norm: str) -> None:
        """La primera vez en este arranque: aparta un archivo ilegible y corta un final a medias."""
        ruta = self.ruta_eventos(codigo_norm)
        if ruta.exists() and not ruta.read_bytes().startswith(b"TSD1"):
            apartado = ruta.with_name(f"eventos-ilegible-{int(time.time())}.tsd")
            logger.error("eventos.tsd de %s no tiene encabezado; se aparta como %s", codigo_norm, apartado.name)
            os.replace(ruta, apartado)
        quitados = tsd_reparar(ruta)
        if quitados:
            logger.warning("eventos.tsd de %s terminaba con un lote incompleto (%d bytes); se quitó",
                           codigo_norm, quitados)
        self._revisados.add(codigo_norm)

    def agregar(self, codigo_norm: str, eventos: list[dict]) -> None:
        ruta = self.ruta_eventos(codigo_norm)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if codigo_norm not in self._revisados:
            self._revisar(codigo_norm)
        lotes = []
        for i in range(0, len(eventos), LOTE_MAX):
            texto = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in eventos[i:i + LOTE_MAX])
            lotes.append(sellar(texto.encode("utf-8"), self.publica))
        try:
            tsd_agregar(ruta, lotes)
        except OSError:
            # si la escritura quedó a medias, la siguiente vuelve a revisar el archivo
            self._revisados.discard(codigo_norm)
            raise
        indice = self.leer_indice(codigo_norm)
        for i, e in enumerate(eventos):
            rango = indice.setdefault(e["arranque"], {"seq_hasta": 0, "lotes": 0})
            rango["seq_hasta"] = max(rango["seq_hasta"], e["seq"])
            rango["lotes"] += i % LOTE_MAX == 0
        _escribir_json(self.ruta_indice(codigo_norm), indice)

    def leer_indice(self, codigo_norm: str) -> dict:
        try:
            indice = json.loads(self.ruta_indice(codigo_norm).read_text(encoding="utf-8"))
            return indice if isinstance(indice, dict) else {}
        except (OSError, ValueError):
            return {}


def leer_eventos(datos_tsd: bytes, privada: PrivateKey) -> list[dict]:
    """Los eventos de un `eventos.tsd`. Sólo funciona con la llave privada (el lector, las pruebas)."""
    lotes, _ = tsd_lotes(datos_tsd)
    eventos = []
    for lote in lotes:
        eventos += [json.loads(linea) for linea in abrir_sellado(lote, privada).decode("utf-8").splitlines()]
    return eventos


def leer_instalacion(carpeta_datos: Path) -> tuple[str, bytes]:
    """Identificador y sal de esta instalación (`datos/instalacion.json`); se crean la primera vez.
    A un archivo sin sal se le agrega sin cambiar el identificador."""
    ruta = carpeta_datos / "instalacion.json"
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
        if not (isinstance(datos, dict) and datos.get("instalacion")):
            raise ValueError("sin identificador")
    except (OSError, ValueError):
        datos = {"instalacion": str(uuid.uuid4()), "creado": datetime.now().astimezone().isoformat()}
    if not datos.get("sal"):
        datos["sal"] = base64.b64encode(os.urandom(argon2id.SALTBYTES)).decode()
        try:
            carpeta_datos.mkdir(parents=True, exist_ok=True)
            _escribir_json(ruta, datos)
        except OSError as e:
            logger.error("No se pudo guardar instalacion.json: %s", e)
    return str(datos["instalacion"]), base64.b64decode(datos["sal"])


class Registro:
    """Pone el sobre a cada evento y lo guarda.

    `seq` es contiguo por alumno dentro de un arranque: así el lector detecta huecos aunque varios
    alumnos usen el tutor sin cerrarlo. `mono` cuenta ms desde el arranque con el reloj monotónico.
    Sin llave pública no se registra nada: nunca se escriben eventos en claro.
    """

    def __init__(self, carpeta_datos: str | Path | None = None, publica: PublicKey | None = None):
        self.carpeta_datos = obtener_carpeta_datos(carpeta_datos)
        self.almacen = Almacen(self.carpeta_datos, publica) if publica is not None else None
        self.instalacion, self.sal = leer_instalacion(self.carpeta_datos)
        self.arranque = str(uuid.uuid4())
        self._t0 = time.monotonic()
        self._seq: dict[str, int] = {}
        # quien lea eventos.tsd (como la exportación) lo toma para no leer una escritura a medias
        self.escritura = threading.Lock()

    @property
    def activo(self) -> bool:
        return self.almacen is not None

    def mono_ahora(self) -> float:
        return round((time.monotonic() - self._t0) * 1000, 1)

    def ultimo_seq(self, codigo: str) -> int:
        return self._seq.get(normalizar(codigo), 0)

    def registrar(self, codigo: str, tipo: str, datos: dict, intento: str | None = None,
                  origen: str = "app") -> dict | None:
        sobres = self.registrar_varios(codigo, [{"tipo": tipo, "datos": datos, "intento": intento,
                                                 "origen": origen}])
        return sobres[0] if sobres else None

    def registrar_varios(self, codigo: str, eventos: list[dict]) -> list[dict]:
        """Cada evento: {tipo, datos, intento, origen, ms?, mono?}. Sin `ms`/`mono`, la hora actual."""
        if self.almacen is None:
            return []
        codigo_norm = normalizar(codigo)
        sobres = []
        # la secuencia y la escritura van juntas: el archivo queda en el orden de `seq`
        with self.escritura:
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
