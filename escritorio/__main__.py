"""Punto de ejecución del paquete de escritorio."""
from __future__ import annotations

import argparse
import sys

from escritorio.ventana import arrancar_aplicacion


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tutor Socrático - Aplicación de Escritorio")
    parser.add_argument("--navegador", action="store_true", help="Fuerza apertura en navegador predeterminado")
    parser.add_argument("--sin-modelo", action="store_true", help="Ejecuta únicamente con motor y plantillas base")
    parser.add_argument("--datos", type=str, default=None, help="Carpeta personalizada para datos de alumnos")
    parser.add_argument("--puerto", type=int, default=0, help="Puerto TCP para el servidor local (0 para automático)")
    args = parser.parse_args(argv)

    return arrancar_aplicacion(
        forzar_navegador=args.navegador,
        sin_modelo=args.sin_modelo,
        carpeta_datos=args.datos,
        puerto=args.puerto,
    )


if __name__ == "__main__":
    sys.exit(main())
