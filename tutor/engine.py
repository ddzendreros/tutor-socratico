"""Motor del tutor socrático: plan de pasos, escalera de andamiaje y registro.

El LLM no decide nada aquí. Este módulo decide:
  - qué paso cognitivo sigue (plan generado a partir del circuito),
  - si la respuesta del alumno es correcta (diagnose.py),
  - qué nivel de ayuda (0–5) corresponde,
  - qué texto base se dice (banco de errores + plantillas del paso).
El LLM, si está disponible, sólo reescribe ese texto con naturalidad (ver llm.py).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import sympy as sp

from . import analysis as an
from .circuit import Circuit, CircuitError, Target, fmt
from .diagnose import (
    Diagnosis, check_system_values, close, diagnose_equation, diagnose_value,
    mesh_mappings, series_parallel_mutations,
)
from .errorbank import ErrorBank, fill
from .mathparse import (
    ParseError, element_refs, looks_like_equation, match_element_set,
    parse_expression, parse_linear_equation, parse_numbers, split_equations,
)
from .reduction import Reducer
from .solver import solve, target_unit, target_value

METHODS = {"ohm": "Ley de Ohm y potencia", "serie_paralelo": "Serie/paralelo",
           "mallas": "Método de mallas", "nodos": "Método de nodos"}
TYPE_WORDS = {"fantasma": "fantasma", "super": None, "real": "real"}
TABLE_TOL = 0.02
JUSTIFY_KINDS = {"shared_expr", "mesh_types", "node_types", "concept", "reduce", "shared_id"}
MODES = {"aprender": "Aprender", "practicar": "Practicar",
         "verificar": "Verificar", "simulador": "Simulador"}

DIRECT_RE = re.compile(
    r"(dame|dime|p[aá]same|quiero|escribe)\s+(ya\s+)?(la|el|las|los)?\s*(respuesta|soluci[oó]n|resultado|valores?)"
    r"|resu[eé]lve(lo|la|me)|s[oó]lo\s+dime|hazlo\s+t[uú]|dame\s+todo", re.I)
HELP_RE = re.compile(
    r"^\s*(no\s+s[eé]|ni\s+idea|ayuda|ay[uú]dame|pista|dame\s+una\s+pista|no\s+entiendo|no\s+le\s+entiendo"
    r"|no\s+me\s+sale|me\s+rindo|help|\?+)\b.{0,40}$", re.I)
NO_PENALTY = {"PARSE_ERROR", "ELABORATE", "AHEAD"}
NUM_WORDS = {"una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6}


@dataclass
class Step:
    id: str
    kind: str
    items: list = field(default_factory=list)
    done: list = field(default_factory=list)
    data: dict = field(default_factory=dict)

    @property
    def pending(self) -> list:
        return [i for i in self.items if i not in self.done]

    @property
    def complete(self) -> bool:
        return not self.pending


@dataclass
class Outcome:
    ok: bool
    code: str = "OK"
    detail: dict = field(default_factory=dict)
    item: object = None
    partial_msg: str = ""


class Session:
    def __init__(self, circuit: Circuit, method: str, mode: str = "aprender",
                 bank: ErrorBank | None = None):
        if method not in METHODS:
            raise ValueError(f"Método desconocido: {method}")
        self.circuit, self.method, self.mode = circuit, method, mode
        self.bank = bank or ErrorBank()
        self.sol = solve(circuit)
        self.loops: list[an.Loop] = []
        self.refs: list[an.RefEq] = []
        self.mappings: list[tuple] | None = None
        self.vars: list[str] = []
        # nombres de elementos que el alumno puede usar en forma simbólica (R1, VX…)
        self.constants = {e.name: float(e.value) for e in circuit.elements}
        self.types: dict = {}
        self.meshes_guessed = method == "mallas" and not circuit.meshes
        if method == "mallas":
            self.loops = an.default_meshes(circuit)
            self.refs = an.mesh_system(circuit, self.loops)
            self.vars = [f"I{k+1}" for k in range(len(self.loops))]
            # con mallas declaradas sólo cabe el sentido global (horario/antihorario)
            self.mappings = ([tuple((k, s) for k in range(len(self.loops))) for s in (1, -1)]
                             if circuit.meshes else
                             mesh_mappings(len(self.loops)) if len(self.loops) <= 4 else None)
            self.types = an.mesh_types(circuit, self.loops)
        elif method == "nodos":
            self.refs = an.nodal_system(circuit)
            self.vars = [an.node_var(n) for n in circuit.non_ground_nodes]
            self.types = an.node_types(circuit)
        self.truth: dict[str, float] = {}
        if self.refs:
            self.truth = {k: float(v) for k, v in an.solve_system(self.refs).items()}
        self.targets = list(circuit.targets)
        self.want_table = any(t.quantity == "VIP" for t in self.targets)
        self.targets = [t for t in self.targets if t.quantity != "VIP"]
        if method in ("ohm", "serie_paralelo") and not self.targets and not self.want_table:
            self.targets = self._default_targets()
        self.reducer: Reducer | None = None
        if method == "serie_paralelo":
            req = next((t for t in self.targets if t.quantity == "Req"), None)
            if req is not None:
                if req.element:
                    e = circuit.get(req.element)
                    self.reducer = Reducer(circuit, e.n1, e.n2, exclude=e.name)
                else:
                    self.reducer = Reducer(circuit, *req.nodes)
                self.req_target = req
                self.targets = [t for t in self.targets if t is not req]
        self.target_truth = [float(target_value(circuit, self.sol, t)) for t in self.targets]
        self.table_rows = [e.name for e in circuit.by_kind("R") if e.n1 != e.n2]

        self.steps = self._build_plan()
        self.idx = 0
        self.level = 0
        self.item_failed = False
        self.last_code: str | None = None
        self.last_detail: dict = {}
        self.assign: dict[int, int] = {}   # malla del alumno -> lazo interno
        self.finished = False
        self.c = {"intentos": 0, "preguntas": 0, "pistas": {1: 0, 2: 0, 3: 0, 4: 0, 5: 0},
                  "conceptuales": 0, "procedimentales": 0, "autonoma": False,
                  "directa": 0, "simulador": False, "nivel_final": 0, "errores": [],
                  "justificaciones": 0, "balance": False}
        self.transcript: list[dict] = []
        self.t0 = time.time()
        self.metacog_asked = False
        self.justify_pending: str | None = None
        self._why = False

    # ================================================================ plan
    def _default_targets(self) -> list[Target]:
        srcs = self.circuit.by_kind("V") + self.circuit.by_kind("I")
        ts = [Target("I", e.name) for e in self.circuit.by_kind("R")]
        if len(srcs) == 1 and srcs[0].kind == "V" and self.method == "serie_paralelo":
            ts = [Target("Req", srcs[0].name)] + ts
        return ts

    def _build_plan(self) -> list[Step]:
        m, mode = self.method, self.mode
        micro = mode in ("aprender", "simulador")
        steps: list[Step] = []
        if mode == "verificar":
            steps.append(Step("submit", "submit"))
        elif micro:
            steps.append(Step("intro", "intro"))
        if m == "mallas":
            if micro:
                steps.append(Step("mesh_count", "mesh_count"))
                steps.append(Step("mesh_define", "mesh_define", items=list(range(len(self.loops)))))
                if self.circuit.by_kind("I"):
                    steps.append(Step("mesh_types", "mesh_types", items=list(range(len(self.loops)))))
                shared = an.shared_elements(self.loops)
                if shared:
                    steps.append(Step("shared_id", "shared_id", items=["shared"],
                                      data={"shared": shared}))
                    steps.append(Step("shared_expr", "shared_expr", items=self._shared_items(shared),
                                      data={"shared": shared}))
            steps.append(Step("equations", "equations", items=list(range(len(self.refs)))))
            steps.append(Step("values", "values", items=list(self.vars)))
        elif m == "nodos":
            if micro:
                steps.append(Step("node_count", "node_count"))
                if self.circuit.by_kind("V"):
                    steps.append(Step("node_types", "node_types",
                                      items=list(self.circuit.non_ground_nodes)))
            steps.append(Step("equations", "equations", items=list(range(len(self.refs)))))
            steps.append(Step("values", "values", items=list(self.vars)))
        elif m == "serie_paralelo":
            if micro:
                steps.append(Step("concept", "concept", items=["c"], data=self._sp_concept()))
            if self.reducer is not None:
                steps.append(Step("reduce", "reduce", items=["req"]))
        elif m == "ohm":
            if micro:
                steps.append(Step("concept", "concept", items=["c"], data={
                    "question": "¿Qué ley relaciona el voltaje, la corriente y la resistencia de un elemento?",
                    "ok": [r"ohm", r"v\s*=\s*(r\s*\*?\s*i|i\s*\*?\s*r)", r"i\s*=\s*v\s*/\s*r"],
                    "bad": {},
                    "hint": "La Ley de Ohm: V = R·I."}))
        if self.targets:
            steps.append(Step("targets", "targets", items=list(range(len(self.targets)))))
        if self.want_table:
            steps.append(Step("table", "table", items=list(self.table_rows)))
        steps.append(Step("verify", "verify", items=["v"]))
        if mode == "simulador":
            steps.append(Step("sim", "sim", items=["s"]))
        steps.append(Step("metacog", "metacog", items=["m"]))
        return steps

    def _shared_items(self, shared: dict) -> list[str]:
        """Un elemento por rama compartida (de preferencia una resistencia), máximo dos."""
        by_pair: dict[tuple, str] = {}
        for name, pair in shared.items():
            cur = by_pair.get(pair)
            if cur is None or (self.circuit.get(cur).kind != "R" and self.circuit.get(name).kind == "R"):
                by_pair[pair] = name
        return list(by_pair.values())[:2]

    def _sp_concept(self) -> dict:
        single_loop = len(self.circuit.elements) - len(self.circuit.nodes) + 1 == 1
        if single_loop:
            return {"question": "Antes de calcular, dime: ¿qué característica tiene la corriente "
                                "cuando varias resistencias están conectadas en serie?",
                    "ok": [r"misma", r"igual", r"no cambia", r"constante", r"única", r"la misma"],
                    "bad": {r"diferente|distinta|cambia|se reparte|se divide": "SERIES_CURRENT_CONCEPT"},
                    "hint": "En serie hay un solo camino: la misma corriente pasa por todos los elementos."}
        return {"question": "Antes de calcular, dime: ¿qué comparten dos resistencias conectadas "
                            "en paralelo: la corriente o el voltaje?",
                "ok": [r"voltaje|tensi[oó]n|diferencia de potencial"],
                "bad": {r"^\s*(la\s+)?corriente": "SP_PARALLEL_SUMMED"},
                "hint": "En paralelo los elementos están conectados a los mismos dos nodos: comparten el voltaje."}

    # ============================================================ helpers texto
    @property
    def step(self) -> Step:
        return self.steps[self.idx]

    def _loop_text(self, k: int) -> str:
        items = self.loops[k].items
        srcs = [i for i, (n, _) in enumerate(items) if self.circuit.get(n).kind != "R"]
        start = srcs[0] if srcs else 0
        order = items[start:] + items[:start]
        return ", ".join(self.circuit.get(n).label for n, _ in order)

    def _branch_expr(self, k: int, name: str) -> str:
        """Corriente en `name` en el sentido de la malla k, p.ej. 'I1 − I2'."""
        dk = self.loops[k].dir(name)
        s = ""
        for m, lp in enumerate(self.loops):
            d = lp.dir(name)
            if d == 0:
                continue
            sign = dk * d
            if not s:
                s = f"I{m+1}" if sign > 0 else f"−I{m+1}"
            else:
                s += f" + I{m+1}" if sign > 0 else f" − I{m+1}"
        return s

    def _guided_kvl(self, k: int) -> str:
        items = self.loops[k].items
        srcs = [i for i, (n, _) in enumerate(items) if self.circuit.get(n).kind != "R"]
        start = srcs[0] if srcs else 0
        text = ""
        for name, d in items[start:] + items[:start]:
            e = self.circuit.get(name)
            if e.kind == "V":
                rise = -d * e.value
                term = f"{fmt(abs(rise))}"
                text += (term if not text else f" + {term}") if rise > 0 else f" − {term}"
            elif e.kind == "R":
                cur = self._branch_expr(k, name)
                cur = f"({cur})" if (" " in cur) else cur
                text += f" − {fmt(e.value)}·{cur}" if text else f"−{fmt(e.value)}·{cur}"
            else:
                text += f" + V_{e.name}"
        return f"{text} = 0"

    def _format_equation(self, q: an.RefEq) -> str | None:
        """Ecuación con valores en el formato de clase: I1(10 + 20) − I2(20) = 100 − 100."""
        def rhs_text(contribs) -> str:
            out = ""
            for c in contribs:
                c = sp.nsimplify(c)
                if c == 0:
                    continue
                t = fmt(abs(c))
                out += (t if not out else f" + {t}") if c > 0 else (f"−{t}" if not out else f" − {t}")
            return out or "0"

        if self.method == "mallas" and q.kind == "kvl":
            m = q.owner[0]
            own = [self.circuit.get(n) for n in q.parts.get("R", [])]
            text = f"I{m+1}(" + " + ".join(fmt(e.value) for e in own) + ")"
            for j, lp in enumerate(self.loops):
                if j == m:
                    continue
                shared = [e for e in own if lp.dir(e.name)]
                if shared:
                    sign = "−" if q.coefs[f"I{j+1}"] < 0 else "+"
                    text += f" {sign} I{j+1}(" + " + ".join(fmt(e.value) for e in shared) + ")"
            return f"{text} = {rhs_text([c for _, c in q.parts.get('V', [])])}"
        if self.method == "nodos" and q.kind == "kcl":
            n = q.owner[0]
            own = [self.circuit.get(x) for x in q.parts.get("R", []) if self.circuit.get(x).n1 != self.circuit.get(x).n2]
            text = f"V{n}(" + " + ".join(f"1/{fmt(e.value)}" for e in own) + ")"
            for e in own:
                other = e.n2 if e.n1 == n else e.n1
                if other != "0":
                    text += f" − V{other}(1/{fmt(e.value)})"
            return f"{text} = {rhs_text([c for _, c in q.parts.get('I', [])])}"
        return None

    def _branch_text(self, name: str) -> str:
        e = self.circuit.get(name)
        if self.method == "mallas" and self.loops:
            k = next((m for m, lp in enumerate(self.loops) if lp.dir(name)), None)
            return self._branch_expr(k, name) if k is not None else "0"
        if self.method == "nodos":
            if e.n2 == "0":
                return f"V{e.n1}/{fmt(e.value)}"
            if e.n1 == "0":
                return f"V{e.n2}/{fmt(e.value)}"
            return f"(V{e.n1} − V{e.n2})/{fmt(e.value)}"
        return "la corriente que pasa por ella"

    def _guided_values(self) -> str:
        syms = [sp.Symbol(v) for v in self.vars]
        if len(syms) == 2:
            q = self.refs[0]
            eq = sum(q.coefs[v] * sp.Symbol(v) for v in self.vars) - q.rhs
            x0 = syms[0] if q.coefs[self.vars[0]] != 0 else syms[1]
            other = syms[1] if x0 == syms[0] else syms[0]
            expr = sp.solve(eq, x0)[0]
            txt = str(sp.simplify(expr)).replace("*", "·")
            return (f"Despeja {x0} de la primera ecuación: {x0} = {txt}. Sustitúyelo en la otra "
                    f"ecuación y despeja {other}. ¿Qué obtienes?")
        rows = "; ".join(q.as_text() for q in self.refs)
        return (f"Escribe el sistema ({rows}) en forma matricial A·x = b y resuélvelo por eliminación "
                f"(o con la calculadora). ¿Qué valores obtienes?")

    def _item_prompt(self, step: Step, item) -> str:
        k = step.kind
        if k == "mesh_define":
            return f"¿Qué elementos recorre la corriente I{item + 1}?"
        if k == "shared_expr":
            a, b = step.data["shared"][item]
            return (f"Tomando como referencia el sentido de I{a+1}, ¿cómo expresarías la corriente "
                    f"que pasa por {self.circuit.get(item).label}?")
        if k == "equations":
            q = self.refs[item]
            if q.kind == "kvl":
                real = " (malla real)" if self._types_known() else ""
                return (f"Plantea la ecuación de la malla {q.owner[0]+1}{real} con LVK. "
                        "Intenta escribirla tú.")
            if q.kind == "supermesh":
                return (f"Ahora la {q.label}: aplica LVK alrededor de las mallas "
                        f"{' y '.join(str(m+1) for m in q.owner)} juntas, sin pasar por la fuente "
                        "de corriente que comparten.")
            if q.kind == "kcl":
                return (f"Plantea la ecuación del nodo {q.owner[0]} con LCK "
                        "(voltaje del nodo por sus conductancias, menos los vecinos, igual a las fuentes).")
            if q.kind == "supernode":
                return (f"Los nodos {' y '.join(q.owner)} forman un supernodo: escribe su ecuación "
                        "complementaria (LCK para todo el supernodo).")
            if self.method == "mallas":
                if len(q.owner) == 1:
                    return (f"La fuente de corriente {q.source} sólo pertenece a la malla {q.owner[0]+1}. "
                            "¿Cuál es la ecuación directa de esa malla?")
                return (f"Las mallas {' y '.join(str(m+1) for m in q.owner)} comparten la fuente de "
                        f"corriente {q.source}: escribe la ecuación auxiliar de la supermalla.")
            if len(q.owner) == 1:
                return (f"La fuente {q.source} une el nodo {q.owner[0]} con la referencia. "
                        "¿Cuál es la ecuación directa de ese nodo?")
            return (f"La fuente {q.source} une los nodos {' y '.join(q.owner)}: ¿qué ecuación impone "
                    "entre sus voltajes?")
        if k == "mesh_types":
            if len(step.pending) < len(step.items):
                return f"¿Y la malla {step.pending[0] + 1}: real, fantasma o supermalla?"
            return ("Clasifica cada malla según el método de formato: ¿es real, fantasma o forma "
                    "parte de una supermalla? (por ejemplo: «malla 1: …, malla 2: …»)")
        if k == "node_types":
            if len(step.pending) < len(step.items):
                return f"¿Y el nodo {step.pending[0]}: real, fantasma o supernodo?"
            return (f"Con el nodo 0 como referencia, clasifica cada nodo ({', '.join(step.items)}): "
                    "¿es real, fantasma o parte de un supernodo?")
        if k == "table":
            e = self.circuit.get(item)
            rama = {"mallas": "¿cuál es su ecuación de rama (en corrientes de malla) y ",
                    "nodos": "¿cuál es su ecuación de rama (en voltajes de nodo) y "}.get(self.method, "¿")
            head = (f"Completa la tabla de resultados. Para {e.label}: {rama}cuánto valen "
                    f"su corriente, su voltaje y su potencia?")
            if not step.done:
                head += (f" Escríbelo así: «{e.name}: I = … A, V = … V, P = … W». Puedes mandar varias "
                         "resistencias, una por renglón.")
            return head
        if k == "values":
            return (f"¿Qué método usarías para resolver el sistema? Resuélvelo y dime "
                    f"{', '.join(step.pending)}.")
        if k == "targets":
            t = self.targets[item]
            return f"Ahora calcula {t.describe()} (en {target_unit(t)})."
        return ""

    def _step_prompt(self, step: Step) -> str:
        k = step.kind
        if k == "submit":
            return ("Escribe tu procedimiento: las ecuaciones que planteaste y tus resultados. "
                    "Lo reviso sin resolverlo por ti.")
        if k == "intro":
            return "Antes de calcular, dime: ¿qué datos proporciona el circuito y qué variable necesitas encontrar?"
        if k == "mesh_count":
            return "Observa el circuito: ¿cuántas corrientes de malla necesitas definir?"
        if k == "node_count":
            return ("Tomaremos el nodo 0 como referencia (0 V). ¿Cuántos voltajes de nodo "
                    "desconocidos tiene el circuito?")
        if k == "shared_id":
            return "¿Hay algún elemento que pertenezca a más de una malla?"
        if k == "concept":
            return step.data["question"]
        if k == "reduce":
            r = self.reducer
            if r.stuck:
                return ("Ya no quedan resistencias en serie ni en paralelo: aquí se necesita una "
                        "conversión delta-estrella. Cuando la hagas, dime cuánto vale la resistencia "
                        f"equivalente. Quedan: {r.state_text()}.")
            if r.history or r.counter:
                return (f"¿Qué combinas ahora? Quedan: {r.state_text()}. Dime cuáles y cuánto vale "
                        "su equivalente.")
            return (f"Reduce el circuito paso a paso para encontrar {self.req_target.describe()}. "
                    "¿Qué resistencias puedes combinar primero (en serie o en paralelo) y cuánto vale "
                    "su equivalente?")
        if k == "verify":
            return ("Todavía no hemos terminado: obtener números no significa que la solución sea "
                    "correcta. ¿Cómo podrías comprobar estos resultados?")
        if k == "sim":
            what = ", ".join(self.vars) if self.vars else ", ".join(t.describe() for t in self.targets)
            return (f"Construye ahora el circuito en PhET o Multisim, mide {what} y dime qué valores "
                    f"obtuviste.")
        if k == "metacog":
            q2 = self._metacog_q2()
            q1 = ("¿Cuál fue el principal error que cometiste durante la resolución?"
                  if self._main_errors() else "¿Qué paso te costó más trabajo y por qué?")
            return (f"Antes de terminar, responde tres preguntas: 1) {q1} 2) {q2} 3) Si mañana "
                    "encuentras un circuito más grande, ¿qué regla de este ejercicio podrías transferir?")
        pend = step.pending
        return self._item_prompt(step, pend[0]) if pend else ""

    def _types_known(self) -> bool:
        """¿El alumno ya clasificó mallas/nodos (o no hacía falta hacerlo)?"""
        st = next((s for s in self.steps if s.kind in ("mesh_types", "node_types")), None)
        return st is not None and st.complete

    def _main_errors(self) -> list[str]:
        return [c for c in self.c["errores"] if self.bank.get(c)
                and c not in ("VERIFY_VAGUE", "PARSE_ERROR", "CONCEPT_WRONG")]

    def _convention_text(self) -> str:
        if self.method == "mallas":
            parts = [f"I{k+1} recorre, en este orden: {self._loop_text(k)}" for k in range(len(self.loops))]
            sense = ("todas las corrientes de malla giran en sentido horario (FMR)"
                     if self.circuit.meshes else "todas las corrientes de malla giran en el mismo sentido")
            return f"Convención: {sense}. " + "; ".join(parts) + "."
        if self.method == "nodos":
            return (f"Convención: el nodo 0 es la referencia (0 V) y las incógnitas son "
                    f"{', '.join(self.vars)}; en LCK, las corrientes que salen del nodo son positivas.")
        return ""

    def _format_example(self) -> str:
        """Ejemplo de formato con las incógnitas de este problema."""
        if not self.vars:
            return "I = 2 A"
        a, b = self.vars[0], self.vars[1] if len(self.vars) > 1 else None
        if self.method == "nodos":
            return f"{a}(1/4 + 1/2) − {b}(1/2) = 5" if b else f"{a}(1/4 + 1/2) = 5"
        return f"6{a} − 2{b} = 12" if b else f"6{a} = 12"

    def _metacog_q2(self) -> str:
        codes = self._main_errors()
        if codes:
            main = max(set(codes), key=codes.count)
            q = self.bank.get(main).metacognitiva
            if q:
                return q
        return {"mallas": "¿Por qué en una resistencia compartida aparece una diferencia de corrientes?",
                "nodos": "¿Por qué cada corriente de una resistencia depende de la diferencia de voltajes?",
                "serie_paralelo": "¿Qué distingue a elementos en serie de elementos en paralelo?",
                "ohm": "¿Cómo compruebas que un despeje de la Ley de Ohm tiene sentido físico?"}[self.method]

    # -------------------------------------------------------- escalera genérica
    def _ladder(self, step: Step, level: int) -> str:
        k, c = step.kind, self.circuit
        item = step.pending[0] if step.pending else None
        B, N = len(c.elements), len(c.nodes)
        if k == "intro":
            return {1: "¿Qué valores conoces (fuentes, resistencias) y cuál es la incógnita que pide el problema?",
                    2: "Separa datos de incógnitas: los datos son los valores dados; la incógnita es lo que te piden calcular.",
                    3: "Haz una lista: fuentes con su valor, resistencias con su valor y, al final, lo que te piden.",
                    4: f"Los datos son: {', '.join(e.label for e in c.elements)}. ¿Qué te piden encontrar?",
                    5: f"Datos: {', '.join(e.label for e in c.elements)}. Incógnita: "
                       f"{', '.join(t.describe() for t in self.targets) or 'las corrientes/voltajes del circuito'}."
                       " ¿Con qué empezarías?"}[level]
        if k == "mesh_count":
            return self.bank.text("MESH_COUNT_WRONG", level, B=B, N=N) if level <= 4 else \
                f"B − N + 1 = {B} − {N} + 1. ¿Cuánto da?"
        if k == "node_count":
            return self.bank.text("NODE_COUNT_WRONG", level, N=N) if level <= 4 else \
                f"El circuito tiene los nodos {', '.join(c.nodes)}. Sin el 0, ¿cuántos quedan?"
        if k == "mesh_define":
            anchor = self.loops[item].items[0][0]
            return {1: f"Sigue el camino cerrado de I{item+1}: sale de un nodo y regresa a él. ¿Qué elementos cruza?",
                    2: "Una malla es un lazo cerrado que no encierra otros elementos.",
                    3: f"I{item+1} pasa por {c.get(anchor).label}. Desde ahí sigue el lazo hasta regresar.",
                    4: f"I{item+1} recorre, en este orden: {self._loop_text(item).split(', ')[0]}, … "
                       "¿qué elementos siguen?",
                    5: f"I{item+1} recorre: {self._loop_text(item)}. ¿Por qué esos y no otros?"}[level]
        if k == "shared_id":
            sh = step.data["shared"]
            m1, m2 = list(sh.values())[0]
            if level <= 4:
                return self.bank.text("SHARED_ID_WRONG", level, m1=m1 + 1, m2=m2 + 1)
            return (f"Compara: la malla {m1+1} tiene {self._loop_text(m1)} y la malla {m2+1} tiene "
                    f"{self._loop_text(m2)}. ¿Cuál aparece en ambas?")
        if k == "shared_expr":
            a, b = step.data["shared"][item]
            if level <= 4:
                return self.bank.text("MESH_SHARED_SUM", level, a=a + 1, b=b + 1, ecuacion=f"malla {a+1}")
            return (f"Por {item} pasan I{a+1} (a favor, referencia) e I{b+1} (en contra). "
                    f"¿Cómo escribes la corriente neta?")
        if k == "equations":
            q = self.refs[item]
            if q.kind == "constraint":
                what = ("la corriente de la rama de la fuente" if self.method == "mallas"
                        else "la diferencia de voltaje entre las terminales de la fuente")
                return {1: f"¿Qué magnitud fija directamente la fuente {q.source}?",
                        2: f"Una fuente fija {what}: esa relación es la ecuación.",
                        3: (f"Escribe {what} {q.source} en términos de tus incógnitas y compárala con su valor."),
                        4: (f"Expresa {what} {q.source} con tus incógnitas, respetando su sentido o su "
                            "polaridad, e iguálala a su valor. ¿Cómo queda?"),
                        5: f"La ecuación tiene esta estructura: {q.as_text()}. ¿De dónde sale cada signo?"}[level]
            if level == 5:
                formatted = self._format_equation(q)
                if formatted:
                    return (f"Con el método de formato queda: {formatted}. Simplifícala en la forma "
                            "a·I1 + b·I2 + … = c." if self.method == "mallas" else
                            f"Con el método de formato queda: {formatted}. Simplifícala.")
                return (f"La ecuación tiene esta estructura: {q.as_text()}. Explica de dónde sale cada "
                        "término y escríbela tú.")
            if self.method == "mallas":
                return {1: "¿Qué dice la Ley de Voltajes de Kirchhoff (LVK) sobre la suma de voltajes en un lazo cerrado?",
                        2: "En un lazo cerrado, la suma algebraica de voltajes es cero: las subidas igualan a las caídas.",
                        3: "Recorre la malla en el sentido de su corriente: por cada resistencia escribe R·(corriente neta) "
                           "y por cada fuente su subida o caída.",
                        4: "Usa la ecuación general del método de formato: I_m·r_mm − I_n·r_mn − … = V_m, donde r_mm es "
                           "la suma de resistencias de la malla, r_mn la resistencia que comparte con la malla n y V_m la "
                           "suma de fuentes que empujan en el sentido de I_m. ¿Qué valor va en cada lugar?"}[level]
            return {1: "¿Qué dice la Ley de Corrientes de Kirchhoff (LCK) sobre las corrientes en un nodo?",
                    2: "La suma de corrientes que salen de un nodo es cero (lo que entra, sale).",
                    3: "Por cada resistencia conectada al nodo escribe (Vnodo − Vvecino)/R; suma las fuentes de corriente.",
                    4: "Usa la ecuación general del método de formato: V_n·g_nn − V_m·g_nm − … = I_n, donde g_nn es la "
                       "suma de conductancias (1/R) del nodo, g_nm la conductancia compartida con el nodo m e I_n la "
                       "suma de fuentes de corriente (+ si entran, − si salen). ¿Qué valor va en cada lugar?"}[level]
        if k == "mesh_types":
            m = item
            if level <= 4:
                return self.bank.text("MESH_TYPE_WRONG", level, malla=m + 1)
            srcs = [e.name for e in self.circuit.by_kind("I") if self.loops[m].dir(e.name)]
            if not srcs:
                return f"La malla {m+1} no tiene fuentes de corriente. ¿De qué tipo es entonces?"
            src = srcs[0]
            others = [k + 1 for k, lp in enumerate(self.loops) if k != m and lp.dir(src)]
            where = (f"la comparte con la malla {others[0]}" if others else "sólo pertenece a ella")
            return f"La malla {m+1} tiene la fuente {src}, que {where}. ¿De qué tipo es?"
        if k == "node_types":
            n = item
            if level <= 4:
                return self.bank.text("NODE_TYPE_WRONG", level, nodo=n)
            srcs = [e for e in self.circuit.by_kind("V") if n in (e.n1, e.n2)]
            if not srcs:
                return f"Al nodo {n} no lo toca ninguna fuente de voltaje. ¿De qué tipo es?"
            e = srcs[0]
            other = e.n2 if e.n1 == n else e.n1
            return (f"El nodo {n} está unido por {e.name} al nodo {other}"
                    f"{' (la referencia)' if other == '0' else ''}. ¿De qué tipo es?")
        if k == "reduce":
            r = self.reducer
            return {1: "¿Qué resistencias están conectadas a los mismos dos nodos? ¿Y cuáles están unidas por "
                       "un nodo al que no llega nada más?",
                    2: "En serie: un solo camino, se suman. En paralelo: mismos dos nodos, "
                       "1/Req = 1/R1 + 1/R2 (para dos, R1·R2/(R1 + R2)).",
                    3: "Combina primero un solo par (lo más interno del circuito) y ponle nombre, por ejemplo R_T1; "
                       "después vuelve a mirar el circuito.",
                    4: r.next_move_hint(),
                    5: r.next_move_hint() + " Si es paralelo usa R1·R2/(R1 + R2); si es serie, súmalas."}[level]
        if k == "table":
            e = self.circuit.get(item)
            expr = self._branch_text(item)
            first = {"mallas": f"¿Qué corrientes de malla pasan por {e.name}? Si pasan dos, ¿en qué sentido va cada una?",
                     "nodos": f"¿Entre qué nodos está {e.name}? ¿Qué voltaje tiene entonces?"}.get(
                self.method, f"¿Qué corriente pasa por {e.name}? ¿Está en serie con la fuente o en una rama en paralelo?")
            return {1: first,
                    2: "Con la corriente de la rama: V = I·R y P = I²·R = V·I.",
                    3: f"Primero la corriente de {e.name} (su ecuación de rama), luego V = I·R y al final P = V·I.",
                    4: f"Para {e.name}: su ecuación de rama es {expr}. Sustituye tus valores y calcula V y P.",
                    5: f"Para {e.name}: I = {expr}; V = I·({fmt(e.value)} Ω); P = V·I. Haz tú las operaciones."}[level]
        if k == "values":
            return {1: "¿Qué método conoces para resolver un sistema de ecuaciones lineales?",
                    2: "Puedes usar sustitución, eliminación o la regla de Cramer.",
                    3: "Elimina una incógnita: multiplica una ecuación para que un coeficiente se cancele al sumarlas.",
                    4: "Intenta obtener primero una sola incógnita y luego sustituye para obtener las demás.",
                    5: self._guided_values()}[level]
        if k == "targets":
            t = self.targets[item]
            return self._target_ladder(t, level)
        if k == "concept":
            return {1: "Revisa esa idea. " + step.data["question"],
                    2: step.data["hint"],
                    3: step.data["hint"] + " ¿Qué implica eso para este circuito?",
                    4: step.data["hint"] + " Con esto, ¿cómo responderías?",
                    5: step.data["hint"] + " Explícalo con tus palabras."}[level]
        if k in ("verify",):
            if step.data.get("balance"):
                return self.bank.text("BALANCE_WRONG", level) if level <= 4 else (
                    "PC es la suma de I²·R de cada resistencia de tu tabla; PE es la suma de V·I de cada "
                    "fuente. Calcula las dos por separado. ¿Qué obtienes?")
            return self.bank.text("VERIFY_VAGUE", level) if level <= 4 else \
                "Sustituye tus valores en las ecuaciones originales y revisa que se cumplan. ¿Se cumplen?"
        if k == "sim":
            return "¿Qué diferencias ves entre tus cálculos y el simulador? ¿Qué podría causarlas (redondeo, polaridad del medidor, un valor mal capturado)?"
        if k == "metacog":
            return "Tómate un momento: ¿qué paso te costó más trabajo y por qué?"
        return "¿Qué harías a continuación?"

    def _target_ladder(self, t: Target, level: int) -> str:
        if t.quantity == "Req":
            return {1: "¿Qué resistencias están en serie y cuáles en paralelo?",
                    2: "En serie las resistencias se suman; en paralelo, 1/Req = 1/R1 + 1/R2 + …",
                    3: "Combina paso a paso: primero los grupos en paralelo (mismos dos nodos), luego los que quedan en serie.",
                    4: "Identifica el primer par que puedas combinar y calcula su equivalente. ¿Cuál es?",
                    5: "Resistencias del circuito: " + ", ".join(e.label for e in self.circuit.by_kind("R"))
                       + ". Reduce de adentro hacia afuera hasta quedarte con una sola. ¿Qué obtienes?"}[level]
        q = {"I": "corriente", "V": "voltaje", "P": "potencia"}[t.quantity]
        return {1: f"¿Qué relación conecta la {q} de {t.element} con lo que ya conoces?",
                2: "Recuerda: V = R·I para resistencias y P = V·I para cualquier elemento.",
                3: f"Obtén primero el voltaje y la corriente de {t.element}; después aplica la relación.",
                4: f"Para {t.element}: ¿cuál es su corriente? Con ella, aplica V = R·I o P = V·I.",
                5: f"Para {t.element}: identifica su corriente, luego su voltaje (V = R·I) y, si se pide, "
                   "P = V·I. Haz tú la última operación."}[level]

    # ============================================================ API pública
    def start(self) -> str:
        head = (f"Vamos a resolver este problema juntos con {METHODS[self.method].lower()}. "
                "No te voy a dar la solución; la vamos a construir paso a paso.")
        conv = "" if self.mode in ("aprender", "simulador") else self._convention_text()
        return self._emit(f"{head} {conv} {self._step_prompt(self.step)}", 0)

    def reply(self, text: str) -> str:
        text = (text or "").strip()
        self.transcript.append({"t": time.time(), "rol": "alumno", "texto": text,
                                "paso": self.step.id, "nivel": self.level})
        if self.finished:
            return self._emit("Ya terminamos este problema. Puedes empezar uno nuevo cuando quieras.", 0)
        if not text:
            return self._emit(self._step_prompt(self.step), self.level)
        if self.justify_pending is not None:
            cont, self.justify_pending = self.justify_pending, None
            # si en lugar de justificar ya respondió el paso siguiente, se evalúa eso
            if not ("=" in text or re.fullmatch(r"[\d\s.,+\-−*/()AVWΩkm]+", text)):
                self.transcript[-1]["rol"] = "justificacion"
                explained = len(text.split()) >= 3 and not HELP_RE.search(text)
                if explained:
                    self.c["justificaciones"] += 1
                ack = "Gracias, explicarlo te ayuda a fijarlo." if explained else "De acuerdo."
                return self._emit(f"{ack} {cont}", self.level)
        if DIRECT_RE.search(text):
            self.c["directa"] += 1
            self.level = min(5, self.level + 1)
            return self._emit("No te voy a dar la solución completa, pero te ayudo con el siguiente paso. "
                              + self._help_text(), self.level, hint=True)
        if HELP_RE.search(text) and not re.search(r"\d\s*[IV]\d|=", text):
            self.level = min(5, self.level + 1)
            return self._emit(self._help_text(), self.level, hint=True)

        step = self.step
        out = self._check(step, text)
        if out is None:  # respuesta no evaluable en este paso
            return self._emit("No logré relacionar tu respuesta con la pregunta. "
                              + self._step_prompt(step), self.level)
        if not out.ok and out.code in NO_PENALTY:
            if out.code == "PARSE_ERROR":
                extra = self.bank.text("PARSE_ERROR", 1, vars=", ".join(self.vars) or "los datos",
                                       ejemplo=self._format_example())
            else:
                extra = "" if out.partial_msg.rstrip().endswith("?") else self._step_prompt(self.step)
            return self._emit(f"{out.partial_msg} {extra}", self.level)
        self.c["intentos"] += 1
        if step.kind == "submit":
            return self._after_submit(out)
        if out.ok:
            # tras corregir un error conceptual se pide justificar (funcionalidad 6)
            self._why = self.mode == "aprender" and self.item_failed and step.kind in JUSTIFY_KINDS
            return self._on_correct(step, out)
        return self._on_wrong(step, out)

    def _say(self, praise: str, rest: str, level: int) -> str:
        if self._why:
            self._why = False
            self.justify_pending = rest.strip()
            return self._emit(f"{praise} Antes de seguir: ¿por qué es así? Explícalo con tus palabras.",
                              level)
        return self._emit(f"{praise} {rest}", level)

    def _after_submit(self, out: Outcome) -> str:
        summary, bad = out.detail["summary"], out.detail.get("bad")
        self.idx += 1
        while self.idx < len(self.steps) - 1 and self.step.items and self.step.complete:
            self.idx += 1
        if bad:
            self.c["errores"].append(bad.code)
            self.c["conceptuales" if self.bank.kind(bad.code) == "conceptual" else "procedimentales"] += 1
            self.item_failed, self.last_code, self.level = True, bad.code, 1
            return self._emit(f"{summary} Empecemos por lo que falla. "
                              + self._help_text(Outcome(False, bad.code, bad.detail)), 1, hint=True)
        self.level = 0
        return self._emit(f"{summary} {self._step_prompt(self.step)}", 0)

    # ------------------------------------------------------------- transiciones
    def _on_correct(self, step: Step, out: Outcome) -> str:
        if self.item_failed and self.level <= 1:
            self.c["autonoma"] = True
        if out.item is not None and out.item not in step.done:
            step.done.append(out.item)
        self.c["nivel_final"] = self.level
        self.item_failed = False
        self.last_code = None
        praise = out.partial_msg or "Correcto."
        if not step.complete:
            self.level = max(0, self.level - 1)
            return self._say(praise, self._step_prompt(step), self.level)
        return self._advance(praise)

    def _advance(self, praise: str) -> str:
        self.level = max(0, self.level - 1)
        self.idx += 1
        extra = ""
        prev = self.steps[self.idx - 1]
        if prev.kind == "mesh_define":
            self._apply_assignment()
            extra = " " + self._convention_text()
        if prev.kind == "node_count":
            extra = " " + self._convention_text()
        if prev.kind == "mesh_count":
            extra = (" Todas las corrientes de malla girarán en sentido horario (FMR), como en el dibujo."
                     if self.circuit.meshes else
                     " Todas las corrientes de malla girarán en el mismo sentido (por ejemplo, horario).")
        if prev.kind == "equations" and len(self.refs) > 1:
            extra = " Ya construiste el sistema completo."
        if self.idx >= len(self.steps):
            return self._finish(praise)
        step = self.step
        # pasos ya resueltos por adelantado (el alumno se adelantó)
        while step.items and step.complete:
            self.idx += 1
            if self.idx >= len(self.steps):
                return self._finish(praise)
            step = self.step
        if step.kind == "equations" and prev.kind in ("shared_expr", "shared_id", "mesh_define",
                                                      "mesh_types", "node_types") \
                and self.level == 0:
            extra += " Ahora intenta las ecuaciones sin mi ayuda."
        return self._say(praise, f"{extra} {self._step_prompt(step)}", self.level)

    def _on_wrong(self, step: Step, out: Outcome) -> str:
        code = out.code
        self.item_failed = True
        self.last_code, self.last_detail = code, dict(out.detail)
        self.c["errores"].append(code)
        if code not in ("PARSE_ERROR", "VERIFY_VAGUE"):
            if self.bank.kind(code) == "conceptual":
                self.c["conceptuales"] += 1
            else:
                self.c["procedimentales"] += 1
        self.level = min(5, self.level + 1)
        if out.partial_msg:
            return self._emit(out.partial_msg + " " + self._help_text(out), self.level, hint=True)
        return self._emit(self._help_text(out), self.level, hint=True)

    def _help_text(self, out: Outcome | None = None) -> str:
        step, lvl = self.step, max(1, self.level)
        code = out.code if out else self.last_code
        detail = out.detail if out else self.last_detail
        if code and lvl <= 4 and self.bank.get(code):
            t = self.bank.text(code, lvl, **self._fill_args(step, detail))
            if t:
                return t
        return self._ladder(step, lvl)

    def _fill_args(self, step: Step, detail: dict) -> dict:
        args = dict(detail)
        args.setdefault("vars", ", ".join(self.vars))
        args.setdefault("ejemplo", self._format_example())
        if "malla" in detail:
            a = detail["malla"]
            args["a"] = a
            other = detail.get("otra")
            if other:
                args["b"] = other.lstrip("I")
            else:
                sh = an.shared_elements(self.loops)
                nb = [m2 + 1 if m1 + 1 == a else m1 + 1 for (m1, m2) in sh.values() if a in (m1 + 1, m2 + 1)]
                args["b"] = nb[0] if nb else "…"
        if step.kind == "shared_expr" and step.pending:
            a, b = step.data["shared"][step.pending[0]]
            args.setdefault("a", a + 1)
            args.setdefault("b", b + 1)
            args.setdefault("ecuacion", f"malla {a+1}")
        if "terminos" in detail:
            args["terminos"] = " y ".join(detail["terminos"])
        return args

    def _finish(self, praise: str) -> str:
        self.finished = True
        self.idx = len(self.steps) - 1
        codes = [c for c in self.c["errores"] if self.bank.get(c) and c not in ("PARSE_ERROR", "VERIFY_VAGUE")]
        concept = {"mallas": "LVK por malla y corriente neta en ramas compartidas",
                   "nodos": "LCK por nodo con corrientes (Vnodo − Vvecino)/R",
                   "serie_paralelo": "en serie se comparte la corriente; en paralelo, el voltaje",
                   "ohm": "V = R·I y P = V·I"}[self.method]
        strategy = {"mallas": "antes de formular LVK, identifica las ramas compartidas y establece "
                              "correctamente la corriente neta de cada una",
                    "nodos": "escribe cada corriente como diferencia de voltajes entre resistencia y revisa "
                             "unidades en cada término",
                    "serie_paralelo": "antes de combinar, identifica qué elementos comparten corriente "
                                      "y cuáles comparten voltaje",
                    "ohm": "revisa unidades y sentido físico de cada despeje"}[self.method]
        if codes:
            main = max(set(codes), key=codes.count)
            d = self.bank.get(main).descripcion
            err = d[:1].lower() + d[1:]
        else:
            err = "ninguno importante: resolviste con poca ayuda"
        msg = (f"{praise} Cierre del problema. Concepto utilizado: {concept}. Error corregido: {err}. "
               f"Estrategia aprendida: {strategy}.")
        return self._emit(msg, 0)

    def _emit(self, msg: str, level: int, hint: bool = False) -> str:
        msg = re.sub(r"\s+", " ", msg).strip()
        if "?" in msg:
            self.c["preguntas"] += 1
        if hint and level >= 1:
            self.c["pistas"][min(level, 5)] += 1
        self.transcript.append({"t": time.time(), "rol": "tutor", "texto": msg,
                                "paso": self.step.id, "nivel": level, "codigo": self.last_code})
        return msg

    # ============================================================== chequeos
    def _check(self, step: Step, text: str) -> Outcome | None:
        k = step.kind
        # el alumno se adelanta con ecuaciones correctas en los pasos previos
        if k in ("intro", "mesh_count", "mesh_define", "mesh_types", "node_count", "node_types",
                 "shared_id", "shared_expr") and self.refs:
            ahead = self._check_ahead(text)
            if ahead:
                return ahead
        ce = self._check_counterexample(text)
        if ce:
            return ce
        fn = getattr(self, f"_check_{k}", None)
        return fn(step, text) if fn else Outcome(True)

    def _check_counterexample(self, text: str) -> Outcome | None:
        """Respuesta a la pregunta de contraejemplo (3 A contra 1 A) del banco de errores."""
        if self.last_code != "MESH_SHARED_SUM" or self.level != 1:
            return None
        if re.search(r"[IiVv]\s*\d", text):
            return None
        n = self._first_int(text)
        if n == 2:
            a = self._fill_args(self.step, {}).get("a", 1)
            return Outcome(False, "ELABORATE", partial_msg=(
                f"Muy bien: las corrientes se restan porque van en sentidos opuestos. Entonces, tomando "
                f"el sentido de I{a} como referencia, ¿cómo escribirías ahora la corriente de esa rama?"))
        if n == 4:
            return Outcome(False, "MESH_SHARED_SUM", {"malla": self._fill_args(self.step, {}).get("a", 1)})
        return None

    def _check_ahead(self, text: str) -> Outcome | None:
        if looks_like_equation(text, self.vars):
            eqs = [e for e in split_equations(text)]
            good = 0
            for raw in eqs:
                try:
                    pe = parse_linear_equation(raw, self.vars, self.constants)
                except ParseError:
                    continue
                st = self._find_step("equations")
                d = diagnose_equation(pe, self.refs, st.pending, self.mappings, self._prefix,
                                      self.circuit, self.loops)
                if d.ok and d.detail.get("index") in st.pending:
                    st.done.append(d.detail["index"])
                    self._narrow(pe)
                    good += 1
            if good:
                return Outcome(False, "AHEAD", partial_msg=(
                    "Esa ecuación es correcta y la guardo, pero no nos saltemos el razonamiento."))
        return None

    def _find_step(self, kind: str) -> Step:
        for s in self.steps:
            if s.kind == kind:
                return s
        raise KeyError(kind)

    @property
    def _prefix(self) -> str:
        return "I" if self.method == "mallas" else "V"

    def _narrow(self, pe) -> None:
        if self.mappings:
            from .diagnose import filter_mappings
            keep = filter_mappings(pe, self.refs, self.mappings, "I")
            if keep:
                self.mappings = keep

    # --- pasos conceptuales / de conteo
    def _check_intro(self, step, text):
        return Outcome(len(text.split()) >= 2 or bool(re.search(r"\d", text)), "CONCEPT_WRONG",
                       {"pista_concepto": "Menciona los datos y la incógnita."})

    def _check_convention(self, step, text):
        return Outcome(True)

    def _first_int(self, text: str) -> int | None:
        """Primer número entero 'suelto' (ignora el 1 de I1) o escrito con letra."""
        found = [(m.start(), int(m.group())) for m in re.finditer(r"(?<![A-Za-z\d.,])\d+(?![.,]\d)", text)]
        for w, n in NUM_WORDS.items():
            found += [(m.start(), n) for m in re.finditer(rf"\b{w}\b", text.lower())]
        return min(found)[1] if found else None

    def _check_mesh_count(self, step, text):
        n = self._first_int(text)
        if n is None:
            return None
        return Outcome(n == len(self.loops), "MESH_COUNT_WRONG", {"B": len(self.circuit.elements),
                                                                  "N": len(self.circuit.nodes)})

    def _check_node_count(self, step, text):
        n = self._first_int(text)
        if n is None:
            return None
        fixed = sum(1 for q in self.refs if q.kind == "constraint"
                    and sum(1 for v in q.coefs.values() if v != 0) == 1)
        total = len(self.circuit.non_ground_nodes)
        if n == total:
            return Outcome(True, partial_msg="Correcto.")
        if n == total - fixed and fixed:
            return Outcome(True, partial_msg=(
                "Correcto: algunos nodos quedan fijados directamente por fuentes, así que sólo "
                f"{n} son realmente desconocidos. Aun así, llamaremos {', '.join(self.vars)} a todos."))
        return Outcome(False, "NODE_COUNT_WRONG", {"N": len(self.circuit.nodes)})

    def _check_mesh_define(self, step, text):
        """El alumno describe la malla k con sus palabras; su descripción decide
        cuál de las ventanas del circuito es 'su' I_k."""
        k = step.pending[0]
        refs = element_refs(text, self.circuit)
        if not refs:
            return None
        free = [j for j in range(len(self.loops)) if j not in self.assign.values()]
        exact, partial = [], []
        for j in free:
            names = self.loops[j].names
            chosen = match_element_set(refs, names)
            if chosen is None:
                continue
            resistors = [n for n in names if self.circuit.get(n).kind == "R"]
            missing = [n for n in resistors if n not in chosen]
            (partial if missing else exact).append((j, len(missing)))
        if len(exact) == 1 or (exact and len(set(self.loops[j].names.__len__() for j, _ in exact)) == 1
                                and len(exact) >= 1 and not partial):
            self.assign[k] = exact[0][0]
            return Outcome(True, item=k)
        if partial or len(exact) > 1:
            faltan = min(m for _, m in partial) if partial else 1
            return Outcome(False, "MESH_INCOMPLETE", {"faltan": faltan, "malla": k + 1})
        anchor = self.loops[free[0]].names[0] if free else self.loops[0].names[0]
        return Outcome(False, "MESH_NOT_A_LOOP", {"malla": k + 1,
                                                  "pista_elementos": self.circuit.get(anchor).label})

    def _apply_assignment(self) -> None:
        """Reordena las mallas internas según la numeración que eligió el alumno."""
        if not self.assign or sorted(self.assign.values()) != list(range(len(self.loops))):
            return
        self.loops = [self.loops[self.assign[k]] for k in range(len(self.loops))]
        self.refs = an.mesh_system(self.circuit, self.loops)
        self.truth = {k: float(v) for k, v in an.solve_system(self.refs).items()}
        self.mappings = [tuple((k, 1) for k in range(len(self.loops)))]
        for st in self.steps:
            if st.kind == "equations":
                st.items, st.done = list(range(len(self.refs))), []
            if st.kind in ("shared_id", "shared_expr"):
                sh = an.shared_elements(self.loops)
                st.data["shared"] = sh
                if st.kind == "shared_expr":
                    st.items = self._shared_items(sh)

    def _check_shared_id(self, step, text):
        shared = list(step.data["shared"].keys())
        if re.match(r"^\s*no\b", text.lower()):
            m1, m2 = list(step.data["shared"].values())[0]
            return Outcome(False, "SHARED_ID_WRONG", {"m1": m1 + 1, "m2": m2 + 1})
        refs = element_refs(text, self.circuit)
        if not refs:
            return None
        chosen = match_element_set(refs, shared)
        m1, m2 = list(step.data["shared"].values())[0]
        if chosen is not None and len(chosen) == len(shared):
            return Outcome(True, item="shared", partial_msg=(
                "Exactamente." if len(shared) == 1 else "Exactamente, esos son los elementos compartidos."))
        if chosen is not None:
            return Outcome(False, "SHARED_ID_WRONG", {"m1": m1 + 1, "m2": m2 + 1},
                           partial_msg="Vas bien, pero hay más elementos compartidos.")
        return Outcome(False, "SHARED_ID_WRONG", {"m1": m1 + 1, "m2": m2 + 1})

    def _check_shared_expr(self, step, text):
        name = step.pending[0]
        a, b = step.data["shared"][name]
        try:
            expr = parse_expression(text.split("=")[-1], self.vars, self.constants)
        except ParseError:
            return None
        Ia, Ib = sp.Symbol(f"I{a+1}"), sp.Symbol(f"I{b+1}")
        det = {"malla": a + 1, "otra": f"I{b+1}", "ecuacion": f"malla {a+1}"}
        if sp.simplify(expr - (Ia - Ib)) == 0:
            return Outcome(True, item=name)
        if sp.simplify(expr - (Ia + Ib)) == 0:
            return Outcome(False, "MESH_SHARED_SUM", det)
        if sp.simplify(expr - (Ib - Ia)) == 0:
            return Outcome(False, "SHARED_EXPR_REF", det)
        if expr in (Ia, Ib):
            return Outcome(False, "MESH_SHARED_MISSED", det)
        if not expr.free_symbols:
            return None     # un número suelto no es una expresión de corriente de rama
        return Outcome(False, "MESH_SHARED_SUM", det)

    # --- clasificación de mallas y nodos (método de formato)
    _TYPE_RE = re.compile(r"fantasma|super\s*-?\s*(?:malla|nodo)|super\b|reale?s?\b", re.I)
    _ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6}

    def _type_events(self, text: str, ids: list) -> list[tuple[int, str, object]]:
        ev: list[tuple[int, str, object]] = []
        for m in self._TYPE_RE.finditer(text):
            w = m.group(0).lower()
            t = "fantasma" if w.startswith("fantasma") else "real" if w.startswith("real") else "super"
            ev.append((m.start(), "type", t))
        if self.method == "mallas":
            for m in re.finditer(r"\bI\s*_?\s*(\d+)\b|\b(IV|V?I{1,3})\b|(?<![\w.])(\d+)(?![\w.])", text):
                k = int(m.group(1) or m.group(3)) if (m.group(1) or m.group(3)) else self._ROMAN.get(m.group(2))
                if k and (k - 1) in ids:
                    ev.append((m.start(), "id", k - 1))
        else:
            names = {n.lower(): n for n in self.circuit.non_ground_nodes}
            for m in re.finditer(r"\b[Vv]?\s*_?\s*([A-Za-z0-9]+)\b", text):
                n = names.get(m.group(1).lower())
                if n in ids:
                    ev.append((m.start(), "id", n))
        ev.sort(key=lambda x: x[0])
        return ev

    def _parse_types(self, text: str, step: Step) -> dict:
        ev = self._type_events(text, step.items)
        types = [v for _, kind, v in ev if kind == "type"]
        ids = [v for _, kind, v in ev if kind == "id"]
        out: dict = {}
        if not types:
            return out
        if not ids:
            if len(types) == len(step.pending):
                return dict(zip(step.pending, types))
            if len(types) == 1 and len(step.pending) == 1:
                return {step.pending[0]: types[0]}
            return out
        type_first = ev[0][1] == "type"
        buf, current = [], None
        for _, kind, v in ev:
            if kind == "id":
                if type_first and current:
                    out[v] = current
                else:
                    buf.append(v)
            else:
                if type_first:
                    current = v
                else:
                    for i in buf:
                        out[i] = v
                    buf = []
        return out

    def _check_types(self, step, text, code: str, key: str):
        said = self._parse_types(text, step)
        if not said:
            return None
        wrong = None
        for item, t in said.items():
            real = self.types.get(item)
            ok = (t == real) or (t == "super" and real in ("supermalla", "supernodo"))
            if ok and item not in step.done:
                step.done.append(item)
            elif not ok and wrong is None:
                wrong = item
        if wrong is not None:
            label = wrong + 1 if key == "malla" else wrong
            good = [i for i in said if i != wrong and i in step.done]
            msg = "Vas bien con algunas. " if good else ""
            return Outcome(False, code, {key: label}, partial_msg=msg.strip())
        return Outcome(True, partial_msg="Correcto." if step.complete else "Bien.")

    def _check_mesh_types(self, step, text):
        return self._check_types(step, text, "MESH_TYPE_WRONG", "malla")

    def _check_node_types(self, step, text):
        return self._check_types(step, text, "NODE_TYPE_WRONG", "nodo")

    # --- reducción serie/paralelo paso a paso
    def _check_reduce(self, step, text):
        chk = self.reducer.check(text)
        if chk is None:
            return None
        if chk.no_penalty:
            return Outcome(False, "ELABORATE", partial_msg=chk.message)
        if chk.ok:
            if self.reducer.done and "req" not in step.done:
                step.done.append("req")
            return Outcome(True, partial_msg=chk.message)
        return Outcome(False, chk.code, chk.detail)

    # --- tabla de resultados: I, V y P en cada resistencia
    def _branch_values(self, name: str) -> tuple[float, float, float]:
        return (abs(float(self.sol.elem_i[name])), abs(float(self.sol.elem_v[name])),
                float(self.sol.power(name)))

    def _table_claims(self, line: str) -> dict[str, float]:
        vals: dict[str, float] = {}
        loose: list[float] = []
        for n in parse_numbers(line):
            key = {"A": "I", "V": "V", "W": "P"}.get(n.unit or "")
            if not key and n.var:
                key = {"i": "I", "v": "V", "p": "P"}.get(n.var.strip()[0].lower()) \
                    if len(n.var.strip()) == 1 else None
            if key and key not in vals:
                vals[key] = n.value
            elif not key:
                loose.append(n.value)
        for key in ("I", "V", "P"):
            if key not in vals and loose:
                vals[key] = loose.pop(0)
        return vals

    def _check_table_row(self, name: str, line: str) -> Outcome | None:
        e = self.circuit.get(name)
        R = float(e.value)
        It, Vt, Pt = self._branch_values(name)
        body = re.sub(rf"\b{re.escape(name)}\b\s*:?", " ", line, flags=re.I)
        # ecuación de rama, si la escribió (sólo mallas: I1 − I2)
        if self.method == "mallas":
            for piece in re.split(r"[=,;]", body):
                if not re.search(r"[Ii]\s*_?\s*(\d|[a-h]\b)", piece) or re.search(r"\d\s*[AVW]\b", piece):
                    continue
                try:
                    expr = parse_expression(piece, self.vars)
                except ParseError:
                    continue
                if not expr.free_symbols:
                    continue
                owners = [(m, lp.dir(name)) for m, lp in enumerate(self.loops) if lp.dir(name)]
                ref = sum(d * sp.Symbol(f"I{m+1}") for m, d in owners)
                if sp.simplify(expr - ref) == 0 or sp.simplify(expr + ref) == 0:
                    break
                if len(owners) == 2:
                    a, b = (sp.Symbol(f"I{m+1}") for m, _ in owners)
                    if sp.simplify(expr - (a + b)) == 0:
                        return Outcome(False, "MESH_SHARED_SUM", {"malla": owners[0][0] + 1,
                                                                  "otra": f"I{owners[1][0]+1}"})
                    if expr in (a, b):
                        return Outcome(False, "MESH_SHARED_MISSED", {"malla": owners[0][0] + 1})
                return Outcome(False, "TABLE_VALUE_WRONG", {"var": "la ecuación de rama", "elemento": name,
                                                            "valor": fmt(R)})
        vals = self._table_claims(body)
        if not vals:
            return None
        det = {"elemento": name, "valor": fmt(R)}
        I, V, P = vals.get("I"), vals.get("V"), vals.get("P")
        if I is not None and not close(abs(I), It, TABLE_TOL):
            if self.method == "mallas":
                owners = [m for m, lp in enumerate(self.loops) if lp.dir(name)]
                if len(owners) == 2:
                    ta, tb = (self._values_truth().get(f"I{m+1}", 0.0) for m in owners)
                    if close(abs(I), abs(ta) + abs(tb), TABLE_TOL) or close(abs(I), abs(ta + tb), TABLE_TOL):
                        return Outcome(False, "MESH_SHARED_SUM", {"malla": owners[0] + 1,
                                                                  "otra": f"I{owners[1]+1}"})
                    if close(abs(I), abs(ta), TABLE_TOL) or close(abs(I), abs(tb), TABLE_TOL):
                        return Outcome(False, "MESH_SHARED_MISSED", {"malla": owners[0] + 1})
            return Outcome(False, "TABLE_VALUE_WRONG", {**det, "var": "la corriente"})
        if V is not None and not close(abs(V), Vt, TABLE_TOL):
            if Vt and (close(abs(V), It / R, TABLE_TOL) or close(abs(V), R / It if It else -1, TABLE_TOL)):
                return Outcome(False, "OHM_INVERTED", {**det, "var": f"el voltaje de {name}"})
            return Outcome(False, "TABLE_VALUE_WRONG", {**det, "var": "el voltaje"})
        if P is not None and not close(abs(P), Pt, TABLE_TOL):
            if close(abs(P), It * R, TABLE_TOL) or close(abs(P), Vt * Vt * R, TABLE_TOL):
                return Outcome(False, "POWER_WRONG_FORMULA", {**det, "var": f"la potencia de {name}"})
            return Outcome(False, "TABLE_VALUE_WRONG", {**det, "var": "la potencia"})
        missing = [k for k in ("I", "V", "P") if k not in vals]
        if missing:
            nombres = {"I": "la corriente", "V": "el voltaje", "P": "la potencia"}
            return Outcome(False, "ELABORATE", partial_msg=(
                f"Lo que diste de {name} está bien. Te falta {' y '.join(nombres[k] for k in missing)}."))
        return Outcome(True, item=name)

    def _check_table(self, step, text):
        lines = [ln for ln in re.split(r"[\n;]+", text) if ln.strip()]
        names = {n.lower(): n for n in self.table_rows}
        results: list[tuple[str, Outcome]] = []
        for ln in lines:
            hit = [names[m.lower()] for m in re.findall(r"\b(R\w*)\b", ln, flags=re.I) if m.lower() in names]
            name = hit[0] if hit else (step.pending[0] if len(lines) == 1 and step.pending else None)
            if name is None or name in step.done:
                continue
            out = self._check_table_row(name, ln)
            if out is not None:
                results.append((name, out))
        if not results:
            return None
        good = [n for n, o in results if o.ok]
        for n in good:
            if n not in step.done:
                step.done.append(n)
        bad = [(n, o) for n, o in results if not o.ok]
        if bad:
            n, o = bad[0]
            if good and o.code != "ELABORATE":
                o.partial_msg = f"{', '.join(good)} está bien."
            elif good:
                o.partial_msg = f"{', '.join(good)} está bien. {o.partial_msg}"
            return o
        return Outcome(True, partial_msg=f"Correcto: {', '.join(good)}." if len(good) > 1 else "Correcto.")

    # --- balance de potencias
    def _power_totals(self) -> float:
        return sum(float(self.sol.power(e.name)) for e in self.circuit.by_kind("R"))

    def _check_balance(self, step, text):
        found = [n for n in parse_numbers(text) if n.unit in (None, "W")]
        if not found:
            return None
        # en "PE = 20·2 = 40 W" los factores no llevan unidad: si el alumno escribió watts, ésas son sus potencias
        watts = [abs(n.value) for n in found if n.unit == "W"]
        nums = watts or [abs(n.value) for n in found]
        pc = self._power_totals()
        hits = sum(close(x, pc, 0.02) for x in nums)
        low = text.lower()
        equal = re.search(r"\bp[ec]\s*=\s*p[ec]\b", low)            # "PE = PC = 40 W"
        both_named = re.search(r"\bpe\b", low) and re.search(r"\bpc\b", low)
        if hits and not (hits >= 2 or equal):
            if len(watts) >= 2 or both_named:
                return Outcome(False, "BALANCE_WRONG", {})
            return Outcome(False, "ELABORATE", partial_msg=(
                "Ese valor está bien. ¿Y la otra potencia? Necesitas las dos para comparar PE con PC."))
        if hits:
            self.c["balance"] = True
            return Outcome(True, item="v", partial_msg=(
                "PE = PC: la potencia que entregan las fuentes es la que consumen las resistencias. "
                "Tus resultados cumplen la conservación de la energía."))
        return Outcome(False, "BALANCE_WRONG", {})

    def _check_concept(self, step, text):
        low = text.lower()
        for pat, code in step.data["bad"].items():
            if re.search(pat, low) and not any(re.search(p, low) for p in step.data["ok"]):
                return Outcome(False, code, {"pista_concepto": step.data["hint"]})
        if any(re.search(p, low) for p in step.data["ok"]):
            return Outcome(True, item="c")
        return Outcome(False, "CONCEPT_WRONG", {"pista_concepto": step.data["hint"]})

    def _check_verify(self, step, text):
        low = text.lower()
        if step.data.get("balance"):
            out = self._check_balance(step, text)
            if out is not None:
                return out
        if re.search(r"potencia|balance|\bpe\b|\bpc\b|energ[ií]a", low):
            step.data["balance"] = True
            if len([n for n in parse_numbers(text) if n.unit in (None, "W")]) >= 2 or \
                    re.search(r"\bp[ec]\s*=\s*p[ec]\b", low):
                return self._check_balance(step, text)
            return Outcome(False, "ELABORATE", partial_msg=(
                "Buena comprobación. Hazla: ¿cuánto vale la potencia que entregan las fuentes (PE) y "
                "cuánto la que consumen las resistencias (PC)?"))
        if re.search(r"sustitu|reemplaz|simul|multisim|phet|kcl|kvl|lck|lvk|kirchhoff|medir|"
                     r"mult[ií]metro|comprob|verific|checar|evaluar", low):
            if re.search(r"simul|multisim|phet|medir|mult[ií]metro", text.lower()) and \
                    not any(s.kind == "sim" for s in self.steps):
                self.steps.insert(self.idx + 1, Step("sim", "sim", items=["s"]))
            return Outcome(True, item="v", partial_msg="Esa es una buena comprobación.")
        return Outcome(False, "VERIFY_VAGUE")

    def _check_sim(self, step, text):
        claims = parse_numbers(text, self.vars)
        if not claims:
            return None
        expected = ([(v, self.truth[v]) for v in self.vars] if self.vars
                    else [(t.describe(), tv) for t, tv in zip(self.targets, self.target_truth)])
        if not expected:
            # sólo Req y la tabla: basta con que lo medido coincida con alguna magnitud del circuito
            known = [abs(x) for n in self.table_rows for x in self._branch_values(n)]
            known += [float(self.reducer.req)] if self.reducer else []
            if any(close(abs(c.value), k, 0.05) for c in claims if c.unit != "Ω" for k in known):
                self.c["simulador"] = True
                return Outcome(True, item="s", partial_msg="Los resultados son consistentes con tus cálculos.")
            return Outcome(False, "VALUE_WRONG", {"ecuacion_falla": "tus mediciones"},
                           partial_msg="Lo que mediste no coincide con tus cálculos.")
        vals = self._assign_claims(claims, [n for n, _ in expected])
        if not vals:
            return None
        diffs = [n for n, tv in expected if n in vals and not close(abs(vals[n]), abs(tv), 0.05)]
        if not diffs:
            self.c["simulador"] = True
            return Outcome(True, item="s", partial_msg="Los resultados son consistentes con tus cálculos.")
        return Outcome(False, "VALUE_WRONG", {"ecuacion_falla": ", ".join(diffs)},
                       partial_msg=f"Hay diferencias en {', '.join(diffs)}.")

    def _check_metacog(self, step, text):
        if len(text.split()) >= 6 or self.metacog_asked:
            self.transcript.append({"t": time.time(), "rol": "metacognicion", "texto": text,
                                    "paso": "metacog", "nivel": self.level})
            return Outcome(True, item="m", partial_msg="Gracias por reflexionarlo.")
        self.metacog_asked = True
        return Outcome(False, "ELABORATE", partial_msg="Cuéntame un poco más, con tus palabras.")

    # --- ecuaciones y valores
    def _check_equations(self, step, text):
        raws = split_equations(text) or ([text] if "=" in text else [])
        if not raws:
            return None
        results = []
        for raw in raws:
            try:
                pe = parse_linear_equation(raw, self.vars, self.constants)
            except ParseError as exc:
                return Outcome(False, "PARSE_ERROR", {"vars": ", ".join(self.vars)},
                               partial_msg=str(exc))
            d = diagnose_equation(pe, self.refs, step.pending, self.mappings, self._prefix,
                                  self.circuit, self.loops)
            results.append((pe, d))
        oks = [(pe, d) for pe, d in results if d.ok]
        for pe, d in oks:
            if d.detail["index"] not in step.done:
                step.done.append(d.detail["index"])
            self._narrow(pe)
        bad = [(pe, d) for pe, d in results if not d.ok]
        if bad:
            pe, d = bad[0]
            prefix = "Tienes bien una parte. " if oks else ""
            if d.code == "EQ_VALID_COMBINATION":
                target = self.refs[step.pending[0]].label if step.pending else "ecuación pedida"
                d.detail["ecuacion"] = target
            return Outcome(False, d.code, d.detail, partial_msg=prefix.strip())
        new = [d for _, d in oks if d.code == "OK"]
        if not new:
            return Outcome(True, partial_msg="Esa ecuación ya la tenías.")
        item = new[-1].detail["index"]
        if item in step.done:
            step.done.remove(item)
        return Outcome(True, item=item, partial_msg=self._eq_praise(step))

    def _eq_praise(self, step: Step) -> str:
        remaining = len(step.pending) - 1
        if len(step.done) == 0 and remaining:
            return "Correcto. Ya construiste la primera ecuación. Ahora intenta la siguiente sin mi ayuda."
        return "Correcto."

    def _assign_claims(self, claims, names: list[str]) -> dict[str, float]:
        vals: dict[str, float] = {}
        labeled = [c for c in claims if c.var and c.var in names]
        for c in labeled:
            vals[c.var] = c.value
        rest = [n for n in names if n not in vals]
        unl = [c for c in claims if not (c.var and c.var in names)]
        if rest and len(unl) >= len(rest) and not labeled:
            unl = unl[-len(rest):]
            for n, c in zip(rest, unl):
                vals[n] = c.value
        elif rest and len(unl) == len(rest):
            for n, c in zip(rest, unl):
                vals[n] = c.value
        return vals

    def _values_truth(self) -> dict[str, float]:
        """Verdad expresada en la nomenclatura del alumno (mallas: según el mapeo)."""
        if self.method == "mallas" and self.mappings:
            mp = self.mappings[0]
            return {f"I{k+1}": s * self.truth[f"I{p+1}"] for k, (p, s) in enumerate(mp)}
        return dict(self.truth)

    def _check_values(self, step, text):
        claims = parse_numbers(text, self.vars)
        if not claims:
            return None
        vals = self._assign_claims(claims, step.pending)
        if not vals:
            return Outcome(False, "PARSE_ERROR", {"vars": ", ".join(step.pending)},
                           partial_msg=f"Indícame cada valor con su nombre, por ejemplo {step.pending[0]} = …")
        if self.method == "mallas" and self.mappings and len(self.mappings) > 1 and not step.done:
            # todas horario (FMR) o todas antihorario (CMR): los valores deciden el sentido
            def score(mp):
                t = {f"I{k+1}": s * self.truth[f"I{p+1}"] for k, (p, s) in enumerate(mp)}
                return sum(diagnose_value(v, t[n]).ok for n, v in vals.items() if n in t)
            best = max(self.mappings, key=score)
            self.mappings = [best] + [mp for mp in self.mappings if mp != best]
        truth = self._values_truth()
        good, wrong = [], []
        for n, v in vals.items():
            d = diagnose_value(v, truth[n])
            (good if d.ok else wrong).append((n, d))
        for n, _ in good:
            if n not in step.done:
                step.done.append(n)
        if wrong:
            n, d = wrong[0]
            full = {**{k: truth[k] for k in self.vars}, **{k: v for k, v in vals.items()}}
            # valores en espacio de referencia para revisar el sistema
            ref_vals = self._to_ref_values(full)
            failing = check_system_values(ref_vals, self.refs)
            code = d.code
            if code == "VALUE_WRONG" and failing:
                code = "VALUE_ALGEBRA"
            msg = f"{', '.join(g for g, _ in good)} está bien. " if good else ""
            return Outcome(False, code, {"var": n, "ecuacion_falla": failing[0] if failing else "tus ecuaciones"},
                           partial_msg=msg.strip())
        if step.complete:
            return Outcome(True, partial_msg="Muy bien.")
        return Outcome(True, partial_msg=f"Bien. Te falta {', '.join(step.pending)}.")

    def _to_ref_values(self, vals: dict[str, float]) -> dict[str, float]:
        if self.method == "mallas" and self.mappings:
            mp = self.mappings[0]
            return {f"I{p+1}": s * vals[f"I{k+1}"] for k, (p, s) in enumerate(mp)}
        return vals

    def _target_mutations(self, t: Target) -> dict[str, float]:
        c, sol = self.circuit, self.sol
        muts: dict[str, float] = {}
        if t.quantity == "Req":
            return series_parallel_mutations(c, float(target_value(c, sol, t)))
        e = c.get(t.element)
        i, v = float(sol.elem_i[e.name]), float(sol.elem_v[e.name])
        srcV = c.by_kind("V")
        if e.kind == "R":
            R = float(e.value)
            if t.quantity == "I":
                if v:
                    muts["OHM_INVERTED"] = R / v
                muts["OHM_MULTIPLIED"] = v * R
                if len(srcV) == 1:
                    Vs = float(srcV[0].value)
                    muts["SP_PARALLEL_SUMMED"] = Vs / sum(float(r.value) for r in c.by_kind("R"))
                    muts["OHM_MULTIPLIED#2"] = Vs * R
            if t.quantity == "V":
                if i:
                    muts["OHM_INVERTED"] = R / i
                    muts["OHM_MULTIPLIED"] = i / R
            if t.quantity == "P":
                muts["POWER_WRONG_FORMULA"] = v * v * R
                muts["POWER_WRONG_FORMULA#2"] = i * i / R if R else None
                if len(srcV) == 1:
                    muts["POWER_WRONG_FORMULA#3"] = float(srcV[0].value) * i
        # mallas: usar una sola corriente de malla (o la suma) en una rama compartida
        if self.method == "mallas" and e.kind == "R":
            owners = [m for m, lp in enumerate(self.loops) if lp.dir(e.name)]
            if len(owners) == 2:
                I = [self.truth[f"I{m+1}"] for m in owners]
                R = float(e.value)
                for code, cur in (("MESH_SHARED_MISSED", I[0]), ("MESH_SHARED_MISSED#2", I[1]),
                                  ("MESH_SHARED_SUM", I[0] + I[1])):
                    val = {"I": cur, "V": cur * R, "P": cur * cur * R}.get(t.quantity)
                    if val is not None:
                        muts[code] = val
        return muts

    def _check_targets(self, step, text):
        claims = parse_numbers(text)
        if not claims:
            return None
        item = step.pending[0]
        t, truth = self.targets[item], self.target_truth[item]
        value = claims[-1].value
        d = diagnose_value(value, truth, self._target_mutations(t))
        if d.ok:
            return Outcome(True, item=item, partial_msg="Correcto.")
        if d.code == "VALUE_SIGN" and t.quantity in ("I", "V"):
            # el signo depende del sentido de referencia: se acepta si lo explica
            return Outcome(True, item=item, partial_msg=(
                "El valor es correcto en magnitud; el signo sólo indica el sentido respecto a tu referencia."))
        code = d.code
        if code == "VALUE_WRONG":
            # ¿es la magnitud correcta pero de OTRO elemento?
            for e in self.circuit.elements:
                if e.name == t.element or t.quantity not in ("I", "V", "P"):
                    continue
                other = {"I": self.sol.elem_i, "V": self.sol.elem_v}.get(t.quantity)
                ov = float(other[e.name]) if other else float(self.sol.power(e.name))
                if close(abs(value), abs(ov)):
                    code = "VALUE_OTHER_ELEMENT"
                    break
            else:
                code = "TARGET_WRONG"   # sin entrada en el banco: usa la escalera del paso
        return Outcome(False, code, {"var": t.describe(), "elemento": t.element or "",
                                     "ecuacion_falla": t.describe()})

    def _check_submit(self, step, text):
        """Modo Verificar: revisa todo lo que manda el alumno de una vez, sin resolver."""
        report, first_bad = [], None
        eq_step = next((s_ for s_ in self.steps if s_.kind == "equations"), None)
        if eq_step:
            for raw in split_equations(text):
                if not looks_like_equation(raw, self.vars):
                    continue
                try:
                    pe = parse_linear_equation(raw, self.vars, self.constants)
                except ParseError:
                    continue
                d = diagnose_equation(pe, self.refs, eq_step.pending, self.mappings, self._prefix,
                                      self.circuit, self.loops)
                if d.code == "EQ_VALID_COMBINATION" and sum(1 for c in pe.coefs.values() if abs(c) > 1e-12) == 1:
                    continue    # "I1 = 1.8182" es un resultado: se revisa abajo con los valores
                if d.ok and d.detail["index"] in eq_step.pending:
                    eq_step.done.append(d.detail["index"])
                    self._narrow(pe)
                    report.append(f"✔ {raw.strip()}")
                elif not d.ok:
                    report.append(f"✘ {raw.strip()}")
                    first_bad = first_bad or d
        val_step = next((s_ for s_ in self.steps if s_.kind == "values"), None)
        if val_step:
            truth = self._values_truth()
            for c in parse_numbers(text, self.vars):
                if c.var not in val_step.pending:
                    continue
                if diagnose_value(c.value, truth[c.var]).ok:
                    val_step.done.append(c.var)
                    report.append(f"✔ {c.var} = {c.value:g}")
                else:
                    report.append(f"✘ {c.var} = {c.value:g}")
        if eq_step is None and val_step is None:
            # Ohm y serie/paralelo: se reconocen los resultados; lo que falte se pregunta por pasos
            nums = [abs(c.value) for c in parse_numbers(text) if c.unit != "Ω"]
            if not nums:
                return None
            red = next((s_ for s_ in self.steps if s_.kind == "reduce"), None)
            if red and "req" in red.pending and any(close(v, float(self.reducer.req), TABLE_TOL) for v in nums):
                red.done.append("req")
                report.append(f"✔ Req = {fmt(self.reducer.req)} Ω")
            tg = next((s_ for s_ in self.steps if s_.kind == "targets"), None)
            for i in list(tg.pending) if tg else []:
                if any(close(v, abs(self.target_truth[i]), TABLE_TOL) for v in nums):
                    tg.done.append(i)
                    report.append(f"✔ {self.targets[i].describe()}")
            if not report:
                report.append("todavía no encuentro los resultados que pide el problema")
        if not report:
            return None
        step.items, step.done = ["s"], ["s"]
        return Outcome(True, detail={"summary": "Revisé tu procedimiento: " + "; ".join(report) + ".",
                                     "bad": first_bad})

    # ============================================================ indicadores
    def indicators(self) -> dict:
        p = self.c["pistas"]
        return {
            "Intentos del estudiante": self.c["intentos"],
            "Preguntas socráticas realizadas": self.c["preguntas"],
            "Pistas nivel 1": p[1],
            "Pistas nivel 2": p[2],
            "Pistas nivel 3 o superiores": p[3] + p[4] + p[5],
            "Errores conceptuales": self.c["conceptuales"],
            "Errores procedimentales": self.c["procedimentales"],
            "Corrección autónoma": "Sí" if self.c["autonoma"] else "No",
            "Solicitó respuesta directa": "Sí" if self.c["directa"] else "No",
            "Verificó con simulador": "Sí" if self.c["simulador"] else "No",
            "Verificó con balance de potencias": "Sí" if self.c["balance"] else "No",
            "Justificaciones dadas": self.c["justificaciones"],
            "Nivel final de ayuda": self.c["nivel_final"],
            "Códigos de error": ",".join(c for c in self.c["errores"] if c != "AHEAD"),
            "Completado": "Sí" if self.finished else "No",
            "Duración (min)": round((time.time() - self.t0) / 60, 1),
        }

    # ============================================= límites para el LLM (guardia)
    def forbidden_numbers(self) -> list[float]:
        """Resultados que el alumno todavía no ha obtenido: el LLM no puede decirlos."""
        out = []
        try:
            vs = self._find_step("values")
            truth = self._values_truth()
            out += [truth[v] for v in vs.pending]
        except KeyError:
            pass
        try:
            ts = self._find_step("targets")
            out += [self.target_truth[i] for i in ts.pending]
        except KeyError:
            pass
        known = {float(e.value) for e in self.circuit.elements}
        return [x for x in out if abs(x) > 1e-9 and not any(close(abs(x), k) for k in known)]

    def pending_equations(self) -> list[an.RefEq]:
        try:
            st = self._find_step("equations")
        except KeyError:
            return []
        return [self.refs[i] for i in st.pending]


def build_session(circuit: Circuit, method: str, mode: str, bank: ErrorBank | None = None) -> Session:
    try:
        return Session(circuit, method, mode, bank)
    except CircuitError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise CircuitError(f"No pude preparar el problema: {exc}") from exc


__all__ = ["Session", "build_session", "METHODS", "MODES", "Diagnosis", "fill"]
