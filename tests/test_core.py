"""Pruebas del núcleo: solver, mallas/nodos, diagnóstico y flujo del tutor."""
import random

import pytest
import sympy as sp

from tutor import analysis as an
from tutor.circuit import CircuitError, Target, parse_netlist, parse_value
from tutor.diagnose import diagnose_equation, diagnose_value, mesh_mappings
from tutor.engine import Session
from tutor.mathparse import ParseError
from tutor.mathparse import parse_linear_equation as P
from tutor.mathparse import parse_numbers
from tutor.solver import equivalent_resistance, solve

DOCX = "V1 a 0 12\nR1 a b 4\nR2 b 0 2\nR3 b c 6\nV2 0 c 6"


# --------------------------------------------------------------- circuito/solver
def test_values_with_prefixes():
    assert parse_value("2.2k") == 2200
    assert parse_value("500m") == sp.Rational(1, 2)
    assert parse_value("4Ω") == 4


def test_series_example_from_excel():
    c = parse_netlist("V1 a 0 12\nR1 a b 4\nR2 b 0 8")
    s = solve(c)
    assert s.elem_i["R1"] == 1 and s.elem_i["R2"] == 1
    assert equivalent_resistance(c, "a", "0", exclude="V1") == 12


def test_docx_mesh_example_exact():
    c = parse_netlist(DOCX)
    loops = an.default_meshes(c)
    refs = an.mesh_system(c, loops)
    sol = an.solve_system(refs)
    assert sorted(float(v) for v in sol.values()) == pytest.approx(sorted([27 / 11, 15 / 11]))


def test_invalid_circuits():
    with pytest.raises(CircuitError):
        parse_netlist("R1 a b 4")                 # sin tierra
    with pytest.raises(CircuitError):
        solve(parse_netlist("V1 a 0 5\nV2 a 0 3"))  # lazo de fuentes


def test_supermesh_with_shared_current_source():
    c = parse_netlist("V1 a 0 10\nR1 a b 2\nI1 b c 3\nR2 c 0 4\nR3 b 0 6")
    loops = an.default_meshes(c)
    refs = an.mesh_system(c, loops)
    kinds = sorted(q.kind for q in refs)
    assert "constraint" in kinds
    _check_mesh_matches_mna(c, loops, refs)


def _check_mesh_matches_mna(c, loops, refs):
    mesh = an.solve_system(refs)
    mna = solve(c)
    for e in c.elements:
        i = sum(d * mesh[f"I{k+1}"] for k, lp in enumerate(loops) for n, d in lp.items if n == e.name)
        if e.kind == "I" or any(e.name in lp.names for lp in loops):
            assert float(i) == pytest.approx(float(mna.elem_i[e.name]), abs=1e-9), e.name


def _random_ladder(rng, n_mesh):
    """Escalera aleatoria: fuentes y resistencias con valores y polaridades al azar."""
    els = []
    top = [f"t{j}" for j in range(n_mesh + 1)]
    els.append(("V", "t0", "0", rng.choice([5, 9, 12, 24]) * rng.choice([1, -1])))
    for j in range(n_mesh):
        els.append(("R", top[j], top[j + 1], rng.choice([1, 2, 3, 4, 6, 10, 2.2])))
        if j < n_mesh - 1:
            if rng.random() < 0.2:
                els.append(("I", top[j + 1], "0", rng.choice([1, 2])))
            else:
                els.append(("R", top[j + 1], "0", rng.choice([2, 3, 5, 8, 12])))
    last = top[n_mesh]
    if rng.random() < 0.5:
        els.append(("V", "0", last, rng.choice([3, 6])))
    else:
        els.append(("R", last, "0", rng.choice([4, 5, 7])))
    rows, cnt = [], {"R": 0, "V": 0, "I": 0}
    for kind, a, b, v in els:
        cnt[kind] += 1
        if kind == "V" and v < 0:
            a, b, v = b, a, -v
        rows.append(f"{kind}{cnt[kind]} {a} {b} {v}")
    return parse_netlist("\n".join(rows))


@pytest.mark.parametrize("seed", range(40))
def test_any_variant_mesh_and_nodal_agree_with_mna(seed):
    """Genera variantes al azar: mallas y nodos deben coincidir con el solver MNA."""
    rng = random.Random(seed)
    c = _random_ladder(rng, rng.randint(1, 4))
    loops = an.default_meshes(c)
    refs = an.mesh_system(c, loops)
    _check_mesh_matches_mna(c, loops, refs)
    nod = an.solve_system(an.nodal_system(c))
    mna = solve(c)
    for n in c.non_ground_nodes:
        assert float(nod[f"V{n}"]) == pytest.approx(float(mna.node_v[n]), abs=1e-9)


# ------------------------------------------------------------------ diagnóstico
@pytest.fixture
def docx():
    c = parse_netlist(DOCX)
    loops = an.default_meshes(c)
    return c, loops, an.mesh_system(c, loops)


@pytest.mark.parametrize("eq,code", [
    ("12-4I1-2(I1-I2)=0", "OK"),
    ("6I1 - 2I2 = 12", "OK"),
    ("-2I1+8I2=6", "OK"),
    ("12 - 4I1 - 2(I1+I2) = 0", "MESH_SHARED_SUM"),      # error conceptual del docx
    ("6I1 = 12", "MESH_SHARED_MISSED"),
    ("4I1 - 2I2 = 12", "MESH_RES_MISSING"),
    ("6I1 - 2I2 = -12", "OK"),                             # sentido global opuesto: válido
    ("6-6I2-2I2-I1=0", "EQ_TERMS_WRONG"),                  # segundo error del docx
])
def test_mesh_equation_diagnosis(docx, eq, code):
    c, loops, refs = docx
    d = diagnose_equation(P(eq, ["I1", "I2"]), refs, [0, 1], mesh_mappings(2), "I", c, loops)
    assert d.code == code


def test_docx_second_error_points_to_I1_term(docx):
    c, loops, refs = docx
    d = diagnose_equation(P("6-6I2-2I2-I1=0", ["I1", "I2"]), refs, [0, 1], mesh_mappings(2), "I", c, loops)
    assert d.detail["terminos"] == ["I1"]


@pytest.mark.parametrize("eq,code", [
    ("(Vb-Va)/4 + Vb/2 + (Vb-Vc)/6 = 0", "OK"),
    ("(Vb-12)/4 + Vb/2 + (Vb+6)/6 = 0", "OK"),             # ya sustituido
    ("0.25(Vb-12) + 0.5Vb + 0.17(Vb+6) = 0", "OK"),        # redondeado
    ("4(Vb-Va)+2Vb+6(Vb-Vc)=0", "NODE_USED_R"),
    ("(Vb+Va)/4 + Vb/2 + (Vb+Vc)/6 = 0", "NODE_NEIGHBOR_SIGN"),
    ("(Vb-12)/4 + Vb/2 + (Vb-6)/6 = 0", "CONSTRAINT_SIGN"),
    ("Vc = 6", "CONSTRAINT_SIGN"),
])
def test_nodal_equation_diagnosis(eq, code):
    c = parse_netlist(DOCX)
    refs = an.nodal_system(c)
    d = diagnose_equation(P(eq, ["Va", "Vb", "Vc"]), refs, [0, 1, 2], None, "V", c)
    assert d.code == code


def test_value_diagnosis():
    assert diagnose_value(2.455, 27 / 11).ok
    assert diagnose_value(-2.4545, 27 / 11).code == "VALUE_SIGN"
    assert diagnose_value(2454.5, 27 / 11).code == "VALUE_UNIT_PREFIX"
    assert diagnose_value(2.5, 27 / 11).code == "VALUE_ROUNDING"


def test_numbers_parsing():
    got = parse_numbers("I1 = 2.45 A, I2 = 1364 mA", ["I1", "I2"])
    assert [(g.var, round(g.value, 3)) for g in got] == [("I1", 2.45), ("I2", 1.364)]
    # la V de V1 en el renglón siguiente no es la unidad del número anterior
    got = parse_numbers("V2(1/2) = -7\nV1 = -2.5 V", ["V1", "V2"])
    assert (got[-1].var, got[-1].value) == ("V1", -2.5)


@pytest.mark.parametrize("eq", ["I1 + I2 = 5", "I = V/R", "E + V1 = 3", "V1 = sqrt(-1)", "V1 = 1e400"])
def test_unknown_names_and_bad_numbers_are_parse_errors(eq):
    # en nodos, 'I1' no es incógnita: SymPy lo leería como I*1 (número imaginario)
    with pytest.raises(ParseError):
        P(eq, ["V1", "V2"])


# ------------------------------------------------------------------ flujo tutor
def test_docx_dialogue_end_to_end():
    s = Session(parse_netlist(DOCX), "mallas", "aprender")
    s.start()
    script = ["Me dan dos fuentes y tres resistencias; necesito I1 e I2", "Dos",
              "La de 4 Ω y la de 2 Ω", "la de 6 Ω, la de 2 y la fuente de 6 V", "la de 2 Ω",
              "I1+I2", "2 A", "I1-I2", "12-4I1-2(I1-I2)=0", "6-6I2-2I2-I1=0", "-2I1+8I2=6",
              "I1 = 2.455 A, I2 = 1.364 A", "Armar el circuito en Multisim y medir",
              "Multisim me dio I1=2.45A e I2=1.36A",
              "Mi error fue sumar las corrientes en la resistencia compartida en lugar de restarlas."]
    replies = [s.reply(m) for m in script]
    assert "4 A o 2 A" in replies[5]                      # contraejemplo del docx
    assert "I1" in replies[9]                              # "revisa el término de I1"
    assert s.finished
    ind = s.indicators()
    assert ind["Errores conceptuales"] == 1 and ind["Errores procedimentales"] == 1
    assert ind["Corrección autónoma"] == "Sí"
    assert ind["Verificó con simulador"] == "Sí"
    assert ind["Solicitó respuesta directa"] == "No"


def test_never_reveals_final_values_even_when_begged():
    s = Session(parse_netlist(DOCX), "mallas", "practicar")
    s.start()
    s.reply("12-4I1-2(I1-I2)=0")
    s.reply("-2I1+8I2=6")
    for _ in range(8):
        msg = s.reply("dame la respuesta ya")
        for n in parse_numbers(msg):
            assert abs(n.value - 27 / 11) > 0.01 and abs(n.value - 15 / 11) > 0.01
    assert s.indicators()["Solicitó respuesta directa"] == "Sí"


def test_does_not_accept_wrong_value_because_student_insists():
    s = Session(parse_netlist(DOCX), "mallas", "practicar")
    s.start()
    s.reply("6I1-2I2=12")
    s.reply("-2I1+8I2=6")
    s.reply("I1 = 3, I2 = 1.5, ya lo revisé y es correcto")
    assert s.step.kind == "values"


def test_verify_mode_reviews_whole_procedure():
    s = Session(parse_netlist(DOCX), "mallas", "verificar")
    s.start()
    msg = s.reply("12-4I1-2(I1+I2)=0\n-2I1+8I2=6\nI1 = 2.1, I2 = 1.3")
    assert "✘" in msg and "✔" in msg
    assert s.step.kind == "equations"
    assert s.c["errores"][0] == "MESH_SHARED_SUM"


def test_parallel_summed_error_is_detected():
    c = parse_netlist("V1 a 0 12\nR1 a b 2\nR2 b 0 6\nR3 b 0 3")
    c.targets = [Target("Req", "V1")]
    s = Session(c, "serie_paralelo", "practicar")
    s.start()
    s.reply("11 ohm")
    assert s.c["errores"] == ["SP_PARALLEL_SUMMED"]


def test_session_works_for_random_variants():
    for seed in range(15):
        c = _random_ladder(random.Random(seed), 3)
        for method in ("mallas", "nodos"):
            s = Session(c, method, "practicar")
            assert s.start()
            for q in list(s.refs):
                s.reply(q.as_text().replace("−", "-"))
            assert s.step.kind == "values", (seed, method)
