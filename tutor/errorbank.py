"""Banco de errores frecuentes → pregunta socrática → pistas graduadas.

Vive en data/banco_errores.csv para que el profesor lo edite en Excel sin tocar
código. Los códigos los emite el diagnóstico; los textos son del profesor.
Columnas por nivel de la escalera de andamiaje:
  nivel 1 = pregunta_socratica, 2 = pista_conceptual, 3 = pista_procedimental,
  4 = andamiaje. (Nivel 0 es la pregunta abierta del paso; nivel 5 lo genera
  el paso con el procedimiento guiado, porque depende del circuito.)
"""
from __future__ import annotations

import csv
import string
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "banco_errores.csv"
LEVEL_COLUMNS = {1: "pregunta_socratica", 2: "pista_conceptual", 3: "pista_procedimental",
                 4: "andamiaje"}


@dataclass
class ErrorEntry:
    codigo: str
    modulo: str
    tipo: str            # "conceptual" | "procedimental"
    descripcion: str
    niveles: dict[int, str]
    verificacion: str
    metacognitiva: str


class _Safe(dict):
    def __missing__(self, key):
        return "…"


def fill(template: str, **kw) -> str:
    """format() tolerante: los marcadores sin dato se vuelven '…'."""
    kw = {k: (", ".join(map(str, v)) if isinstance(v, (list, tuple, set)) else v) for k, v in kw.items()}
    try:
        return string.Formatter().vformat(template, (), _Safe(kw))
    except (ValueError, IndexError):
        return template


class ErrorBank:
    def __init__(self, path: Path | str = DEFAULT_PATH):
        self.entries: dict[str, ErrorEntry] = {}
        with open(path, encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = row["codigo"].strip()
                if not code:
                    continue
                self.entries[code] = ErrorEntry(
                    code, row["modulo"], row["tipo"].strip().lower(), row["descripcion"],
                    {lvl: row[col].strip() for lvl, col in LEVEL_COLUMNS.items()},
                    row.get("verificacion", "").strip(), row.get("pregunta_metacognitiva", "").strip(),
                )

    def get(self, code: str) -> ErrorEntry | None:
        return self.entries.get(code)

    def text(self, code: str, level: int, **kw) -> str | None:
        e = self.entries.get(code)
        if not e:
            return None
        lvl = max(1, min(level, 4))
        t = e.niveles.get(lvl) or e.niveles.get(1)
        return fill(t, **kw) if t else None

    def kind(self, code: str) -> str:
        e = self.entries.get(code)
        return e.tipo if e else "procedimental"
