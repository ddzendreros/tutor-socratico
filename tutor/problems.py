"""Banco de problemas del curso.

Cada problema es un archivo de texto en problemas/ que el profesor puede
editar sin tocar código:

    TITULO: Caso 1 · Malla real
    TEMA: mallas
    METODO: mallas
    NIVEL: básico
    FUENTE: Material de clase, Tema 4
    ENUNCIADO: Encuentre V, I y P en cada resistencia.
    PIDE: VIP
    RESPUESTA: I1=1.82 I2=2.73

    VX a 0 100
    R1 a b 10
    ...
    MALLA 1: VX R1 R2 VY
    MALLA 2: VY R2 R3

PIDE admite: VIP (tabla de V, I y P en cada resistencia), "I R2", "V R1",
"P R3", "Req V1" (la que ve la fuente) o "Req a-b", separados por ";".
RESPUESTA guarda los resultados publicados en el material; las pruebas
verifican que el motor los reproduce.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .circuit import Circuit, CircuitError, Target, parse_netlist

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "problemas"
HEADER_RE = re.compile(r"^\s*([A-ZÁÉÍÓÚÑ]+)\s*:\s*(.*)$")
KEYS = {"TITULO", "TEMA", "METODO", "NIVEL", "FUENTE", "ENUNCIADO", "PIDE", "RESPUESTA", "IMAGEN", "NOTA"}


@dataclass
class Problem:
    id: str
    title: str
    topic: str
    method: str
    circuit: Circuit
    level: str = ""
    source: str = ""
    statement: str = ""
    targets_text: str = ""
    expected: dict[str, float] = field(default_factory=dict)
    image: str = ""
    note: str = ""

    @property
    def label(self) -> str:
        return f"{self.title} ({self.level})" if self.level else self.title


def parse_targets(text: str) -> list[Target]:
    """'I R2; P R3; Req V1; Req a-b; VIP' -> lista de Target."""
    out = []
    for part in re.split(r"[;\n]", text or ""):
        tok = part.strip().split()
        if not tok:
            continue
        if tok[0].upper() in ("VIP", "TABLA"):
            out.append(Target("VIP"))
            continue
        if len(tok) < 2:
            raise CircuitError(f"No entiendo '{part.strip()}'. Usa: VIP; I R2; V R1; P R3; Req V1; Req a-b")
        q = {"i": "I", "v": "V", "p": "P", "req": "Req"}.get(tok[0].lower())
        if not q:
            raise CircuitError(f"No entiendo '{part.strip()}'. Usa: VIP; I R2; V R1; P R3; Req V1; Req a-b")
        if q == "Req" and "-" in tok[1]:
            a, b = tok[1].split("-", 1)
            out.append(Target("Req", None, (a, b)))
        else:
            out.append(Target(q, tok[1]))
    return out


def targets_to_text(ts: list[Target]) -> str:
    parts = []
    for t in ts:
        if t.quantity == "VIP":
            parts.append("VIP")
        elif t.nodes:
            parts.append(f"Req {t.nodes[0]}-{t.nodes[1]}")
        else:
            parts.append(f"{t.quantity} {t.element}")
    return "; ".join(parts)


def _parse_expected(text: str) -> dict[str, float]:
    out = {}
    for m in re.finditer(r"([A-Za-z][\w ]*?)\s*=\s*([-+]?\d+(?:\.\d+)?)", text):
        out[m.group(1).strip()] = float(m.group(2))
    return out


def parse_problem(text: str, pid: str = "") -> Problem:
    meta: dict[str, str] = {}
    body: list[str] = []
    for line in text.splitlines():
        if line.strip().startswith("#"):
            continue
        m = HEADER_RE.match(line)
        if m and m.group(1) in KEYS:
            meta[m.group(1)] = m.group(2).strip()
        else:
            body.append(line)
    circuit = parse_netlist("\n".join(body))
    targets = meta.get("PIDE", "")
    circuit.targets = parse_targets(targets)
    circuit.statement = meta.get("ENUNCIADO", "")
    circuit.validate()
    if circuit.meshes:
        from .analysis import meshes_from_spec
        meshes_from_spec(circuit)          # falla aquí, con un mensaje claro, si están mal declaradas
    method = meta.get("METODO", meta.get("TEMA", "mallas")).lower()
    return Problem(
        id=pid, title=meta.get("TITULO", pid), topic=meta.get("TEMA", method).lower(),
        method=method, circuit=circuit, level=meta.get("NIVEL", ""), source=meta.get("FUENTE", ""),
        statement=meta.get("ENUNCIADO", ""), targets_text=targets,
        expected=_parse_expected(meta.get("RESPUESTA", "")), image=meta.get("IMAGEN", ""),
        note=meta.get("NOTA", ""),
    )


def load_bank(directory: Path | str = DEFAULT_DIR) -> list[Problem]:
    out = []
    for path in sorted(Path(directory).glob("*.txt")):
        try:
            out.append(parse_problem(path.read_text(encoding="utf-8"), path.stem))
        except CircuitError as exc:
            raise CircuitError(f"{path.name}: {exc}") from exc
    return out
