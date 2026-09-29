"""Solución exacta de circuitos DC resistivos por análisis nodal modificado (MNA).

Es la "verdad" contra la que se valida todo lo que escribe el alumno. El LLM
nunca calcula nada: todos los números salen de aquí.
"""
from __future__ import annotations

from dataclasses import dataclass

import sympy as sp

from .circuit import Circuit, CircuitError, Element, Target


@dataclass
class Solution:
    node_v: dict[str, sp.Rational]          # voltaje de nodo respecto a "0"
    elem_i: dict[str, sp.Rational]          # corriente n1 -> n2 por el elemento
    elem_v: dict[str, sp.Rational]          # v(n1) - v(n2)

    def power(self, name: str) -> sp.Rational:
        """Potencia ABSORBIDA (negativa = entrega)."""
        return self.elem_v[name] * self.elem_i[name]


def _mna(elements: list[Element], nodes: list[str]) -> Solution:
    idx = {n: k for k, n in enumerate(nodes)}
    vsrc = [e for e in elements if e.kind == "V"]
    nv, ns = len(nodes), len(vsrc)
    A = sp.zeros(nv + ns, nv + ns)
    b = sp.zeros(nv + ns, 1)

    def stamp(node: str, col: int, val):
        if node != "0":
            A[idx[node], col] += val

    for e in elements:
        if e.kind == "R":
            g = 1 / e.value
            for p, q in ((e.n1, e.n2), (e.n2, e.n1)):
                if p != "0":
                    A[idx[p], idx[p]] += g
                    if q != "0":
                        A[idx[p], idx[q]] -= g
        elif e.kind == "I":
            # corriente sale de n1 (a través del elemento) y entra a n2
            if e.n1 != "0":
                b[idx[e.n1]] -= e.value
            if e.n2 != "0":
                b[idx[e.n2]] += e.value
    for k, e in enumerate(vsrc):
        col = nv + k
        stamp(e.n1, col, 1)     # corriente de la fuente sale de n1 hacia el elemento
        stamp(e.n2, col, -1)
        if e.n1 != "0":
            A[col, idx[e.n1]] += 1
        if e.n2 != "0":
            A[col, idx[e.n2]] -= 1
        b[col] = e.value

    if A.det() == 0:
        raise CircuitError(
            "El circuito no tiene solución única: revisa si hay un lazo solo de fuentes "
            "de voltaje, una fuente de corriente en circuito abierto o un nodo flotante."
        )
    x = A.LUsolve(b)
    node_v = {"0": sp.Integer(0)}
    node_v.update({n: sp.nsimplify(x[idx[n]]) for n in nodes})
    elem_i, elem_v = {}, {}
    for e in elements:
        v = node_v[e.n1] - node_v[e.n2]
        elem_v[e.name] = v
        if e.kind == "R":
            elem_i[e.name] = v / e.value
        elif e.kind == "I":
            elem_i[e.name] = e.value
    for k, e in enumerate(vsrc):
        elem_i[e.name] = sp.nsimplify(x[nv + k])
    return Solution(node_v, elem_i, elem_v)


def solve(circuit: Circuit) -> Solution:
    circuit.validate()
    return _mna(circuit.elements, circuit.non_ground_nodes)


def equivalent_resistance(circuit: Circuit, a: str, b: str,
                          exclude: str | None = None) -> sp.Rational:
    """Req entre a y b con fuentes apagadas (V en corto, I abierta).

    `exclude`: elemento a retirar (p.ej. la fuente que "ve" la Req).
    """
    elems: list[Element] = []
    for e in circuit.elements:
        if e.name == exclude or e.kind == "I":
            continue
        if e.kind == "V":
            elems.append(Element(e.name, "V", e.n1, e.n2, sp.Integer(0)))
        else:
            elems.append(e)
    # fuente de prueba de 1 A que entra por a y sale por b
    elems.append(Element("__Itest", "I", b, a, sp.Integer(1)))
    # re-referenciar: b es tierra para el cálculo
    ren = {b: "0", "0": "__old_ground"} if b != "0" else {}
    elems = [Element(e.name, e.kind, ren.get(e.n1, e.n1), ren.get(e.n2, e.n2), e.value)
             for e in elems]
    nodes = []
    for e in elems:
        for n in (e.n1, e.n2):
            if n != "0" and n not in nodes:
                nodes.append(n)
    try:
        sol = _mna(elems, nodes)
    except CircuitError:
        raise CircuitError(f"No hay camino resistivo entre {a} y {b}.")
    a2 = ren.get(a, a)
    return sp.nsimplify(sol.node_v[a2])


def target_value(circuit: Circuit, sol: Solution, t: Target) -> sp.Rational:
    if t.quantity == "I":
        return sol.elem_i[t.element]
    if t.quantity == "V":
        return sol.elem_v[t.element]
    if t.quantity == "P":
        return sol.power(t.element)
    if t.quantity == "Req":
        if t.element:
            e = circuit.get(t.element)
            return equivalent_resistance(circuit, e.n1, e.n2, exclude=e.name)
        return equivalent_resistance(circuit, *t.nodes)
    raise CircuitError(f"Magnitud desconocida: {t.quantity}")


def target_unit(t: Target) -> str:
    return {"I": "A", "V": "V", "P": "W", "Req": "Ω"}[t.quantity]
