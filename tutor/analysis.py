"""Sistemas de referencia para método de mallas y método de nodos.

Las mallas son las "ventanas" del dibujo. Una netlist no trae geometría y un
mismo circuito admite varios dibujos planos (p.ej. el orden de ramas en
paralelo), así que las mallas se toman de la declaración del problema
(circuit.meshes). Sin declaración se propone un encaje plano como sugerencia,
que el alumno o el profesor deben confirmar. Todas las mallas se recorren en el
mismo sentido de giro, de modo que en una rama compartida la corriente neta es
siempre Ia − Ib.

Cada ecuación de referencia se guarda como fila lineal: coefs · x = rhs, con
metadatos de qué elementos aportan cada término (sirve para diagnosticar).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import sympy as sp

from .circuit import Circuit, CircuitError


@dataclass
class Loop:
    """Lazo orientado: lista de (elemento, dirección). dir=+1 si se recorre n1->n2."""
    items: list[tuple[str, int]]

    @property
    def names(self) -> list[str]:
        return [n for n, _ in self.items]

    def dir(self, name: str) -> int:
        for n, d in self.items:
            if n == name:
                return d
        return 0


@dataclass
class RefEq:
    """Ecuación lineal de referencia: sum(coefs[v]*v) = rhs."""
    coefs: dict[str, sp.Rational]
    rhs: sp.Rational
    kind: str            # "kvl", "supermesh", "constraint", "kcl", "supernode"
    label: str           # "malla 1", "supermalla 1-2", "nodo a"...
    owner: tuple = ()    # índices de malla / nombres de nodo que la definen
    # para diagnóstico: aportes por elemento
    parts: dict = field(default_factory=dict)
    source: str = ""     # fuente que impone la ecuación (sólo restricciones)

    def as_text(self, var_names: dict[str, str] | None = None) -> str:
        var_names = var_names or {}
        s = ""
        for v, c in self.coefs.items():
            if c == 0:
                continue
            t = f"{_num(abs(c))}{var_names.get(v, v)}"
            if not s:
                s = t if c > 0 else f"−{t}"
            else:
                s += f" + {t}" if c > 0 else f" − {t}"
        rhs = _num(self.rhs) if self.rhs not in (1, -1) else str(self.rhs)
        return f"{s or '0'} = {rhs}"


def _num(x) -> str:
    x = sp.nsimplify(x)
    if x == 1:
        return ""
    if x.is_Integer:
        return str(x)
    if x.is_Rational and x.q <= 12:
        return f"({x})"
    return f"{float(x):.4g}"


# ====================================================================== mallas
def loop_from_names(circuit: Circuit, names: list[str], label: str = "") -> tuple[Loop, bool]:
    """Lazo orientado a partir de la lista de elementos de una malla.

    Si los elementos vienen en orden de recorrido, ese orden fija el sentido.
    Devuelve (lazo, ordenado): ordenado=False si hubo que reconstruir el
    recorrido porque la lista no seguía el camino."""
    label = label or "la malla"
    elems = [circuit.get(n) for n in names]
    for e in elems:
        if e.n1 == e.n2:
            raise CircuitError(f"{e.name} está en corto: no forma parte de {label}.")
    if len(elems) < 2:
        raise CircuitError(f"{label.capitalize()} necesita al menos dos elementos para cerrarse.")
    degree: dict[str, int] = {}
    for e in elems:
        for n in (e.n1, e.n2):
            degree[n] = degree.get(n, 0) + 1
    if any(d != 2 for d in degree.values()):
        raise CircuitError(f"Los elementos de {label} no forman un lazo cerrado simple.")

    def walk(seq):
        first, second = seq[0], seq[1]
        shared = {first.n1, first.n2} & {second.n1, second.n2}
        start = first.n1 if len(shared) == 2 or first.n2 in shared else first.n2
        cur, items = start, []
        for e in seq:
            if e.n1 == cur:
                items.append((e.name, 1))
                cur = e.n2
            elif e.n2 == cur:
                items.append((e.name, -1))
                cur = e.n1
            else:
                return None
        return items if cur == start else None

    items = walk(elems)
    if items is not None:
        # con dos elementos el orden no distingue el sentido de giro
        return Loop(items), len(elems) >= 3
    # la lista no seguía el camino: reconstruirlo (sentido arbitrario)
    pending, seq = list(elems[1:]), [elems[0]]
    cur = elems[0].n2
    while pending:
        nxt = next((e for e in pending if cur in (e.n1, e.n2)), None)
        if nxt is None:
            raise CircuitError(f"Los elementos de {label} no forman un solo lazo.")
        pending.remove(nxt)
        seq.append(nxt)
        cur = nxt.n2 if nxt.n1 == cur else nxt.n1
    items = walk(seq)
    if items is None:
        raise CircuitError(f"Los elementos de {label} no forman un lazo cerrado simple.")
    return Loop(items), False


def expected_mesh_count(circuit: Circuit) -> int:
    act = circuit.active_elements
    nodes = {n for e in act for n in (e.n1, e.n2)}
    return len(act) - len(nodes) + 1


def meshes_from_spec(circuit: Circuit, spec: list[list[str]] | None = None) -> list[Loop]:
    """Mallas declaradas -> lazos validados y orientados en el mismo sentido."""
    spec = spec if spec is not None else circuit.meshes
    built = [loop_from_names(circuit, names, f"la malla {k+1}") for k, names in enumerate(spec)]
    loops = [lp for lp, _ in built]
    need = expected_mesh_count(circuit)
    if len(loops) != need:
        raise CircuitError(f"El circuito tiene {need} mallas (ramas − nodos + 1) y se declararon "
                           f"{len(loops)}.")
    owners: dict[str, list[int]] = {}
    for k, lp in enumerate(loops):
        for n in lp.names:
            owners.setdefault(n, []).append(k)
    for n, ks in owners.items():
        if len(ks) > 2:
            raise CircuitError(f"{n} aparece en {len(ks)} mallas; en un dibujo, cada elemento "
                               "separa a lo más dos ventanas. ¿Hay alguna malla combinada?")
    names = [e.name for e in circuit.active_elements]
    M = sp.Matrix([[lp.dir(n) for n in names] for lp in loops])
    if M.rank() != len(loops):
        raise CircuitError("Las mallas declaradas no son independientes: alguna se puede formar "
                           "combinando otras.")
    # mismo sentido de giro: en cada elemento compartido, direcciones opuestas
    root = next((k for k, (_, ordered) in enumerate(built) if ordered), 0)
    flip: dict[int, bool] = {root: False}
    queue = [root]
    while queue:
        a = queue.pop()
        for n, ks in owners.items():
            if a not in ks or len(ks) != 2:
                continue
            b = ks[1] if ks[0] == a else ks[0]
            same = loops[a].dir(n) * loops[b].dir(n) > 0
            want = flip[a] != same   # si van igual, b debe invertirse respecto de a
            if b in flip:
                if flip[b] != want:
                    raise CircuitError("Las mallas declaradas no pueden girar todas en el mismo "
                                       "sentido: revisa que sean las ventanas del dibujo.")
            else:
                flip[b] = want
                queue.append(b)
    out = []
    for k, lp in enumerate(loops):
        f = flip.get(k, False)
        out.append(Loop([(n, -d if f else d) for n, d in lp.items]))
    return out


def meshes_to_text(loops: list[Loop]) -> str:
    return "\n".join(f"MALLA {k+1}: {' '.join(lp.names)}" for k, lp in enumerate(loops))


def find_faces(circuit: Circuit) -> list[Loop]:
    """Todas las caras de un encaje plano (incluida la exterior)."""
    import networkx as nx   # sólo hace falta para proponer mallas; en el navegador pesa mucho
    G = nx.Graph()
    for e in circuit.active_elements:
        mid = f"~{e.name}"
        G.add_edge(e.n1, mid)
        G.add_edge(mid, e.n2)
    planar, emb = nx.check_planarity(G)
    if not planar:
        raise CircuitError("El circuito no es plano: el método de mallas no aplica, usa nodos.")
    visited: set = set()
    faces: list[Loop] = []
    for u, v in emb.edges():
        if (u, v) in visited:
            continue
        seq = emb.traverse_face(u, v, mark_half_edges=visited)
        # convertir secuencia de nodos a elementos orientados
        n = len(seq)
        acc: dict[str, int] = {}
        order: list[str] = []
        for k, node in enumerate(seq):
            if str(node).startswith("~"):
                name = node[1:]
                prev_, next_ = seq[k - 1], seq[(k + 1) % n]
                e = circuit.get(name)
                d = 1 if (prev_, next_) == (e.n1, e.n2) else -1 if (prev_, next_) == (e.n2, e.n1) else 0
                if name not in acc:
                    order.append(name)
                acc[name] = acc.get(name, 0) + d
        items = [(nm, acc[nm]) for nm in order if acc[nm] != 0]
        if items:
            faces.append(Loop(items))
    return faces


def default_meshes(circuit: Circuit) -> list[Loop]:
    """Mallas declaradas si existen; si no, una propuesta que hay que confirmar."""
    if circuit.meshes:
        return meshes_from_spec(circuit)
    return guess_meshes(circuit)


def guess_meshes(circuit: Circuit) -> list[Loop]:
    """Propuesta de mallas a partir de un encaje plano cualquiera.

    Es sólo una sugerencia: sin el dibujo no se sabe cuál cara es la exterior
    ni en qué orden van las ramas en paralelo."""
    faces = find_faces(circuit)
    expected = expected_mesh_count(circuit)
    if len(faces) != expected + 1:
        raise CircuitError("No pude identificar las mallas de este circuito.")
    outer = max(range(len(faces)), key=lambda k: (len(faces[k].items), k))
    return [f for k, f in enumerate(faces) if k != outer]


def mesh_system(circuit: Circuit, loops: list[Loop]) -> list[RefEq]:
    """Ecuaciones de referencia en variables I1..In (con supermallas si hace falta)."""
    n = len(loops)
    I = [f"I{k+1}" for k in range(n)]
    elem = {e.name: e for e in circuit.elements}
    membership: dict[str, list[tuple[int, int]]] = {}
    for m, lp in enumerate(loops):
        for name, d in lp.items:
            membership.setdefault(name, []).append((m, d))

    def branch_current(name: str) -> dict[str, sp.Rational]:
        return {I[m]: sp.Integer(d) for m, d in membership.get(name, [])}

    # KVL "crudas" con incógnitas de voltaje en fuentes de corriente
    raw = []
    for m, lp in enumerate(loops):
        coefs = {v: sp.Integer(0) for v in I}
        vunk: dict[str, sp.Rational] = {}
        rhs = sp.Integer(0)
        parts = {"R": [], "V": [], "I": []}
        for name, d in lp.items:
            e = elem[name]
            if e.kind == "R":
                for var, s in branch_current(name).items():
                    coefs[var] += d * s * e.value
                parts["R"].append(name)
            elif e.kind == "V":
                # caída n1->n2 recorriendo en d es d*V; pasa al lado derecho
                rhs -= d * e.value
                parts["V"].append((name, -d * e.value))
            else:
                vunk[name] = vunk.get(name, 0) + d
                parts["I"].append(name)
        raw.append((coefs, vunk, rhs, parts))

    eqs: list[RefEq] = []
    # agrupar mallas unidas por fuentes de corriente compartidas
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    csrc = circuit.by_kind("I")
    for e in csrc:
        ms = membership.get(e.name, [])
        if len(ms) == 2:
            parent[find(ms[0][0])] = find(ms[1][0])
        # restricción: corriente de rama = valor de la fuente
        coefs = {v: sp.Integer(0) for v in I}
        for m, d in ms:
            coefs[I[m]] += d
        owner = tuple(sorted(m for m, _ in ms))
        label = (f"ecuación directa de la malla {owner[0]+1}" if len(owner) == 1 else
                 f"ecuación auxiliar de la supermalla {'-'.join(str(m+1) for m in owner)}")
        eqs.append(RefEq(coefs, e.value, "constraint", label,
                         owner=owner, parts={"I": [e.name]}, source=e.name))
    groups: dict[int, list[int]] = {}
    for m in range(n):
        groups.setdefault(find(m), []).append(m)
    for members in groups.values():
        coefs = {v: sp.Integer(0) for v in I}
        vunk: dict[str, sp.Rational] = {}
        rhs = sp.Integer(0)
        parts = {"R": [], "V": [], "I": []}
        for m in members:
            c, vu, r, p = raw[m]
            for v in I:
                coefs[v] += c[v]
            for k, val in vu.items():
                vunk[k] = vunk.get(k, 0) + val
            rhs += r
            for key in parts:
                parts[key] += p[key]
        if any(val != 0 for val in vunk.values()):
            continue  # fuente de corriente en rama exterior: la define la restricción
        if len(members) == 1:
            eqs.append(RefEq(coefs, rhs, "kvl", f"malla {members[0]+1}", (members[0],), parts))
        else:
            lbl = "-".join(str(m + 1) for m in members)
            eqs.append(RefEq(coefs, rhs, "supermesh", f"ecuación real de la supermalla {lbl}",
                             tuple(members), parts))
    # orden del método de formato: malla por malla y, dentro de cada una,
    # primero la ecuación directa/auxiliar y después la ecuación real
    eqs.sort(key=lambda q: (min(q.owner), 0 if q.kind == "constraint" else 1, q.owner))
    return eqs


def solve_system(eqs: list[RefEq]) -> dict[str, sp.Rational]:
    vars_ = list(eqs[0].coefs.keys()) if eqs else []
    A = sp.Matrix([[q.coefs[v] for v in vars_] for q in eqs])
    b = sp.Matrix([q.rhs for q in eqs])
    if A.shape[0] != A.shape[1] or A.det() == 0:
        raise CircuitError("El sistema de ecuaciones no tiene solución única.")
    x = A.LUsolve(b)
    return {v: sp.nsimplify(x[k]) for k, v in enumerate(vars_)}


def shared_elements(loops: list[Loop]) -> dict[str, tuple[int, int]]:
    """Elementos que pertenecen a exactamente dos mallas: {nombre: (m1, m2)}."""
    owners: dict[str, list[int]] = {}
    for m, lp in enumerate(loops):
        for name in lp.names:
            owners.setdefault(name, []).append(m)
    return {k: tuple(v) for k, v in owners.items() if len(v) == 2}


# ======================================================================= nodos
def node_var(node: str) -> str:
    return f"V{node}"


def nodal_system(circuit: Circuit) -> list[RefEq]:
    """KCL (corrientes que SALEN = 0) por nodo, con supernodos para fuentes de V."""
    nodes = circuit.non_ground_nodes
    V = {n: node_var(n) for n in nodes}
    parent = {n: n for n in circuit.nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    eqs: list[RefEq] = []
    for e in circuit.by_kind("V"):
        parent[find(e.n1)] = find(e.n2)
        coefs = {V[n]: sp.Integer(0) for n in nodes}
        if e.n1 != "0":
            coefs[V[e.n1]] += 1
        if e.n2 != "0":
            coefs[V[e.n2]] -= 1
        free = [n for n in (e.n1, e.n2) if n != "0"]
        label = (f"ecuación directa del nodo {free[0]}" if len(free) == 1 else
                 f"ecuación de la fuente {e.name} del supernodo {free[0]}-{free[1]}")
        eqs.append(RefEq(coefs, e.value, "constraint", label,
                         owner=tuple(free), parts={"V": [e.name]}, source=e.name))

    def kcl(n: str):
        coefs = {V[k]: sp.Integer(0) for k in nodes}
        rhs = sp.Integer(0)
        parts = {"R": [], "I": [], "Vsrc": []}
        for e in circuit.elements:
            if n not in (e.n1, e.n2):
                continue
            other = e.n2 if e.n1 == n else e.n1
            if e.kind == "R":
                g = 1 / e.value
                coefs[V[n]] += g
                if other != "0":
                    coefs[V[other]] -= g
                parts["R"].append(e.name)
            elif e.kind == "I":
                leaving = e.value if e.n1 == n else -e.value
                rhs -= leaving
                parts["I"].append((e.name, -leaving))
            else:
                parts["Vsrc"].append(e.name)
        return coefs, rhs, parts

    groups: dict[str, list[str]] = {}
    for n in nodes:
        groups.setdefault(find(n), []).append(n)
    for root, members in groups.items():
        if find("0") == root:
            continue  # nodos fijados por fuentes a tierra: los definen las restricciones
        coefs = {V[k]: sp.Integer(0) for k in nodes}
        rhs = sp.Integer(0)
        parts = {"R": [], "I": [], "Vsrc": []}
        for n in members:
            c, r, p = kcl(n)
            for k in coefs:
                coefs[k] += c[k]
            rhs += r
            for key in parts:
                parts[key] += p[key]
        # resistencias internas al supernodo se cancelan solas
        if len(members) == 1:
            eqs.append(RefEq(coefs, rhs, "kcl", f"nodo {members[0]}", tuple(members), parts))
        else:
            eqs.append(RefEq(coefs, rhs, "supernode", f"supernodo {'-'.join(members)}",
                             tuple(members), parts))
    pos = {n: k for k, n in enumerate(nodes)}
    eqs.sort(key=lambda q: (min(pos[n] for n in q.owner), 0 if q.kind == "constraint" else 1,
                            len(q.owner)))
    return eqs


def mesh_types(circuit: Circuit, loops: list[Loop]) -> dict[int, str]:
    """Tipo de cada malla según el método de formato: real, fantasma o supermalla."""
    types = {k: "real" for k in range(len(loops))}
    for e in circuit.by_kind("I"):
        owners = [k for k, lp in enumerate(loops) if lp.dir(e.name)]
        if len(owners) == 1:
            types[owners[0]] = "fantasma"
        elif len(owners) == 2:
            for k in owners:
                if types[k] != "fantasma":
                    types[k] = "supermalla"
    return types


def node_types(circuit: Circuit) -> dict[str, str]:
    """Tipo de cada nodo (sin la referencia): real, fantasma o supernodo."""
    parent = {n: n for n in circuit.nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in circuit.by_kind("V"):
        parent[find(e.n1)] = find(e.n2)
    ground = find("0")
    out = {}
    for n in circuit.non_ground_nodes:
        touches = any(n in (e.n1, e.n2) for e in circuit.by_kind("V"))
        if not touches:
            out[n] = "real"
        elif find(n) == ground:
            out[n] = "fantasma"
        else:
            out[n] = "supernodo"
    return out
