"""Generador de códigos de acceso para alumnos en CSV y papeletas HTML imprimibles."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permitir ejecución directa como script
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from escritorio.alumnos import generar


PLANTILLA_HTML = """<!doctype html>
<html lang="es-MX">
<head>
  <meta charset="utf-8">
  <title>Papeletas de acceso · Tutor Socrático</title>
  <style>
    @page { size: letter; margin: 12mm; }
    body { font-family: system-ui, sans-serif; margin: 0; padding: 0; color: #111; }
    .rejilla {
      display: grid;
      grid-template-columns: repeat(3, 1fr);
      grid-auto-rows: 48mm;
      gap: 0;
    }
    .papeleta {
      border: 1px dashed #666;
      box-sizing: border-box;
      padding: 10px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      page-break-inside: avoid;
    }
    .titulo { font-size: 0.85rem; font-weight: 700; color: #0f6b5c; margin: 0; }
    .instruccion { font-size: 0.72rem; color: #444; margin: 4px 0; line-height: 1.25; }
    .codigo-caja {
      text-align: center;
      background: #f4f4f4;
      border: 1px solid #ccc;
      border-radius: 4px;
      padding: 6px 0;
      margin: 4px 0;
    }
    .codigo {
      font-family: ui-monospace, monospace;
      font-size: 1.25rem;
      font-weight: 800;
      letter-spacing: 0.08em;
    }
    .pie { font-size: 0.65rem; color: #666; text-align: right; margin: 0; }
  </style>
</head>
<body>
  <div class="rejilla">
__PAPELETAS__
  </div>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generador de códigos de alumnos para el Tutor Socrático")
    parser.add_argument("--n", type=int, default=60, help="Número de códigos a generar (por defecto 60)")
    parser.add_argument("--salida", type=str, default="salida/codigos",
                        help="Carpeta de salida para codigos.csv y codigos.html (por defecto salida/codigos)")
    parser.add_argument("--prefijo", type=str, default="TS", help="Prefijo del código (por defecto TS)")
    args = parser.parse_args(argv)

    carpeta = Path(args.salida).resolve()
    carpeta.mkdir(parents=True, exist_ok=True)

    codigos = generar(args.n, prefijo=args.prefijo)

    # 1. codigos.csv en UTF-8 con BOM
    ruta_csv = carpeta / "codigos.csv"
    with open(ruta_csv, "w", encoding="utf-8-sig", newline="") as f:
        f.write("codigo,alumno\r\n")
        for c in codigos:
            f.write(f"{c},\r\n")

    # 2. codigos.html
    bloques_html = []
    for c in codigos:
        bloque = f"""    <div class="papeleta">
      <div class="titulo">Tutor Socrático · Circuitos</div>
      <div class="instruccion">Conserva esta papeleta. Introduce tu código personal al abrir el tutor:</div>
      <div class="codigo-caja"><span class="codigo">{c}</span></div>
      <div class="pie">Uso personal · No transferible</div>
    </div>"""
        bloques_html.append(bloque)

    ruta_html = carpeta / "codigos.html"
    contenido_html = PLANTILLA_HTML.replace("__PAPELETAS__", "\n".join(bloques_html))
    ruta_html.write_text(contenido_html, encoding="utf-8")

    return 0


if __name__ == "__main__":
    sys.exit(main())
