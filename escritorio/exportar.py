"""Exportación de los datos de un alumno a un .zip que sólo abre el equipo de investigación.

El .zip lleva una copia completa de `eventos.tsd`, así que cada exportación incluye las anteriores.
"""
from __future__ import annotations

import hashlib
import json
import os
import zipfile
from datetime import datetime
from pathlib import Path

from nacl.public import PublicKey

from escritorio.alumnos import formatear
from escritorio.cifrado import huella_publica, sellar, tsd_lotes

FORMATO_PAQUETE = "tutor-paquete-1"

LEEME = """Datos del Tutor socrático de Circuitos Eléctricos

Este archivo contiene tus datos de uso del tutor, cifrados. Sólo el equipo de investigación
puede abrirlo; ni tú ni nadie más puede leerlo sin su llave.

Súbelo a la carpeta que te indicó tu profesor. Si exportas otra vez, el archivo nuevo incluye
todo lo anterior: basta con subir el más reciente.
"""


class SinDatos(Exception):
    """El alumno todavía no tiene eventos registrados."""


def exportar(carpeta_datos: Path, codigo_norm: str, instalacion: str, publica: PublicKey,
             versiones: dict, carpeta_salida: Path) -> dict:
    """Escribe `<CÓDIGO>_<AAAAMMDD-HHMMSS>.zip` y devuelve {ruta, lotes, bytes}."""
    ruta_tsd = carpeta_datos / codigo_norm / "eventos.tsd"
    try:
        datos_tsd = ruta_tsd.read_bytes()
    except FileNotFoundError as e:
        raise SinDatos(codigo_norm) from e
    lotes, sobrante = tsd_lotes(datos_tsd)
    try:
        indice = json.loads((ruta_tsd.with_name("eventos.idx")).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        indice = {}

    ahora = datetime.now().astimezone()
    manifiesto = {
        "formato": FORMATO_PAQUETE,
        "codigo": formatear(codigo_norm),
        "instalacion": instalacion,
        "versiones": versiones,
        "exportado_en": ahora.isoformat(timespec="milliseconds"),
        "lotes": len(lotes),
        "bytes_incompletos": sobrante,
        "rangos": [{"arranque": a, "seq_desde": 1, **r} for a, r in sorted(indice.items())],
        "sha256_eventos": hashlib.sha256(datos_tsd).hexdigest(),
    }
    paquete = {"formato": FORMATO_PAQUETE, "huella_llave": huella_publica(publica)}

    carpeta_salida.mkdir(parents=True, exist_ok=True)
    nombre = f"{formatear(codigo_norm)}_{ahora:%Y%m%d-%H%M%S}"
    ruta = carpeta_salida / f"{nombre}.zip"
    n = 2
    while ruta.exists():
        ruta = carpeta_salida / f"{nombre}-{n}.zip"
        n += 1
    # se escribe aparte y se renombra: nunca queda en la carpeta un .zip a medias
    tmp = ruta.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:
        zf.writestr("LEEME.txt", LEEME)
        zf.writestr("paquete.json", json.dumps(paquete, indent=2))
        zf.writestr("manifiesto.tsd", sellar(json.dumps(manifiesto, ensure_ascii=False).encode("utf-8"), publica))
        zf.writestr("eventos.tsd", datos_tsd)
    os.replace(tmp, ruta)
    return {"ruta": ruta, "lotes": len(lotes), "bytes": ruta.stat().st_size}
