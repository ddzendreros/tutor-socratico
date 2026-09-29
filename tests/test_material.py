"""Validación contra el material de clase: cada problema del banco debe
reproducir los resultados publicados por el profesor."""
import pytest

from tutor import analysis as an
from tutor.problems import DEFAULT_DIR, load_bank
from tutor.solver import solve, target_value

BANK = load_bank()
TOL = 0.01


def _value(p, key):
    c = p.circuit
    if key == "Req":
        t = next(t for t in c.targets if t.quantity == "Req")
        return float(target_value(c, solve(c), t))
    if " " in key:                       # "I R1", "P R1", "V R2"
        q, name = key.split()
        sol = solve(c)
        return float({"I": sol.elem_i, "V": sol.elem_v}[q][name]) if q in "IV" else float(sol.power(name))
    if p.method == "mallas":
        loops = an.default_meshes(c)
        return float(an.solve_system(an.mesh_system(c, loops))[key])
    return float(solve(c).node_v[key[1:]])


@pytest.mark.parametrize("p", BANK, ids=[p.id for p in BANK])
def test_reproduces_published_results(p):
    assert p.expected, "el problema debe traer RESPUESTA"
    for key, published in p.expected.items():
        got = _value(p, key)
        assert got == pytest.approx(published, rel=TOL, abs=0.006), (key, got, published)


@pytest.mark.parametrize("p", [p for p in BANK if p.method == "mallas"], ids=lambda p: p.id)
def test_declared_meshes_are_used_and_valid(p):
    loops = an.default_meshes(p.circuit)
    assert [set(lp.names) for lp in loops] == [set(m) for m in p.circuit.meshes]


def test_professor_mesh_equations_caso1():
    p = next(p for p in BANK if p.id.startswith("mallas-01"))
    refs = an.mesh_system(p.circuit, an.default_meshes(p.circuit))
    assert [q.as_text() for q in refs] == ["30I1 − 20I2 = 0", "−20I1 + 50I2 = 100"]


def test_supermesh_labels_follow_class_terms():
    p = next(p for p in BANK if p.id.startswith("mallas-02"))
    refs = an.mesh_system(p.circuit, an.default_meshes(p.circuit))
    labels = [q.label for q in refs]
    assert labels[0] == "ecuación auxiliar de la supermalla 1-2"
    assert labels[1] == "ecuación real de la supermalla 1-2"
    assert an.mesh_types(p.circuit, an.default_meshes(p.circuit)) == {0: "supermalla", 1: "supermalla", 2: "real"}


def test_node_types_supernode_example():
    p = next(p for p in BANK if p.id.startswith("nodos-03"))
    assert an.node_types(p.circuit) == {"1": "fantasma", "2": "supernodo", "3": "supernodo", "5": "real"}


@pytest.mark.parametrize("p", [p for p in BANK if p.image], ids=lambda p: p.id)
def test_declared_drawing_exists(p):
    assert (DEFAULT_DIR / p.image).is_file(), f"IMAGEN: {p.image} no está en problemas/"
