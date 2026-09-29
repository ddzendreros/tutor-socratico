"""Lectura tolerante de lo que escribe el alumno: ecuaciones, expresiones,
valores numéricos con unidades y referencias a elementos del circuito."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor, implicit_multiplication_application, parse_expr, standard_transformations,
)

from .circuit import Circuit, Element

_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)
_PREFIX = {"": 1.0, "k": 1e3, "m": 1e-3, "u": 1e-6, "µ": 1e-6, "μ": 1e-6, "M": 1e6}

_VAR_RE = re.compile(r"(?<![A-Za-z])([IiVv])\s*_?\s*\{?([A-Za-z]?\d+|[a-z])\}?(?![A-Za-z])")
# SymPy parte 'I1' en I*1 (I es la unidad imaginaria) y trae E, S, N, O, Q predefinidos:
# si el alumno los escribe se leen como símbolos para decirle que no se reconocen
_LOOSE_NAME_RE = re.compile(r"(?<![A-Za-z0-9_.])(?:[A-Za-z]+\d[A-Za-z0-9]*|[IESNOQ])(?![A-Za-z0-9_])")


_UNIT_PREFIX = {"k": 1e3, "K": 1e3, "M": 1e6, "m": 1e-3, "µ": 1e-6, "μ": 1e-6, "u": 1e-6}
_NUM_UNIT_RE = re.compile(
    r"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)\s*(?:(k|K|M|m|µ|μ|u)\s*)?"
    r"(V|A|W|volts?|voltios?|amperes?|amperios?|amps?|watts?)?(?![A-Za-z0-9_])"
)
_MESH_LETTER_RE = re.compile(r"(?<![A-Za-z_])[iI]\s*_?\s*([a-hA-H])(?![A-Za-z0-9_])")


def _clean(text: str) -> str:
    t = text.replace("−", "-").replace("–", "-").replace("×", "*").replace("·", "*")
    t = t.replace("÷", "/").replace("Ω", "").replace("ohms", "").replace("ohm", "")
    t = re.sub(r"(?<=\d),(?=\d{1,3}\b)", ".", t)  # 1,364 -> 1.364
    return t


def _strip_units(t: str) -> str:
    """'100 V' -> '100', '2 mA' -> '0.002', '2k' -> '2000'; deja '2V1' intacto."""
    def rep(m):
        num, pre, unit = m.group(1), m.group(2), m.group(3)
        if not pre and not unit:
            return m.group(0)
        if pre and pre not in "kKM" and not unit:
            return m.group(0)     # "2 m" sin unidad es ambiguo
        return repr(float(num) * _UNIT_PREFIX.get(pre, 1.0)) if pre else num
    return _NUM_UNIT_RE.sub(rep, t)


def normalize_vars(text: str, allowed: list[str]) -> str:
    """'i_1', 'I 1', 'i1' -> 'I1'; 'v_a', 'Va' -> 'Va' si está permitido.

    Con corrientes de malla I1..In también acepta la notación con letras
    del material de clase: ia, ib, ic... -> I1, I2, I3..."""
    canon = {a.lower(): a for a in allowed}

    def rep(m):
        cand = (m.group(1) + m.group(2)).lower()
        return canon.get(cand, m.group(0))
    text = _VAR_RE.sub(rep, text)
    if allowed and all(re.fullmatch(r"I\d+", a) for a in allowed):
        def letter(m):
            k = ord(m.group(1).lower()) - ord("a") + 1
            return f"I{k}" if f"I{k}" in allowed else m.group(0)
        text = _MESH_LETTER_RE.sub(letter, text)
    return text


def _split_identifiers(t: str, names: list[str]) -> str:
    """Separa productos implícitos entre nombres conocidos: 'I2R3' -> 'I2*R3'."""
    by_lower: dict[str, str] = {}
    for n in sorted(names, key=len, reverse=True):
        by_lower.setdefault(n.lower(), n)

    def segment(word: str) -> list[str] | None:
        if not word:
            return []
        if word in names:
            return [word]
        for size in range(len(word), 0, -1):
            head = word[:size]
            hit = head if head in names else by_lower.get(head.lower())
            if hit:
                rest = segment(word[size:])
                if rest is not None:
                    return [hit] + rest
        return None

    def rep(m):
        parts = segment(m.group(0))
        return "*".join(parts) if parts else m.group(0)
    return re.sub(r"[A-Za-z_][A-Za-z0-9_]*", rep, t)


def prepare(text: str, allowed: list[str], constants: dict[str, float] | None = None) -> str:
    """Normaliza lo que escribe el alumno antes de leerlo con SymPy."""
    t = _strip_units(_clean(text))
    t = normalize_vars(t, allowed)
    return _split_identifiers(t, list(allowed) + list(constants or {}))


@dataclass
class ParsedEq:
    coefs: dict[str, float]
    rhs: float
    raw: str


class ParseError(ValueError):
    pass


def _local_dict(names: list[str]) -> dict:
    return {v: sp.Symbol(v) for v in names}


def parse_expression(text: str, allowed: list[str],
                     constants: dict[str, float] | None = None) -> sp.Expr:
    """Lee una expresión. `constants` son nombres de elementos (R1, VX…) que el
    alumno puede usar en forma simbólica; se sustituyen por su valor."""
    constants = {k: v for k, v in (constants or {}).items() if k not in allowed}
    t = prepare(text, allowed, constants)
    t = re.sub(r"\b[Aa]\b|\bamperes?\b|\bvolts?\b", "", t)
    names = list(allowed) + list(constants)
    loose = [n for n in dict.fromkeys(_LOOSE_NAME_RE.findall(t)) if n not in names]
    try:
        expr = parse_expr(t, local_dict=_local_dict(names + loose), transformations=_TRANSFORMS)
    except Exception as exc:  # noqa: BLE001 - errores de sintaxis del alumno
        raise ParseError(f"No pude leer la expresión '{text}'.") from exc
    if not isinstance(expr, sp.Expr):
        raise ParseError(f"No pude leer la expresión '{text}'.")
    expr = expr.subs({sp.Symbol(k): sp.nsimplify(v) for k, v in constants.items()})
    unknown = {str(s) for s in expr.free_symbols} - set(allowed)
    if unknown:
        raise ParseError(f"No reconozco: {', '.join(sorted(unknown))}. Usa {', '.join(allowed)}.")
    return expr


def looks_like_equation(text: str, allowed: list[str]) -> bool:
    t = prepare(text, allowed)
    return "=" in t and any(re.search(rf"\b{v}\b", t) or re.search(rf"\d{v}\b", t) for v in allowed)


def split_equations(text: str) -> list[str]:
    """Separa varias ecuaciones escritas en un solo mensaje."""
    t = _clean(text)
    parts = re.split(r"[\n;]|(?<=\d)\s*,\s+(?=[\-\d(IiVv])|\s+y\s+", t)
    return [p.strip() for p in parts if "=" in p]


def parse_linear_equation(text: str, allowed: list[str],
                          constants: dict[str, float] | None = None) -> ParsedEq:
    t = _clean(text)
    # quitarle al texto prefacios tipo "malla 1:" o "por lo tanto"
    t = re.sub(r"^[^=]*?:\s*", "", t) if ":" in t.split("=")[0] else t
    if t.count("=") != 1:
        raise ParseError("Escribe una sola ecuación con un signo '='.")
    lhs, rhs = t.split("=")
    expr = parse_expression(lhs, allowed, constants) - parse_expression(rhs, allowed, constants)
    expr = sp.expand(expr)
    syms = [sp.Symbol(v) for v in allowed]
    poly_ok = all(sp.degree(expr, s) <= 1 for s in syms if expr.has(s))
    cross = any(expr.coeff(a).has(b) for a in syms for b in syms if a != b)
    if not poly_ok or cross:
        raise ParseError("La ecuación debe ser lineal en las incógnitas.")
    try:
        coefs = {v: float(expr.coeff(sp.Symbol(v))) for v in allowed}
        const = float(expr.subs({s: 0 for s in syms}))
    except TypeError as exc:   # complejos, p. ej. sqrt(-1)
        raise ParseError(f"No pude leer la ecuación '{text}'.") from exc
    if not all(math.isfinite(x) for x in [*coefs.values(), const]):
        raise ParseError(f"No pude leer la ecuación '{text}'.")
    if all(abs(c) < 1e-12 for c in coefs.values()):
        raise ParseError("La ecuación no contiene incógnitas.")
    return ParsedEq(coefs, -const, text)


# ------------------------------------------------------------------ números
_NUM_RE = re.compile(
    r"(?:(?P<var>\b[A-Za-z]{1,3}_?\{?[A-Za-z0-9]{0,3}\}?)\s*(?:=|es|vale|:)\s*)?"
    # la unidad va en el mismo renglón y no es el inicio de un nombre: en "= -7⏎V1 = 2" la V es de V1
    r"(?<![A-Za-z0-9_.])(?P<num>[+-]?\s?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)[ \t]*"
    r"(?P<pre>[kmuµμM])?[ \t]*(?P<unit>(?:Ω|ohms?|A|V|W|amperes?|volts?|watts?)(?![A-Za-z0-9_]))?"
)


@dataclass
class NumClaim:
    var: str | None
    value: float
    unit: str | None


def parse_numbers(text: str, allowed_vars: list[str] | None = None) -> list[NumClaim]:
    """'I1 = 2.45 A, I2=1364 mA' -> [NumClaim('I1',2.45,'A'), NumClaim('I2',1.364,'A')]."""
    t = _clean(text)
    if allowed_vars:
        t = normalize_vars(t, allowed_vars)
    out = []
    prev_end, prev_var = -1, None
    for m in _NUM_RE.finditer(t):
        # "I1 = -3 A = 3 A": el segundo número es la magnitud del mismo valor
        chained = prev_var and not m.group("var") and re.fullmatch(r"\s*=\s*", t[prev_end:m.start()])
        prev_end = m.end()
        if chained:
            continue
        prev_var = m.group("var")
        num = float(m.group("num").replace(" ", "").replace(",", "."))
        pre, unit = m.group("pre") or "", m.group("unit")
        if pre and not unit and pre in "mM":
            # "12 m" sin unidad es ambiguo; sólo aceptamos prefijo si hay unidad
            pre = ""
        num *= _PREFIX.get(pre, 1.0)
        u = None
        if unit:
            u = {"o": "Ω", "Ω": "Ω", "a": "A", "v": "V", "w": "W"}[unit[0].lower() if unit[0] != "Ω" else "Ω"]
        var = m.group("var")
        if var and allowed_vars:
            var = normalize_vars(var, allowed_vars)
        out.append(NumClaim(var, num, u))
    return out


# --------------------------------------------------------- referencias a elementos
_REF_RE = re.compile(
    r"\b(?P<name>[RVIE]\d+[a-z]?)\b|(?P<num>\d+(?:[.,]\d+)?)\s*(?P<pre>[kmM])?\s*(?P<unit>Ω|ohms?|ohmios?|V\b|volts?|A\b|amp\w*)?",
    re.IGNORECASE,
)


def element_refs(text: str, circuit: Circuit) -> list[list[Element]]:
    """Cada referencia -> lista de elementos candidatos (por nombre o por valor)."""
    t = text.replace("Ω", " Ω")
    refs: list[list[Element]] = []
    names = {e.name.lower(): e for e in circuit.elements}
    # nombres reales del circuito (VX, IY, VF1…), además de la forma R1/V2/I3
    alts = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    ref_re = re.compile(rf"\b(?P<name>{alts}|[RVIE]\d+[a-z]?)\b|" + _REF_RE.pattern.split("|", 1)[1],
                        re.IGNORECASE) if alts else _REF_RE
    for m in ref_re.finditer(t):
        if m.group("name"):
            e = names.get(m.group("name").lower())
            if e:
                refs.append([e])
            continue
        num = float(m.group("num").replace(",", "."))
        pre = m.group("pre") or ""
        num *= {"k": 1e3, "K": 1e3, "m": 1e-3, "M": 1e6}.get(pre, 1.0)
        unit = (m.group("unit") or "").lower()
        kind = "R" if unit.startswith(("ω", "ohm")) else "V" if unit.startswith("v") else \
            "I" if unit.startswith("a") else None
        cands = [e for e in circuit.elements
                 if abs(float(e.value) - num) <= 1e-9 * max(1, num) and (kind is None or e.kind == kind)]
        if cands:
            refs.append(cands)
    return refs


def match_element_set(refs: list[list[Element]], pool: list[str]) -> set[str] | None:
    """Asigna cada referencia a un elemento distinto del conjunto `pool`.
    Devuelve el conjunto asignado o None si alguna referencia no cabe."""
    chosen: set[str] = set()

    def bt(k: int) -> bool:
        if k == len(refs):
            return True
        for e in refs[k]:
            if e.name in pool and e.name not in chosen:
                chosen.add(e.name)
                if bt(k + 1):
                    return True
                chosen.discard(e.name)
        return False
    return set(chosen) if bt(0) else None
