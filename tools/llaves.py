"""Llaves del estudio: genera el par y verifica la contraseña de la llave privada.

Uso:
  python -m tools.llaves generar --salida <carpeta>
  python -m tools.llaves verificar <llave_privada.tsk>
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

# permite ejecutarlo directamente como script
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from escritorio.cifrado import (  # noqa: E402
    cargar_llave_privada,
    generar_llaves,
    guardar_llave_privada,
    guardar_llave_publica,
    huella_publica,
)


def _error(mensaje: str) -> int:
    print(f"Error: {mensaje}", file=sys.stderr)
    return 1


def cmd_generar(salida: Path) -> int:
    salida = salida.resolve()
    if salida == _RAIZ or _RAIZ in salida.parents:
        return _error("Guarda la llave fuera del repositorio.")
    ruta_privada, ruta_publica = salida / "llave_privada.tsk", salida / "llave_publica.txt"
    if ruta_privada.exists():
        return _error(f"ya hay una llave privada en {ruta_privada}; si se reemplaza, "
                      "los paquetes cifrados con ella no se podrán abrir. Usa otra carpeta.")
    contrasena = getpass.getpass("Contraseña de la llave privada: ")
    if not contrasena:
        return _error("la contraseña no puede estar vacía.")
    if getpass.getpass("Repite la contraseña: ") != contrasena:
        return _error("las contraseñas no coinciden.")

    privada, publica = generar_llaves()
    guardar_llave_privada(privada, contrasena, ruta_privada)
    guardar_llave_publica(publica, ruta_publica)
    print(f"Huella de la llave: {huella_publica(publica)}")
    print(f"  Llave privada: {ruta_privada}")
    print(f"  Llave pública: {ruta_publica}")
    print("Copia llave_publica.txt a escritorio/ antes de construir la aplicación.")
    print("Guarda la llave privada y su contraseña donde sólo las tenga el equipo de investigación.")
    return 0


def cmd_verificar(ruta: Path) -> int:
    try:
        privada = cargar_llave_privada(ruta, getpass.getpass(f"Contraseña de {ruta.name}: "))
    except ValueError as e:
        return _error(str(e))
    print(f"La contraseña es correcta. Huella de la llave: {huella_publica(privada.public_key)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.llaves", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="orden", required=True)
    generar = sub.add_parser("generar", help="crea un par de llaves nuevo")
    generar.add_argument("--salida", type=Path, required=True, help="carpeta donde se guardan las llaves")
    verificar = sub.add_parser("verificar", help="comprueba la contraseña de una llave privada")
    verificar.add_argument("llave", type=Path, help="llave_privada.tsk")
    args = parser.parse_args(argv)
    return cmd_generar(args.salida) if args.orden == "generar" else cmd_verificar(args.llave)


if __name__ == "__main__":
    sys.exit(main())
