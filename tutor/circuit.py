"""Modelo de circuito DC resistivo y parser de netlist.

Formato de netlist (una línea por elemento, estilo SPICE simplificado):

    R1 a b 4        resistencia de 4 Ω entre los nodos a y b
    V1 a 0 12       fuente de voltaje de 12 V; el PRIMER nodo es la terminal +
    I1 0 a 2        fuente de corriente de 2 A; la flecha va del primer nodo al segundo

El nodo "0" (o "gnd"/"tierra") es la referencia. Los valores aceptan prefijos
k, m, u/µ, M/meg y unidades opcionales (Ω, ohm, V, A): "2.2k", "4Ω", "500mA".

Las mallas se declaran tal como están dibujadas, recorriendo cada una en
sentido horario (el orden de los elementos define el sentido):

    MALLA 1: VX R1 R2 VY
    MALLA 2: VY R2 R3

Sin geometría no hay forma de saber cuáles lazos son las "ventanas" del
dibujo, así que el problema las trae declaradas o el alumno las confirma.

Una resistencia con ambas terminales en el mismo nodo está en corto: se acepta
y no conduce corriente.

Convención interna para TODOS los elementos: la corriente i_e fluye de n1 a n2
a través del elemento y v_e = v(n1) - v(n2). Potencia absorbida = v_e * i_e.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Iterable

import sympy as sp

GROUND_ALIASES = {"0", "gnd", "tierra", "ref", "gnd0"}

_PREFIX = {
    "": 1, "k": 10**3, "K": 10**3, "m": sp.Rational(1, 1000),
    "u": sp.Rational(1, 10**6), "µ": sp.Rational(1, 10**6), "μ": sp.Rational(1, 10**6),
    "M": 10**6, "meg": 10**6, "MEG": 10**6, "Meg": 10**6,
}
_UNIT_RE = r"(?:Ω|ohms?|ohmios?|V|volts?|voltios?|A|amperes?|amperios?|amps?)?"
_VALUE_RE = re.compile(
    rf"^\s*([+-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)\s*(meg|MEG|Meg|[kKmuµμM]?)\s*{_UNIT_RE}\s*$"
)


class CircuitError(ValueError):
    """Error de definición del circuito, con mensaje apto para el alumno."""


def parse_value(text: str) -> sp.Rational:
    """'2.2k' -> 2200, '500mA' -> 1/2, '4Ω' -> 4. Devuelve un racional exacto."""
    m = _VALUE_RE.match(str(text))
    if not m:
        raise CircuitError(f"No entiendo el valor '{text}'. Usa algo como 4, 2.2k o 500m.")
    num, prefix = m.group(1).replace(",", "."), m.group(2)
    return sp.Rational(num) * _PREFIX[prefix]


def norm_node(n: str) -> str:
    n = str(n).strip()
    return "0" if n.lower() in GROUND_ALIASES else n


@dataclass
class Element:
    name: str
    kind: str  # "R", "V", "I"
    n1: str
    n2: str
    value: sp.Rational

    @property
    def label(self) -> str:
        unit = {"R": "Ω", "V": "V", "I": "A"}[self.kind]
        return f"{self.name} ({fmt(self.value)} {unit})"

    def to_row(self) -> list[str]:
        return [self.name, self.n1, self.n2, fmt(self.value)]


@dataclass
class Target:
    """Magnitud que pide el problema."""
    quantity: str  # "I" corriente, "V" voltaje, "P" potencia, "Req"
    element: str | None = None
    nodes: tuple[str, str] | None = None  # para Req entre dos nodos

    def describe(self) -> str:
        names = {"I": "la corriente en", "V": "el voltaje en", "P": "la potencia en"}
        if self.quantity == "Req":
            if self.element:
                return f"la resistencia equivalente que ve {self.element}"
            return f"la resistencia equivalente entre {self.nodes[0]} y {self.nodes[1]}"
        return f"{names[self.quantity]} {self.element}"


@dataclass
class Circuit:
    elements: list[Element]
    targets: list[Target] = field(default_factory=list)
    statement: str = ""
    # mallas como están dibujadas: nombres de elementos en recorrido horario
    meshes: list[list[str]] = field(default_factory=list)

    # ------------------------------------------------------------------ utils
    @property
    def nodes(self) -> list[str]:
        seen: list[str] = []
        for e in self.elements:
            for n in (e.n1, e.n2):
                if n not in seen:
                    seen.append(n)
        return seen

    @property
    def non_ground_nodes(self) -> list[str]:
        return [n for n in self.nodes if n != "0"]

    def get(self, name: str) -> Element:
        for e in self.elements:
            if e.name.lower() == name.lower():
                return e
        raise CircuitError(f"No existe el elemento {name}.")

    def by_kind(self, kind: str) -> list[Element]:
        return [e for e in self.elements if e.kind == kind]

    @property
    def shorted(self) -> list[Element]:
        """Resistencias en corto (ambas terminales en el mismo nodo)."""
        return [e for e in self.elements if e.n1 == e.n2]

    @property
    def active_elements(self) -> list[Element]:
        """Elementos que forman parte del análisis (sin los que están en corto)."""
        return [e for e in self.elements if e.n1 != e.n2]

    def validate(self) -> None:
        if not self.elements:
            raise CircuitError("El circuito no tiene elementos.")
        names = [e.name.lower() for e in self.elements]
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            raise CircuitError(f"Nombres repetidos: {', '.join(sorted(dup))}.")
        if "0" not in self.nodes:
            raise CircuitError("Falta el nodo de referencia '0' (tierra).")
        for e in self.elements:
            if e.n1 == e.n2 and e.kind != "R":
                raise CircuitError(f"{e.name} tiene ambas terminales en el mismo nodo: una fuente "
                                   "no puede estar en corto.")
            if e.kind == "R" and e.value <= 0:
                raise CircuitError(f"{e.name} debe tener resistencia positiva.")
        # conectividad
        adj: dict[str, set[str]] = {n: set() for n in self.nodes}
        for e in self.elements:
            adj[e.n1].add(e.n2)
            adj[e.n2].add(e.n1)
        seen, stack = set(), ["0"]
        while stack:
            n = stack.pop()
            if n in seen:
                continue
            seen.add(n)
            stack.extend(adj[n] - seen)
        if seen != set(self.nodes):
            raise CircuitError("Hay partes del circuito que no están conectadas a tierra.")
        for t in self.targets:
            if t.element:
                self.get(t.element)
        for k, mesh in enumerate(self.meshes):
            for name in mesh:
                self.get(name)
            if len(set(n.lower() for n in mesh)) != len(mesh):
                raise CircuitError(f"La malla {k+1} repite un elemento.")

    def netlist(self) -> str:
        lines = [" ".join(e.to_row()) for e in self.elements]
        lines += [f"MALLA {k+1}: {' '.join(m)}" for k, m in enumerate(self.meshes)]
        return "\n".join(lines)

    def describe(self) -> str:
        lines = []
        for e in self.elements:
            if e.kind == "R":
                lines.append(f"- {e.name}: {fmt(e.value)} Ω entre {e.n1} y {e.n2}")
            elif e.kind == "V":
                lines.append(f"- {e.name}: {fmt(e.value)} V, + en {e.n1}, − en {e.n2}")
            else:
                lines.append(f"- {e.name}: {fmt(e.value)} A, de {e.n1} hacia {e.n2}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "elements": [e.to_row() for e in self.elements],
            "targets": [asdict(t) for t in self.targets],
            "statement": self.statement,
            "meshes": [list(m) for m in self.meshes],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Circuit":
        c = parse_rows(d["elements"])
        c.targets = [Target(t["quantity"], t.get("element"),
                            tuple(t["nodes"]) if t.get("nodes") else None)
                     for t in d.get("targets", [])]
        c.statement = d.get("statement", "")
        c.meshes = [list(m) for m in d.get("meshes", [])]
        return c


def fmt(x, digits: int = 4) -> str:
    """Formatea un número de forma legible: 12, 2.2, 0.3333."""
    x = sp.nsimplify(x) if not isinstance(x, (int, float)) else x
    f = float(x)
    if abs(f - round(f)) < 1e-12:
        return str(int(round(f)))
    return f"{f:.{digits}g}"


def parse_rows(rows: Iterable[Iterable]) -> Circuit:
    elements = []
    for raw in rows:
        row = [str(x).strip() for x in raw if x is not None and str(x).strip() != ""]
        if not row:
            continue
        if len(row) != 4:
            raise CircuitError(f"La fila '{' '.join(row)}' debe tener 4 campos: nombre nodo1 nodo2 valor.")
        name, n1, n2, val = row
        kind = name[0].upper()
        if kind == "E":  # E1 como alias común de fuente de voltaje
            kind = "V"
        if kind not in "RVI":
            raise CircuitError(f"'{name}': el nombre debe empezar con R, V (o E) o I.")
        elements.append(Element(name, kind, norm_node(n1), norm_node(n2), parse_value(val)))
    return Circuit(elements)


_MESH_LINE = re.compile(r"^\s*malla\s*(\d+)?\s*[:=]?\s*(.+)$", re.I)


def parse_mesh_spec(text: str) -> list[list[str]]:
    """'MALLA 1: V1 R1 R2' (una por línea) -> [['V1','R1','R2'], ...].

    También acepta sólo la lista de elementos por renglón."""
    meshes: dict[int, list[str]] = {}
    order = 0
    for line in text.splitlines():
        line = re.split(r"[;#]", line, maxsplit=1)[0].strip()
        if not line:
            continue
        m = _MESH_LINE.match(line)
        body = m.group(2) if m else line
        names = [t for t in re.split(r"[\s,]+", body) if t]
        order += 1
        k = int(m.group(1)) if m and m.group(1) else order
        if k in meshes:
            raise CircuitError(f"La malla {k} está declarada dos veces.")
        meshes[k] = names
    return [meshes[k] for k in sorted(meshes)]


def parse_netlist(text: str) -> Circuit:
    rows, mesh_lines = [], []
    for line in text.splitlines():
        clean = re.split(r"[;#]", line, maxsplit=1)[0].strip()
        if not clean:
            continue
        if _MESH_LINE.match(clean):
            mesh_lines.append(clean)
        else:
            rows.append(clean.split())
    c = parse_rows(rows)
    c.meshes = parse_mesh_spec("\n".join(mesh_lines))
    c.validate()
    return c
