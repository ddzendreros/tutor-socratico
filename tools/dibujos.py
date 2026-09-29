"""Dibuja los circuitos del banco en problemas/<id>.svg.

    pip install schemdraw
    python tools/dibujos.py              # todos
    python tools/dibujos.py mallas-01    # los que empiezan así

Cada dibujo se describe en una cuadrícula: los cables de cada nodo y los
elementos entre dos puntos. Antes de dibujar se compara contra la tabla del
problema: cada elemento debe unir los nodos que dice la tabla, con la
polaridad de la tabla (+ de V en su primer nodo; I va del primer nodo al
segundo). Si no coincide, no se genera el dibujo.
"""
from __future__ import annotations

import sys
from pathlib import Path

import schemdraw
import schemdraw.elements as elm

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tutor.circuit import fmt  # noqa: E402
from tutor.problems import load_bank  # noqa: E402

S = 2.5                     # unidades de schemdraw por paso de la cuadrícula
MESH_COLOR = "#8a8a8a"
NODE_COLOR = "#1f5fa8"
UNITS = {"R": "Ω", "V": "V", "I": "A"}

# nodos: polilíneas de cable por nodo; elementos: (nombre, punto en su 1er nodo, punto en su 2o nodo, lado
# de la etiqueta); mallas y nodos: etiquetas; tierra: punto del símbolo; terminales: puntos abiertos
DIBUJOS: dict[str, dict] = {
    "mallas-01-caso1-malla-real": dict(
        nodos={"0": [[(0, 0), (4, 0)]], "a": [[(0, 2)]], "b": [[(2, 2), (4, 2)]], "m": [[(2, 1)]]},
        elementos=[("VX", (0, 2), (0, 0), "l"), ("R1", (0, 2), (2, 2), "t"), ("R2", (2, 2), (2, 1), "r"),
                   ("VY", (2, 1), (2, 0), "r"), ("R3", (4, 2), (4, 0), "r")],
        mallas=[("I", (1, 1)), ("II", (3.25, 1))],
    ),
    "mallas-02-supermalla": dict(
        nodos={"0": [[(0, 4), (0, 0), (4, 0)]], "B": [[(2, 4)]], "C": [[(2, 2)]], "D": [[(4, 4), (4, 2)]]},
        elementos=[("R1", (0, 4), (2, 4), "t"), ("R2", (2, 4), (4, 4), "t"), ("IX", (2, 2), (2, 4), "l"),
                   ("R3", (2, 0), (2, 2), "l"), ("R4", (2, 2), (4, 2), "b"), ("VX", (4, 2), (4, 0), "r")],
        mallas=[("I", (0.9, 2)), ("II", (3, 3.3)), ("III", (3.3, 0.7))],
    ),
    "mallas-03-fantasma-supermalla": dict(
        nodos={"0": [[(0, 0), (6, 0)]], "m0": [[(0, 2), (0, 4)]], "m1": [[(2, 2)]],
               "m2": [[(4, 4), (4, 2)]], "m3": [[(6, 2)]]},
        elementos=[("R1", (0, 2), (0, 0), "l"), ("R2", (0, 2), (2, 2), "t"), ("IX", (0, 4), (4, 4), "t"),
                   ("R3", (2, 2), (2, 0), "r"), ("R4", (2, 2), (4, 2), "t"), ("IY", (4, 0), (4, 2), "r"),
                   ("R5", (4, 2), (6, 2), "t"), ("VX", (6, 2), (6, 0), "r")],
        mallas=[("I", (2, 3.4)), ("II", (0.9, 0.7)), ("III", (3.1, 0.7)), ("IV", (5.2, 0.7))],
    ),
    "mallas-04-ejemplo-docx": dict(
        nodos={"0": [[(0, 0), (4, 0)]], "a": [[(0, 2)]], "b": [[(2, 2)]], "c": [[(4, 2)]]},
        elementos=[("V1", (0, 2), (0, 0), "l"), ("R1", (0, 2), (2, 2), "t"), ("R2", (2, 2), (2, 0), "r"),
                   ("R3", (2, 2), (4, 2), "t"), ("V2", (4, 0), (4, 2), "r")],
        mallas=[("1", (0.85, 1)), ("2", (3.2, 1))],
    ),
    "nodos-01-nodos-reales": dict(
        nodos={"0": [[(0, 0), (6, 0)]], "1": [[(0, 2), (2, 2)]], "2": [[(4, 2), (6, 2)]]},
        elementos=[("IX", (0, 0), (0, 2), "l"), ("R1", (2, 2), (2, 0), "r"), ("R2", (2, 2), (4, 2), "t"),
                   ("R3", (4, 2), (4, 0), "r"), ("IY", (6, 2), (6, 0), "r")],
        etiquetas_nodo=[("1", (1, 2)), ("2", (5, 2))], tierra=(3, 0),
    ),
    "nodos-02-nodo-fantasma": dict(
        nodos={"0": [[(0, 0), (6, 0)]], "1": [[(0, 2), (2, 2)]], "2": [[(4, 2)]], "3": [[(6, 2)]]},
        elementos=[("VA", (0, 2), (0, 0), "l"), ("R3", (2, 2), (2, 0), "r"), ("R1", (2, 2), (4, 2), "t"),
                   ("R4", (4, 2), (4, 0), "r"), ("R2", (4, 2), (6, 2), "t"), ("VB", (6, 0), (6, 2), "r")],
        etiquetas_nodo=[("1", (1, 2)), ("2", (4, 2)), ("3", (6, 2))], tierra=(3, 0),
    ),
    "nodos-03-supernodo": dict(
        nodos={"0": [[(0, 0), (8, 0)]], "1": [[(0, 2), (2, 2), (2, 4)]], "2": [[(4, 2)]],
               "3": [[(6, 4), (6, 2)]], "5": [[(8, 3), (8, 2)]]},
        elementos=[("VF", (0, 2), (0, 0), "l"), ("R1", (2, 2), (2, 0), "l"), ("R2", (2, 2), (4, 2), "b"),
                   ("R3", (2, 4), (6, 4), "t"), ("VS", (4, 2), (6, 2), "b"), ("R4", (4, 2), (4, 0), "r"),
                   ("IX", (6, 3), (8, 3), "t"), ("R6", (6, 2), (8, 2), "b"), ("R5", (8, 2), (8, 0), "r")],
        etiquetas_nodo=[("1", (1, 2)), ("2", (4, 2)), ("3", (6, 4)), ("5", (8, 3))], tierra=(4.8, 0),
    ),
    "ohm-01-ley-de-ohm": dict(
        nodos={"0": [[(0, 0), (2, 0)]], "a": [[(0, 2), (2, 2)]]},
        elementos=[("V1", (0, 2), (0, 0), "l"), ("R1", (2, 2), (2, 0), "r")],
    ),
    "serie-01-serie": dict(
        nodos={"0": [[(0, 0), (4, 0)]], "a": [[(0, 2)]], "b": [[(2, 2)]], "c": [[(4, 2)]]},
        elementos=[("VT", (0, 2), (0, 0), "l"), ("R1", (0, 2), (2, 2), "t"), ("R2", (2, 2), (4, 2), "t"),
                   ("R3", (4, 2), (4, 0), "r")],
    ),
    "serie-02-paralelo": dict(
        nodos={"0": [[(0, 0), (6, 0)]], "a": [[(0, 2), (6, 2)]]},
        elementos=[("VT", (0, 2), (0, 0), "l"), ("R1", (2, 2), (2, 0), "r"), ("R2", (4, 2), (4, 0), "r"),
                   ("R3", (6, 2), (6, 0), "r")],
    ),
    "serie-03-reto-mixto": dict(
        nodos={"0": [[(0, 0), (8, 0), (8, 2)]], "a": [[(0, 2)]], "b": [[(2, 2), (6, 2)]]},
        elementos=[("VT", (0, 2), (0, 0), "l"), ("R1", (0, 2), (2, 2), "t"), ("R2", (3, 2), (3, 0), "r"),
                   ("R3", (5, 2), (5, 0), "r"), ("R4", (6, 2), (8, 2), "t")],
    ),
    "serie-04-thevenin-rab": dict(
        nodos={"0": [[(0, 0), (4, 0)]], "x": [[(0, 2)]], "A": [[(2, 2), (4, 2)]]},
        elementos=[("V1", (0, 2), (0, 0), "l"), ("R1", (0, 2), (2, 2), "t"), ("R2", (2, 2), (2, 0), "r")],
        terminales=[("A", (4, 2)), ("B", (4, 0))],
    ),
}


# ------------------------------------------------------------------ geometría
def _on_segment(p, a, b) -> bool:
    (x, y), (x1, y1), (x2, y2) = p, a, b
    cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
    return abs(cross) < 1e-9 and min(x1, x2) - 1e-9 <= x <= max(x1, x2) + 1e-9 \
        and min(y1, y2) - 1e-9 <= y <= max(y1, y2) + 1e-9


def _segments(polys):
    for poly in polys:
        if len(poly) == 1:
            yield poly[0], poly[0]
        yield from zip(poly, poly[1:])


def node_at(spec: dict, p) -> list[str]:
    return [n for n, polys in spec["nodos"].items() if any(_on_segment(p, a, b) for a, b in _segments(polys))]


def check(pid: str, spec: dict, circuit) -> None:
    """El dibujo debe tener los mismos elementos, entre los mismos nodos y con la misma polaridad."""
    drawn = {name for name, *_ in spec["elementos"]}
    table = {e.name for e in circuit.elements}
    if drawn != table:
        raise SystemExit(f"{pid}: el dibujo tiene {sorted(drawn)} y la tabla {sorted(table)}")
    for name, p1, p2, _ in spec["elementos"]:
        e = circuit.get(name)
        for p, want in ((p1, e.n1), (p2, e.n2)):
            got = node_at(spec, p)
            if got != [want]:
                raise SystemExit(f"{pid}: {name} toca {got or 'ningún nodo'} en {p}; la tabla dice {want}")


def junctions(spec: dict) -> list[tuple]:
    """Puntos donde se unen tres o más conexiones (llevan punto de unión)."""
    ends: dict[tuple, int] = {}
    for polys in spec["nodos"].values():
        for poly in polys:
            for p in (poly[0], poly[-1]):
                ends[p] = ends.get(p, 0) + (len(poly) > 1)
    for _, p1, p2, _ in spec["elementos"]:
        for p in (p1, p2):
            ends[p] = ends.get(p, 0) + 1
    out = []
    for p, n in ends.items():
        inner = sum(2 for polys in spec["nodos"].values() for poly in polys for a, b in zip(poly, poly[1:])
                    if p not in (a, b) and _on_segment(p, a, b))
        corner = sum(1 for polys in spec["nodos"].values() for poly in polys
                     for q in poly[1:-1] if q == p) * 2
        if n + inner + corner >= 3:
            out.append(p)
    return out


# ------------------------------------------------------------------ dibujo
def _xy(p):
    return (p[0] * S, p[1] * S)


def _label(d, text, xy, side, gap, color="black", size=16):
    dx, dy, ha, va = {"l": (-gap, 0, "right", "center"), "r": (gap, 0, "left", "center"),
                      "t": (0, gap, "center", "bottom"), "b": (0, -gap, "center", "top")}[side]
    d.add(elm.Label().at((xy[0] + dx, xy[1] + dy)).label(text, halign=ha, valign=va, color=color,
                                                           fontsize=size))


def draw(pid: str, spec: dict, circuit, path: Path) -> None:
    with schemdraw.Drawing(show=False) as d:
        d.config(fontsize=16, lw=1.6)
        for polys in spec["nodos"].values():
            for poly in polys:
                for a, b in zip(poly, poly[1:]):
                    d.add(elm.Line().endpoints(_xy(a), _xy(b)))
        for name, p1, p2, side in spec["elementos"]:
            e = circuit.get(name)
            if e.kind == "V" and p1[1] == p2[1]:
                # horizontal: SourceV giraría el "−" hasta verse como "|"; los signos van como texto
                d.add(elm.Source().endpoints(_xy(p2), _xy(p1)))
                cx, cy, u = (p1[0] + p2[0]) / 2 * S, p1[1] * S, (1 if p1[0] > p2[0] else -1) * 0.22
                for sign, dx in (("+", u), ("−", -u)):
                    d.add(elm.Label().at((cx + dx, cy)).label(sign, halign="center", valign="center",
                                                              fontsize=17))
            elif e.kind == "V":    # el + queda en el extremo final: se traza del 2o nodo al 1o
                d.add(elm.SourceV().endpoints(_xy(p2), _xy(p1)))
            elif e.kind == "I":    # la flecha apunta al extremo final: del 1er nodo al 2o
                d.add(elm.SourceI().endpoints(_xy(p1), _xy(p2)))
            else:
                d.add(elm.Resistor().endpoints(_xy(p1), _xy(p2)))
            mid = ((p1[0] + p2[0]) / 2 * S, (p1[1] + p2[1]) / 2 * S)
            _label(d, f"{name}\n{fmt(e.value)} {UNITS[e.kind]}", mid, side, 0.8 if e.kind in "VI" else 0.45)
        for p in junctions(spec):
            d.add(elm.Dot(radius=0.12).at(_xy(p)))
        for text, p in spec.get("mallas", []):
            _label(d, text, _xy(p), "t", -0.3, MESH_COLOR, 20)
        for text, p in spec.get("etiquetas_nodo", []):
            _label(d, text, _xy(p), "t", 0.2, NODE_COLOR, 18)
        if spec.get("tierra"):
            d.add(elm.Ground().at(_xy(spec["tierra"])))
        for text, p in spec.get("terminales", []):
            d.add(elm.Dot(open=True, radius=0.15).at(_xy(p)))
            _label(d, text, _xy(p), "r", 0.35, NODE_COLOR, 18)
        d.save(str(path))
    if path.suffix == ".svg":
        # schemdraw escribe font-family="sans", que los navegadores no reconocen (caen en una serif)
        svg = path.read_text(encoding="utf-8")
        svg = svg.replace('font-family="sans"', 'font-family="Helvetica, Arial, sans-serif"')
        # fondo blanco también cuando se abre sola, en grande, con el navegador en modo oscuro
        path.write_text(svg.replace("<svg ", '<svg style="background:#fff" ', 1), encoding="utf-8")


def main(prefixes: list[str]) -> None:
    schemdraw.use("svg")
    bank = {p.id: p for p in load_bank(ROOT / "problemas")}
    missing = sorted(set(bank) - set(DIBUJOS))
    if missing and not prefixes:
        print("Sin dibujo:", ", ".join(missing))
    for pid, spec in DIBUJOS.items():
        if prefixes and not any(pid.startswith(x) for x in prefixes):
            continue
        circuit = bank[pid].circuit
        check(pid, spec, circuit)
        draw(pid, spec, circuit, ROOT / "problemas" / f"{pid}.svg")
        print(f"problemas/{pid}.svg")


if __name__ == "__main__":
    main(sys.argv[1:])
