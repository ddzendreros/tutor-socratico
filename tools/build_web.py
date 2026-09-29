"""Arma el sitio estático en site/: la página, el motor en Python y el banco.

    python tools/build_web.py

El motor se empaqueta en site/bundle.json y el navegador lo ejecuta con
Pyodide, así que el sitio se puede publicar en cualquier hosting estático.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
# módulos que no hacen falta en el navegador (servidor, base de datos, modelos de lenguaje)
SKIP = {"store.py", "llm.py"}


def main() -> None:
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir()
    files: dict[str, str] = {}
    for path in sorted((ROOT / "tutor").glob("*.py")):
        if path.name not in SKIP:
            files[f"tutor/{path.name}"] = path.read_text(encoding="utf-8")
    files["data/banco_errores.csv"] = (ROOT / "data" / "banco_errores.csv").read_text(encoding="utf-8-sig")
    for path in sorted((ROOT / "problemas").glob("*.txt")):
        files[f"problemas/{path.name}"] = path.read_text(encoding="utf-8")
    files["bridge.py"] = (ROOT / "web" / "bridge.py").read_text(encoding="utf-8")
    (SITE / "bundle.json").write_text(json.dumps({"files": files}, ensure_ascii=False), encoding="utf-8")
    for name in ("index.html", "app.js", "style.css", "config.js"):
        src = ROOT / "web" / name
        if src.exists():
            shutil.copy(src, SITE / name)
    for img in (ROOT / "problemas").glob("*.png"):
        (SITE / "img").mkdir(exist_ok=True)
        shutil.copy(img, SITE / "img" / img.name)
    print(f"site/ listo: {len(files)} archivos del motor y del banco.")


if __name__ == "__main__":
    sys.exit(main())
