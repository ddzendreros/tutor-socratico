"""Diagnóstico determinista de respuestas del alumno.

Idea central: además de la ecuación correcta, generamos las ecuaciones que
saldrían de cada error típico ("mutaciones": sumar corrientes en la rama
compartida, invertir la polaridad de una fuente, usar R en vez de 1/R...).
Si la ecuación del alumno coincide con una mutación, sabemos EXACTAMENTE qué
error cometió, sin pedirle nada al LLM.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np


from .analysis import Loop, RefEq
from .circuit import Circuit
from .mathparse import ParsedEq

REL_TOL = 0.02       # 2 %: los alumnos redondean 1/6 como 0.17
VALUE_TOL = 0.01     # 1 % para resultados numéricos
ROUND_TOL = 0.05


@dataclass
class Diagnosis:
    ok: bool
    code: str                     # código del banco de errores o "OK"
    detail: dict = field(default_factory=dict)
    matched: RefEq | None = None
    mapping: tuple | None = None


# ============================================================== proporcionalidad
def _vec(coefs: dict[str, float], rhs: float, order: list[str]) -> list[float]:
    return [float(coefs.get(v, 0.0)) for v in order] + [float(rhs)]


def proportional(s: list[float], r: list[float], tol: float = REL_TOL) -> tuple[bool, float]:
    """¿s = k·r para algún k ≠ 0? Devuelve (bool, k)."""
    num = sum(a * b for a, b in zip(s, r))
    den = sum(b * b for b in r)
    if den == 0 or num == 0:
        return False, 0.0
    k = num / den
    scale = max(max(abs(x) for x in s), 1e-12)
    for a, b in zip(s, r):
        if abs(a - k * b) > tol * max(abs(a), abs(k * b), 0.05 * scale):
            return False, k
    return True, k


# ================================================================== mapeos
def mesh_mappings(n: int) -> list[tuple]:
    """Todas las formas de nombrar/orientar n corrientes de malla.
    mapping[i] = (índice de malla de referencia, signo) para la variable I{i+1} del alumno."""
    # Todas las corrientes giran en el MISMO sentido (convención del tutor), así
    # que sólo se permite un signo global; si se permitieran signos por malla,
    # el error "I1 + I2 en la rama compartida" pasaría como una convención válida.
    out = []
    for perm in itertools.permutations(range(n)):
        for sign in (1, -1):
            out.append(tuple((p, sign) for p in perm))
    out.sort(key=lambda m: (sum(1 for k, (p, _) in enumerate(m) if p != k), m[0][1] < 0))
    return out


def to_ref_space(eq: ParsedEq, mapping: tuple | None, prefix: str) -> dict[str, float]:
    if mapping is None:
        return dict(eq.coefs)
    out: dict[str, float] = {}
    for k, (p, s) in enumerate(mapping):
        out[f"{prefix}{p+1}"] = s * eq.coefs.get(f"{prefix}{k+1}", 0.0)
    return out


# ============================================================ mutaciones malla
def mesh_mutations(circuit: Circuit, q: RefEq, loops: list[Loop]) -> list[tuple[str, dict, float, dict]]:
    """(código, coefs, rhs, detalle) de errores típicos para la ecuación q."""
    if q.kind != "kvl":
        return []
    m = q.owner[0]
    me = f"I{m+1}"
    elem = {e.name: e for e in circuit.elements}
    muts = []
    base = {k: float(v) for k, v in q.coefs.items()}
    rhs = float(q.rhs)
    others = [k for k, v in base.items() if k != me and v != 0]
    if others:
        flipped = {k: (-v if k != me else v) for k, v in base.items()}
        muts.append(("MESH_SHARED_SUM", flipped, rhs, {"malla": m + 1}))
        dropped = {k: (0.0 if k != me else v) for k, v in base.items()}
        muts.append(("MESH_SHARED_MISSED", dropped, rhs, {"malla": m + 1}))
        for o in others:
            one = dict(base)
            one[o] = -one[o]
            muts.append(("MESH_SHARED_SUM", one, rhs, {"malla": m + 1, "otra": o}))
    # fuentes
    vparts = q.parts.get("V", [])
    if vparts:
        muts.append(("MESH_SOURCE_SIGN", dict(base), -rhs, {"fuentes": [n for n, _ in vparts]}))
    for name, contrib in vparts:
        muts.append(("MESH_SOURCE_SIGN", dict(base), rhs - 2 * float(contrib), {"fuente": name}))
        muts.append(("MESH_SOURCE_MISSING", dict(base), rhs - float(contrib), {"fuente": name}))
    # resistencias omitidas (en la suma propia)
    for name in q.parts.get("R", []):
        R = float(elem[name].value)
        c = dict(base)
        c[me] -= R
        muts.append(("MESH_RES_MISSING", c, rhs, {"resistencia": name}))
        if any(name in lp.names for k, lp in enumerate(loops) if k != m):
            c2 = dict(base)
            c2[me] -= R
            for k, lp in enumerate(loops):
                if k != m and name in lp.names:
                    c2[f"I{k+1}"] = 0.0
            muts.append(("MESH_RES_MISSING", c2, rhs, {"resistencia": name, "compartida": True}))
    return muts


# ============================================================ mutaciones nodo
def node_mutations(circuit: Circuit, q: RefEq) -> list[tuple[str, dict, float, dict]]:
    if q.kind not in ("kcl", "supernode"):
        return []
    elem = {e.name: e for e in circuit.elements}
    base = {k: float(v) for k, v in q.coefs.items()}
    rhs = float(q.rhs)
    muts = []
    # usar R en lugar de 1/R: reconstruir con R
    usedR = {k: 0.0 for k in base}
    for name in q.parts.get("R", []):
        e = elem[name]
        for p, o in ((e.n1, e.n2), (e.n2, e.n1)):
            if p in q.owner:
                usedR[f"V{p}"] += float(e.value)
                if o != "0" and o not in q.owner:
                    usedR[f"V{o}"] -= float(e.value)
    muts.append(("NODE_USED_R", usedR, rhs, {}))
    own = {f"V{n}" for n in q.owner}
    neigh = [k for k, v in base.items() if k not in own and v != 0]
    if neigh:
        muts.append(("NODE_NEIGHBOR_SIGN", {k: (-v if k in neigh else v) for k, v in base.items()},
                     rhs, {}))
    iparts = q.parts.get("I", [])
    if iparts:
        muts.append(("NODE_CSOURCE_SIGN", dict(base), -rhs, {}))
    for name, contrib in iparts:
        muts.append(("NODE_CSOURCE_SIGN", dict(base), rhs - 2 * float(contrib), {"fuente": name}))
        muts.append(("NODE_CSOURCE_MISSING", dict(base), rhs - float(contrib), {"fuente": name}))
    return muts


# ======================================================== diagnóstico ecuación
def diagnose_equation(eq: ParsedEq, refs: list[RefEq], pending: list[int],
                      mappings: list[tuple] | None, prefix: str,
                      circuit: Circuit, loops: list[Loop] | None = None) -> Diagnosis:
    """Compara la ecuación del alumno contra las de referencia pendientes.

    `pending`: índices de refs aún no escritos por el alumno.
    `mappings`: mapeos de variables aún consistentes (None = identidad, p.ej. nodos).
    """
    maps = mappings or [None]
    order = list(refs[0].coefs.keys())
    rowv = [_vec({v: float(c) for v, c in q.coefs.items()}, float(q.rhs), order) for q in refs]
    cons = [i for i, q in enumerate(refs) if q.kind == "constraint"]
    scan = pending + [i for i in range(len(refs)) if i not in pending]
    # 1) ¿es una de las ecuaciones correctas? (admite sustituir valores ya
    #    conocidos por restricciones, p.ej. Va = 12 dentro del KCL del nodo b)
    for mp in maps:
        s = _vec(to_ref_space(eq, mp, prefix), eq.rhs, order)
        for idx in scan:
            ok = proportional(s, rowv[idx])[0]
            if not ok and refs[idx].kind != "constraint" and cons:
                ok = in_span(s, [rowv[idx]] + [rowv[c] for c in cons])
            if ok:
                code = "OK" if idx in pending else "OK_REPEATED"
                return Diagnosis(True, code, {"index": idx}, refs[idx], mp)
    # 2) ¿es verdadera pero no es ninguna de las pedidas (combinación)?
    for mp in maps:
        s = _vec(to_ref_space(eq, mp, prefix), eq.rhs, order)
        if in_span(s, rowv, need_first=False):
            return Diagnosis(False, "EQ_VALID_COMBINATION", {}, None, mp)
    # 3) mutaciones: ¿qué error típico explica la ecuación?
    for mp in maps:
        s = _vec(to_ref_space(eq, mp, prefix), eq.rhs, order)
        for idx in pending:
            q = refs[idx]
            if q.kind == "constraint":
                muts = [("CONSTRAINT_SIGN", {k: float(v) for k, v in q.coefs.items()}, -float(q.rhs), {})]
            elif prefix == "I":
                muts = mesh_mutations(circuit, q, loops or [])
            else:
                muts = node_mutations(circuit, q)
            for code, coefs, rhs, det in muts:
                mv = _vec(coefs, rhs, order)
                ok = proportional(s, mv)[0] or (
                    q.kind != "constraint" and cons and in_span(s, [mv] + [rowv[c] for c in cons]))
                if ok:
                    det = dict(det, index=idx, ecuacion=q.label)
                    if q.kind == "constraint":
                        det["fuente"] = q.source
                    return Diagnosis(False, code, det, q, mp)
            # ecuación correcta, pero sustituyó un valor conocido con el signo equivocado
            if q.kind != "constraint":
                for c in cons:
                    flipped = rowv[c][:-1] + [-rowv[c][-1]]
                    others = [rowv[o] for o in cons if o != c]
                    if in_span(s, [rowv[idx], flipped] + others) and not in_span(s, [rowv[idx]] + others):
                        return Diagnosis(False, "CONSTRAINT_SIGN", {
                            "index": idx, "ecuacion": q.label,
                            "fuente": refs[c].source, "sustitucion": True}, q, mp)
    # 4) diferencia término a término contra la ecuación pendiente más parecida.
    #    Primero se sustituyen las incógnitas fijadas por una sola restricción
    #    (p.ej. Va = 12), porque el alumno suele escribir el KCL ya sustituido.
    known: dict[int, float] = {}
    for c in cons:
        nz = [j for j, x in enumerate(rowv[c][:-1]) if abs(x) > 1e-12]
        if len(nz) == 1:
            known[nz[0]] = rowv[c][-1] / rowv[c][nz[0]]

    def subst(v: list[float], keep: set[int]) -> list[float]:
        v = list(v)
        for j, val in known.items():
            if j not in keep:
                v[-1] -= v[j] * val
                v[j] = 0.0
        return v

    best = None
    mp0 = maps[0]
    s_raw = _vec(to_ref_space(eq, mp0, prefix), eq.rhs, order)
    for idx in pending or range(len(refs)):
        q = refs[idx]
        keep = {j for j, x in enumerate(rowv[idx][:-1]) if abs(x) > 1e-12} if q.kind == "constraint" else set()
        s, r = subst(s_raw, keep), subst(rowv[idx], keep)
        vs = {j for j, x in enumerate(s_raw[:-1]) if abs(x) > 1e-12}
        vr = {j for j, x in enumerate(rowv[idx][:-1]) if abs(x) > 1e-12}
        jac = len(vs & vr) / max(len(vs | vr), 1)
        # anclar primero en el término independiente (las fuentes suelen estar bien)
        for a in [len(r) - 1] + list(range(len(r) - 1)):
            if r[a] == 0 or s[a] == 0:
                continue
            k = s[a] / r[a]
            bad = [j for j in range(len(r))
                   if abs(s[j] - k * r[j]) > REL_TOL * max(abs(s[j]), abs(k * r[j]), 1e-9)]
            key = (len(bad), -jac, q.kind == "constraint")
            if best is None or key < best[3]:
                best = (bad, idx, k, key)
    if best is None:
        return Diagnosis(False, "EQ_UNRELATED", {})
    bad, idx = best[0], best[1]
    labels = order + ["término independiente"]
    wrong_terms = [labels[j] for j in bad]
    if prefix == "I" and mp0 is not None:
        # expresar variables en la nomenclatura del alumno
        inv = {f"I{p+1}": f"I{k+1}" for k, (p, _) in enumerate(mp0)}
        wrong_terms = [inv.get(t, t) for t in wrong_terms]
    code = "EQ_TERMS_WRONG" if len(bad) <= 2 else "EQ_UNRELATED"
    return Diagnosis(False, code, {"terminos": wrong_terms, "index": idx,
                                   "ecuacion": refs[idx].label}, refs[idx], mp0)


def in_span(s: list[float], rows: list[list[float]], need_first: bool = True,
            tol: float = REL_TOL) -> bool:
    """¿s es combinación lineal de `rows`? Con need_first, la primera fila debe
    participar (si no, s sería sólo una restricción reescrita)."""
    A = np.array(rows, dtype=float).T
    sv = np.array(s, dtype=float)
    c, *_ = np.linalg.lstsq(A, sv, rcond=None)
    fit = A @ c
    scale = max(float(np.abs(sv).max()), 1e-12)
    for a, b in zip(sv, fit):  # tolerancia término a término, igual que proportional()
        if abs(a - b) > tol * max(abs(a), abs(b), 0.05 * scale):
            return False
    return (not need_first) or abs(c[0]) > 1e-6


def filter_mappings(eq: ParsedEq, refs: list[RefEq], mappings: list[tuple], prefix: str) -> list[tuple]:
    """Mapeos bajo los cuales la ecuación del alumno es alguna de las correctas."""
    order = list(refs[0].coefs.keys())
    keep = []
    for mp in mappings:
        s = _vec(to_ref_space(eq, mp, prefix), eq.rhs, order)
        if any(proportional(s, _vec({v: float(c) for v, c in q.coefs.items()}, float(q.rhs), order))[0]
               for q in refs):
            keep.append(mp)
    return keep


# ============================================================ valores numéricos
def close(a: float, b: float, tol: float = VALUE_TOL) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-9) or abs(a - b) < 1e-6


def diagnose_value(student: float, truth: float, mutations: dict[str, float] | None = None) -> Diagnosis:
    if close(student, truth):
        return Diagnosis(True, "OK", {"valor": student})
    if close(student, -truth):
        return Diagnosis(False, "VALUE_SIGN", {"valor": student})
    for f in (1000, 1 / 1000):
        if close(student, truth * f) or close(student, -truth * f):
            return Diagnosis(False, "VALUE_UNIT_PREFIX", {"valor": student})
    for code, v in (mutations or {}).items():
        if v is not None and close(student, float(v), 0.015):
            return Diagnosis(False, code.split("#")[0], {"valor": student})
    if close(student, truth, ROUND_TOL):
        return Diagnosis(False, "VALUE_ROUNDING", {"valor": student})
    return Diagnosis(False, "VALUE_WRONG", {"valor": student})


def series_parallel_mutations(circuit: Circuit, truth_req: float | None = None) -> dict[str, float]:
    """Valores de Req que saldrían de los errores más comunes."""
    Rs = [float(e.value) for e in circuit.by_kind("R")]
    out: dict[str, float] = {}
    if len(Rs) >= 2:
        s = sum(Rs)
        if truth_req is None or not close(s, truth_req):
            out["SP_PARALLEL_SUMMED"] = s
        par = 1 / sum(1 / r for r in Rs)
        if truth_req is None or not close(par, truth_req):
            out["SP_SERIES_AS_PARALLEL"] = par
    if len(Rs) >= 3:
        prod = 1.0
        for r in Rs:
            prod *= r
        out["SP_PRODUCT_OVER_SUM"] = prod / sum(Rs)
    return out


def check_system_values(values: dict[str, float], refs: list[RefEq]) -> list[str]:
    """Ecuaciones de referencia que NO satisfacen los valores del alumno."""
    bad = []
    for q in refs:
        lhs = sum(float(c) * values.get(v, 0.0) for v, c in q.coefs.items())
        if not close(lhs, float(q.rhs), 0.02) and not (abs(float(q.rhs)) < 1e-9 and abs(lhs) < 0.02):
            bad.append(q.label)
    return bad
