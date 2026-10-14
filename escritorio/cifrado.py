"""Cifrado de los datos del alumno (PyNaCl).

- Eventos y manifiestos: `SealedBox` con la llave pública del equipo de investigación. El programa
  sólo tiene la pública: puede cifrar, pero no leer lo que cifró.
- Llave privada: archivo `tutor-llave-1`, cifrado con una llave derivada de la contraseña (argon2id).
- Estado del alumno: `SecretBox` con una llave derivada de su código y la sal de la instalación.
- `eventos.tsd`: encabezado `TSD1` y lotes `[uint32 LE longitud][lote sellado]`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import struct
import zlib
from pathlib import Path

import nacl.exceptions
import nacl.pwhash.argon2id as argon2id
from nacl.public import PrivateKey, PublicKey, SealedBox
from nacl.secret import SecretBox

FORMATO_LLAVE = "tutor-llave-1"
TSD_MAGIC = b"TSD1"
_LONGITUD = struct.Struct("<I")


# ------------------------------------------------------------------ llaves

def generar_llaves() -> tuple[PrivateKey, PublicKey]:
    privada = PrivateKey.generate()
    return privada, privada.public_key


def huella_publica(publica: PublicKey) -> str:
    """SHA-256 corto de la llave pública; identifica la llave en los paquetes."""
    return hashlib.sha256(bytes(publica)).hexdigest()[:12]


def guardar_llave_privada(privada: PrivateKey, contrasena: str, ruta: Path) -> None:
    sal = os.urandom(argon2id.SALTBYTES)
    opslimit, memlimit = argon2id.OPSLIMIT_MODERATE, argon2id.MEMLIMIT_MODERATE
    caja = SecretBox(argon2id.kdf(SecretBox.KEY_SIZE, contrasena.encode("utf-8"), sal,
                                  opslimit=opslimit, memlimit=memlimit))
    doc = {
        "formato": FORMATO_LLAVE,
        "huella": huella_publica(privada.public_key),
        "sal": base64.b64encode(sal).decode(),
        "opslimit": opslimit,
        "memlimit": memlimit,
        "cifrado": base64.b64encode(caja.encrypt(bytes(privada))).decode(),
    }
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def cargar_llave_privada(ruta: Path, contrasena: str) -> PrivateKey:
    """Lanza ValueError con un mensaje para el usuario si el archivo o la contraseña no sirven."""
    try:
        doc = json.loads(Path(ruta).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ValueError(f"No se pudo leer la llave {ruta}: {e}") from e
    if not isinstance(doc, dict) or doc.get("formato") != FORMATO_LLAVE:
        raise ValueError(f"{ruta} no es una llave privada del tutor ({FORMATO_LLAVE}).")
    caja = SecretBox(argon2id.kdf(SecretBox.KEY_SIZE, contrasena.encode("utf-8"), base64.b64decode(doc["sal"]),
                                  opslimit=doc["opslimit"], memlimit=doc["memlimit"]))
    try:
        privada = PrivateKey(caja.decrypt(base64.b64decode(doc["cifrado"])))
    except nacl.exceptions.CryptoError as e:
        raise ValueError("La contraseña no corresponde a esta llave.") from e
    if huella_publica(privada.public_key) != doc.get("huella"):
        raise ValueError("La llave está dañada: su huella no corresponde.")
    return privada


def guardar_llave_publica(publica: PublicKey, ruta: Path) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(base64.b64encode(bytes(publica)).decode() + "\n", encoding="utf-8")


def leer_llave_publica(ruta: Path) -> PublicKey:
    return PublicKey(base64.b64decode(Path(ruta).read_text(encoding="utf-8").strip()))


# ------------------------------------------------------------------ sellar y abrir

def sellar(datos: bytes, publica: PublicKey) -> bytes:
    return SealedBox(publica).encrypt(zlib.compress(datos))


def abrir_sellado(blob: bytes, privada: PrivateKey) -> bytes:
    return zlib.decompress(SealedBox(privada).decrypt(blob))


# ------------------------------------------------------------------ estado del alumno

def caja_estado(codigo_norm: str, sal: bytes) -> SecretBox:
    """Llave del estado de un alumno. Tarda unas decenas de ms: se deriva una vez por sesión."""
    return SecretBox(argon2id.kdf(SecretBox.KEY_SIZE, codigo_norm.encode("utf-8"), sal,
                                  opslimit=argon2id.OPSLIMIT_INTERACTIVE,
                                  memlimit=argon2id.MEMLIMIT_INTERACTIVE))


# ------------------------------------------------------------------ eventos.tsd

def tsd_agregar(ruta: Path, lotes: list[bytes]) -> None:
    """Agrega lotes sellados al archivo (lo crea con su encabezado) con una sola escritura a disco."""
    nuevo = not ruta.exists()
    datos = (TSD_MAGIC if nuevo else b"") + b"".join(_LONGITUD.pack(len(lote)) + lote for lote in lotes)
    with open(ruta, "ab") as f:
        f.write(datos)
        f.flush()
        os.fsync(f.fileno())


def tsd_reparar(ruta: Path) -> int:
    """Corta un lote que quedó a medias al final (el programa se cerró mientras escribía); si se
    dejara, lo que se agregue después ya no se podría leer. Devuelve los bytes que quitó."""
    if not ruta.exists():
        return 0
    datos = ruta.read_bytes()
    if not datos.startswith(TSD_MAGIC):
        return 0
    _, sobrante = tsd_lotes(datos)
    if sobrante:
        with open(ruta, "r+b") as f:
            f.truncate(len(datos) - sobrante)
    return sobrante


def tsd_lotes(datos: bytes) -> tuple[list[bytes], int]:
    """Lotes de un `eventos.tsd` y cuántos bytes sobran al final (un lote que quedó a medias)."""
    if not datos.startswith(TSD_MAGIC):
        raise ValueError("El archivo de eventos no empieza con TSD1.")
    lotes, pos = [], len(TSD_MAGIC)
    while pos + _LONGITUD.size <= len(datos):
        (n,) = _LONGITUD.unpack_from(datos, pos)
        if pos + _LONGITUD.size + n > len(datos):
            break
        lotes.append(datos[pos + _LONGITUD.size:pos + _LONGITUD.size + n])
        pos += _LONGITUD.size + n
    return lotes, len(datos) - pos
