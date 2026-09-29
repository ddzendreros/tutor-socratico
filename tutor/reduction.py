"""Reducción serie/paralelo paso a paso, como se hace en clase.

Cada combinación válida produce un equivalente R_T1, R_T2, ... que el alumno
puede seguir usando por nombre o por valor. Así se aceptan los pasos
intermedios y se detecta el error de fondo del tema: combinar resistencias que
no están ni en serie ni en paralelo.

Criterios (vistos desde las terminales a-b, con las demás fuentes apagadas):
  - serie: dos resistencias unidas por un nodo al que no se conecta nada más
    y que no es terminal;
  - paralelo: resistencias conectadas a los mismos dos nodos;
  - en corto: ambas terminales en el mismo nodo (no aportan);
  - sueltas: un extremo no conecta con nada (no llevan corriente).
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field

import sympy as sp

from .circuit import Circuit, CircuitError, fmt
from .solver import equivalent_resistance

TOL = 0.015


@dataclass
class Res:
    name: str
    n1: str
    n2: str
    value: sp.Rational
    parts: tuple[str, ...]

    @property
    def label(self) -> str:
        return f"{display(self.name)} ({fmt(self.value)} Ω)"


@dataclass
class Move:
    kind: str                 # "serie" | "paralelo"
    members: tuple[str, ...]
    value: sp.Rational


@dataclass
class Check:
    ok: bool
    code: str = "OK"
    detail: dict = field(default_factory=dict)
    message: str = ""
    no_penalty: bool = False


def display(name: str) -> str:
    m = re.fullmatch(r"RT(\d+)", name)
    return f"R_T{m.group(1)}" if m else name


def _close(a: float, b: float, tol: float = TOL) -> bool:
    return abs(a - b) <= tol * max(abs(b), 1e-9)


def _par(values) -> sp.Rational:
    return 1 / sum(1 / v for v in values)


class Reducer:
    def __init__(self, circuit: Circuit, a: str, b: str, exclude: str | None = None):
        parent: dict[str, str] = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        # fuentes de voltaje apagadas = corto: unen sus nodos
        for e in circuit.elements:
            if e.kind == "V" and e.name != exclude:
                parent[find(e.n1)] = find(e.n2)
        self.a, self.b = find(a), find(b)
        if self.a == self.b:
            raise CircuitError("Las terminales quedan en corto: la resistencia equivalente es 0.")
        self.items: dict[str, Res] = {}
        for e in circuit.by_kind("R"):
            self.items[e.name] = Res(e.name, find(e.n1), find(e.n2), e.value, (e.name,))
        self.original = dict(self.items)
        self.shorted = {n for n, r in self.items.items() if r.n1 == r.n2}
        self.removed: set[str] = set()
        self.counter = 0
        self.history: list[tuple[Move, str]] = []
        self.req = equivalent_resistance(circuit, a, b, exclude=exclude)
        self._prune()

    # ------------------------------------------------------------ topología
    def _live(self) -> dict[str, Res]:
        return {n: r for n, r in self.items.items() if n not in self.shorted and n not in self.removed}

    def _prune(self) -> None:
        """Resistencias sueltas (un extremo sin conexión) no llevan corriente."""
        changed = True
        while changed:
            changed = False
            deg = self._degree()
            for n, r in self._live().items():
                for node in (r.n1, r.n2):
                    if deg.get(node, 0) == 1 and node not in (self.a, self.b):
                        self.removed.add(n)
                        changed = True
                        break
                if changed:
                    break

    def _degree(self) -> dict[str, int]:
        deg: dict[str, int] = {}
        for r in self._live().values():
            for node in (r.n1, r.n2):
                deg[node] = deg.get(node, 0) + 1
        return deg

    @property
    def done(self) -> bool:
        live = self._live()
        return len(live) == 1 and {next(iter(live.values())).n1, next(iter(live.values())).n2} == {self.a, self.b}

    @property
    def stuck(self) -> bool:
        """Sin combinaciones serie/paralelo posibles (hace falta delta-estrella)."""
        return not self.done and not self.moves()

    def is_parallel(self, names) -> bool:
        live = self._live()
        if len(names) < 2 or any(n not in live for n in names):
            return False
        pairs = {frozenset((live[n].n1, live[n].n2)) for n in names}
        return len(pairs) == 1

    def is_series(self, names) -> bool:
        live = self._live()
        if len(names) < 2 or any(n not in live for n in names):
            return False
        deg = self._degree()
        count: dict[str, int] = {}
        for n in names:
            for node in (live[n].n1, live[n].n2):
                count[node] = count.get(node, 0) + 1
        ends = [nd for nd, c in count.items() if c == 1]
        inner = [nd for nd, c in count.items() if c == 2]
        if len(ends) != 2 or len(inner) != len(names) - 1 or any(c > 2 for c in count.values()):
            return False
        return all(deg[nd] == 2 and nd not in (self.a, self.b) for nd in inner)

    def moves(self) -> list[Move]:
        live = self._live()
        out: list[Move] = []
        names = list(live)
        for size in range(2, len(names) + 1):
            for combo in itertools.combinations(names, size):
                if self.is_parallel(combo):
                    out.append(Move("paralelo", combo, _par([live[n].value for n in combo])))
                elif self.is_series(combo):
                    out.append(Move("serie", combo, sum(live[n].value for n in combo)))
        return out

    def apply(self, move: Move) -> Res:
        live = self._live()
        members = [live[n] for n in move.members]
        if move.kind == "paralelo":
            n1, n2 = members[0].n1, members[0].n2
        else:
            count: dict[str, int] = {}
            for r in members:
                for node in (r.n1, r.n2):
                    count[node] = count.get(node, 0) + 1
            n1, n2 = [nd for nd, c in count.items() if c == 1]
        self.counter += 1
        new = Res(f"RT{self.counter}", n1, n2, sp.nsimplify(move.value),
                  tuple(p for r in members for p in r.parts))
        for r in members:
            del self.items[r.name]
        self.items[new.name] = new
        self.history.append((move, new.name))
        self._prune()
        return new

    # ------------------------------------------------------------- lectura
    def _refs(self, text: str) -> tuple[list[list[str]], list[float]]:
        """Referencias a resistencias (por nombre o valor) y números sueltos."""
        live = self._live()
        known = {**live, **{n: self.items[n] for n in self.shorted}}
        refs: list[list[str]] = []
        t = text.replace("Ω", " Ω ")
        spans = []
        for m in re.finditer(r"\bR\s*_?\s*[Tt]\s*_?\s*(\d+)\b|\b([Rr]\s*_?\s*\w+)\b", t):
            if m.group(1):
                name = f"RT{m.group(1)}"
            else:
                name = re.sub(r"[\s_]", "", m.group(2))
                name = next((n for n in known if n.lower() == name.lower()), None)
            if name and name in known:
                refs.append([name])
                spans.append(m.span())
        numbers: list[float] = []
        for m in re.finditer(r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*(k|K|M)?", t):
            if any(s <= m.start() < e for s, e in spans):
                continue
            v = float(m.group(1).replace(",", ".")) * {"k": 1e3, "K": 1e3, "M": 1e6}.get(m.group(2) or "", 1)
            numbers.append((m.start(), v))
        # el resultado es el primer número después del último "=" (o el último número)
        result = None
        eq = t.rfind("=")
        after = [v for pos, v in numbers if pos > eq] if eq >= 0 else []
        if after:
            result = after[0]
        elif numbers:
            result = numbers[-1][1]
        before = [v for pos, v in numbers if eq < 0 or pos < eq]
        if eq < 0 and numbers:
            before = before[:-1]
        named = {r[0] for r in refs}
        dedup: list[list[str]] = []
        for r in refs:
            if r not in dedup:
                dedup.append(r)
        refs = dedup
        for v in before:
            cands = [n for n, r in known.items() if _close(float(r.value), v, 0.005)]
            # "R2 y R3: 30*60/90" -> los valores repiten a las ya nombradas
            if cands and not (set(cands) & named):
                refs.append(cands)
        return refs, ([result] if result is not None else [])

    def _assign(self, refs: list[list[str]]) -> list[tuple[str, ...]]:
        """Asignaciones posibles de referencias a resistencias distintas."""
        out = []
        for combo in itertools.product(*refs):
            if len(set(combo)) == len(combo):
                out.append(tuple(combo))
        return out[:200]

    # ------------------------------------------------------------ diagnóstico
    def check(self, text: str) -> Check | None:
        low = text.lower()
        relation = "paralelo" if re.search(r"paralel|\|\||∥", low) else \
            "serie" if re.search(r"\bserie\b", low) else None
        refs, result = self._refs(text)
        value = result[0] if result else None

        # "R5 está en corto"
        if re.search(r"\bcorto", low) and refs:
            named = {n for r in refs for n in r if len(r) == 1}
            hit = named & self.shorted
            if hit:
                return Check(True, message=f"Exacto: {', '.join(sorted(hit))} está en corto y no aporta nada.",
                             no_penalty=True)

        if len(refs) >= 2:
            options = self._assign(refs)
            for combo in options:
                if set(combo) & self.shorted:
                    continue
                if self.is_parallel(combo) or self.is_series(combo):
                    return self._judge_group(combo, relation, value)
            combo = options[0] if options else tuple(r[0] for r in refs)
            shorted = set(combo) & self.shorted
            if shorted:
                return Check(False, "SP_SHORTED_INCLUDED", {"elemento": ", ".join(sorted(shorted))})
            return Check(False, "SP_NOT_COMBINABLE", {
                "elementos": " y ".join(display(n) for n in combo),
                "relacion": relation or "serie o en paralelo"})

        if value is None:
            return None
        return self._judge_value(value)

    def _judge_group(self, combo, relation, value) -> Check:
        live = self._live()
        vals = [live[n].value for n in combo]
        par, ser = float(_par(vals)), float(sum(vals))
        actual = "paralelo" if self.is_parallel(combo) else "serie"
        names = " y ".join(display(n) for n in combo)
        if relation and relation != actual:
            code = "SP_PARALLEL_SUMMED" if actual == "paralelo" else "SP_SERIES_AS_PARALLEL"
            return Check(False, code, {"elementos": names})
        if value is None:
            return Check(False, "ELABORATE", no_penalty=True, message=(
                f"Bien: {names} están en {actual}. ¿Cuánto vale su resistencia equivalente?"))
        expected = par if actual == "paralelo" else ser
        if _close(value, expected):
            return self._accept(Move(actual, tuple(combo), sp.nsimplify(expected)))
        if actual == "paralelo" and _close(value, ser):
            return Check(False, "SP_PARALLEL_SUMMED", {"elementos": names})
        if actual == "serie" and _close(value, par):
            return Check(False, "SP_SERIES_AS_PARALLEL", {"elementos": names})
        return Check(False, "VALUE_WRONG", {"var": f"el equivalente de {names}", "elementos": names})

    def _accept(self, move: Move) -> Check:
        sym = " ∥ " if move.kind == "paralelo" else " + "
        new = self.apply(move)
        msg = f"Correcto: {sym.join(display(n) for n in move.members)} = {fmt(move.value)} Ω."
        if not self.done:
            msg += f" Llamémosla {display(new.name)}."
        return Check(True, message=msg)

    def _judge_value(self, value: float) -> Check:
        if _close(value, float(self.req)):
            while not self.done and self.moves():
                self.apply(self.moves()[0])
            return Check(True, message=f"Correcto: la resistencia equivalente es {fmt(self.req)} Ω.")
        for mv in self.moves():
            if _close(value, float(mv.value)):
                return self._accept(mv)
        seq = self._search_sequence(value)
        if seq:
            parts = []
            for mv in seq:
                sym = " ∥ " if mv.kind == "paralelo" else " + "
                new = self.apply(mv)
                parts.append(f"{sym.join(display(n) for n in mv.members)} = {fmt(mv.value)} Ω"
                             + ("" if self.done else f" ({display(new.name)})"))
            return Check(True, message="Correcto, hiciste varios pasos a la vez: " + "; ".join(parts) + ".")
        # errores típicos, del más específico al más general
        live = self._live()
        for mv in self.moves():
            vals = [live[n].value for n in mv.members]
            names = " y ".join(display(n) for n in mv.members)
            if mv.kind == "paralelo" and _close(value, float(sum(vals))):
                return Check(False, "SP_PARALLEL_SUMMED", {"elementos": names})
            if mv.kind == "serie" and _close(value, float(_par(vals))):
                return Check(False, "SP_SERIES_AS_PARALLEL", {"elementos": names})
        all_r = [r.value for r in self.original.values()]
        if len(all_r) > 1 and _close(value, float(sum(all_r))) and not self.is_series(tuple(live)):
            return Check(False, "SP_PARALLEL_SUMMED", {"elementos": "todas las resistencias"})
        names = list(live)
        for size in (2, 3):
            for combo in itertools.combinations(names, size):
                if self.is_parallel(combo) or self.is_series(combo):
                    continue
                vals = [live[n].value for n in combo]
                if _close(value, float(sum(vals))) or _close(value, float(_par(vals))):
                    return Check(False, "SP_NOT_COMBINABLE", {
                        "elementos": " y ".join(display(n) for n in combo),
                        "relacion": "serie o en paralelo"})
        for n in self.shorted:
            with_short = float(self.req) + float(self.items[n].value)
            if _close(value, with_short):
                return Check(False, "SP_SHORTED_INCLUDED", {"elemento": n})
        return Check(False, "VALUE_WRONG", {"var": "la resistencia equivalente"})

    def _search_sequence(self, value: float, depth: int = 3) -> list[Move] | None:
        """¿El valor es el resultado de encadenar varias combinaciones válidas?"""
        start = (dict(self.items), set(self.removed), self.counter, list(self.history))

        def restore(state):
            self.items, self.removed, self.counter, self.history = \
                dict(state[0]), set(state[1]), state[2], list(state[3])

        def dfs(level, path):
            if level == 0:
                return None
            for mv in self.moves():
                state = (dict(self.items), set(self.removed), self.counter, list(self.history))
                new = self.apply(mv)
                if len(path) >= 1 and _close(value, float(new.value)):
                    restore(state)
                    return path + [mv]
                found = dfs(level - 1, path + [mv])
                restore(state)
                if found:
                    return found
            return None

        try:
            found = dfs(depth, [])
        finally:
            restore(start)
        if not found:
            return None
        # rehacer la secuencia con nombres de los miembros actuales
        return found

    # --------------------------------------------------------------- pistas
    def next_move_hint(self) -> str:
        mv = next(iter(self.moves()), None)
        if mv is None:
            return ("Aquí ya no hay resistencias en serie ni en paralelo: se necesita una conversión "
                    "delta-estrella. ¿Qué tres resistencias forman la delta?")
        live = self._live()
        names = " y ".join(display(n) for n in mv.members)
        if mv.kind == "paralelo":
            r = live[mv.members[0]]
            return (f"Observa {names}: están conectadas a los mismos dos nodos ({r.n1} y {r.n2}). "
                    "¿Cuánto vale su equivalente?")
        return (f"Observa {names}: entre ellas hay un nodo al que no se conecta nada más. "
                "¿Cuánto vale su equivalente?")

    def state_text(self) -> str:
        return ", ".join(r.label for r in self._live().values())
