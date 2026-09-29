"""Flujos del tutor con los problemas del banco y la notación de clase."""
import pytest

from tutor.circuit import parse_netlist
from tutor.engine import MODES, Session
from tutor.problems import load_bank
from tutor.reduction import Reducer, display

BANK = {p.id: p for p in load_bank()}


def bank(prefix):
    return next(p for pid, p in BANK.items() if pid.startswith(prefix))


def session(prefix, mode):
    p = bank(prefix)
    s = Session(p.circuit, p.method, mode)
    s.start()
    return s


def test_caso1_full_flow_with_table_and_power_balance():
    s = session("mallas-01", "aprender")
    script = [
        "Me dan dos fuentes de 100 V y tres resistencias, necesito V, I y P de cada una",
        "dos", "VX, R1, R2 y VY", "VY, R2 y R3", "R2 y VY",
        "I1 + I2", "2 A", "I1 - I2", "porque las corrientes van en sentidos opuestos en esa rama",
        "I1(R1 + R2) - I2R2 = VX - VY", "50I2 - 20I1 = 100 V",
        "I1 = 1.82 A, I2 = 2.73 A",
        "R1: I = 1.82 A, V = 18.2 V, P = 33.1 W\nR2: I1 + I2 = 4.55 A, 91 V, 414 W",
        "R2: I1 - I2 = 0.91 A, 18.2 V, 16.6 W\nR3: 2.73 A, 81.9 V, 223.6 W",
        "con el balance de potencias", "PE = 273.3 W y PC = 273.2 W",
        "Me equivoqué al sumar las corrientes de la resistencia compartida, ahora sé que se restan",
    ]
    replies = [s.reply(m) for m in script]
    assert "4 A o 2 A" in replies[5]
    assert "¿por qué es así?" in replies[7]
    assert "Ahora intenta las ecuaciones" in replies[8]
    assert s.finished
    ind = s.indicators()
    assert ind["Verificó con balance de potencias"] == "Sí"
    assert ind["Justificaciones dadas"] == 1
    assert ind["Códigos de error"] == "MESH_SHARED_SUM,MESH_SHARED_SUM"


def test_symbolic_equation_in_class_format_is_accepted():
    s = session("mallas-01", "practicar")
    assert s.reply("I1(R1 + R2) - I2R2 = VX - VY").startswith("Correcto")
    assert s.reply("I2(R2 + R3) - I1R2 = VY").startswith("Correcto")
    assert s.step.kind == "values"


def test_ccw_convention_is_accepted_for_values():
    # todas las corrientes en sentido antihorario (CMR): I' = −I
    s = session("mallas-01", "practicar")
    assert s.reply("-30I1 + 20I2 = 0").startswith("Correcto")
    assert s.reply("20I1 - 50I2 = 100").startswith("Correcto")
    s.reply("I1 = -1.82 A, I2 = -2.73 A")
    assert s.step.kind == "table"


def test_mesh_types_step_detects_wrong_classification():
    s = session("mallas-02", "aprender")
    for m in ["Una fuente de corriente de 2 A y una de voltaje de 50 V", "3",
              "R1, IX y R3", "R2, R4 y IX", "R3, R4 y VX"]:
        s.reply(m)
    assert s.step.kind == "mesh_types"
    s.reply("la 1 es fantasma, la 2 real y la 3 real")
    assert s.c["errores"][-1] == "MESH_TYPE_WRONG"
    s.reply("1 y 2 forman una supermalla y la 3 es real")
    assert s.step.kind != "mesh_types" or s.justify_pending


def test_supermesh_equations_follow_class_order():
    s = session("mallas-02", "practicar")
    assert "ecuación auxiliar" in s._step_prompt(s.step)
    assert s.reply("I2 - I1 = 2").startswith("Correcto")
    assert "ecuación real de la supermalla" in s._step_prompt(s.step)
    assert s.reply("40I1 + 60I2 - 70I3 = 0 V").startswith("Correcto")
    assert s.reply("70I3 - 30I1 - 40I2 = -50 V").startswith("Correcto")


def test_node_types_and_direct_equation():
    s = session("nodos-03", "aprender")
    for m in ["10 V, 31 V, 5 A y seis resistencias", "4", "1 fantasma, 2 y 3 supernodo, 5 real"]:
        s.reply(m)
    assert s.step.kind == "equations"
    assert "ecuación directa" in s._step_prompt(s.step)
    assert s.reply("V1 = 10").startswith("Correcto")


def test_mesh_currents_in_nodal_equation_are_reported():
    s = session("nodos-01", "practicar")
    msg = s.reply("I1 + I2 = 5")
    assert "No reconozco: I1, I2. Usa V1, V2" in msg
    assert "V1(1/4 + 1/2) − V2(1/2) = 5" in msg      # el ejemplo usa las incógnitas del problema
    assert s.step.kind == "equations"


def test_shared_branch_asked_once():
    s = session("mallas-01", "aprender")
    st = next(x for x in s.steps if x.kind == "shared_expr")
    assert st.items == ["R2"]


# ------------------------------------------------------------ serie/paralelo
def test_reduction_accepts_intermediate_steps_like_in_class():
    s = session("serie-03", "practicar")
    assert s.reply("R2 y R3 en paralelo = 20 ohm").startswith("Correcto")
    assert s.c["errores"] == []
    s.reply("RT1 en serie con R4 = 40")               # la pista equivocada de la diapositiva
    assert s.c["errores"] == ["SP_PARALLEL_SUMMED"]
    s.reply("R4 en paralelo con RT1 = 10")
    s.reply("R1 + RT2 = 20")
    assert s.step.kind == "table"


@pytest.mark.parametrize("msg,code", [
    ("R1 y R2 en serie = 40", "SP_NOT_COMBINABLE"),
    ("R2 y R3 en serie = 90", "SP_PARALLEL_SUMMED"),
    ("11", "SP_PARALLEL_SUMMED"),
])
def test_reduction_diagnoses(msg, code):
    c = parse_netlist("V1 a 0 12\nR1 a b 10\nR2 b 0 30\nR3 b 0 60")
    r = Reducer(c, "a", "0", exclude="V1")
    if msg == "11":
        c = parse_netlist("V1 a 0 12\nR1 a b 2\nR2 b 0 6\nR3 b 0 3")
        r = Reducer(c, "a", "0", exclude="V1")
    assert r.check(msg).code == code


def test_shorted_resistor_is_recognized():
    c = parse_netlist("V1 a 0 120\nR1 a b 100\nR2 b 0 50\nR3 b 0 50\nR5 b b 100")
    r = Reducer(c, "a", "0", exclude="V1")
    assert "en corto" in r.check("R5 está en corto").message
    assert r.check("125").ok


@pytest.mark.parametrize("spec", [
    "MALLA 1: V1 R1\nMALLA 2: R2 R3 V2",          # no cierra
    "MALLA 1: V1 R1 R2",                           # faltan mallas
    "MALLA 1: V1 R1 R2\nMALLA 2: V1 R1 R2",        # repetida (no independiente)
])
def test_mesh_spec_is_validated(spec):
    from tutor import analysis as an
    from tutor.circuit import CircuitError
    c = parse_netlist("V1 a 0 12\nR1 a b 4\nR2 b 0 2\nR3 b c 6\nV2 0 c 6\n" + spec)
    with pytest.raises(CircuitError):
        an.default_meshes(c)


# ------------------------------------------------------------ banco completo
def g(x):
    return f"{float(x):.6g}"


def correct_answer(s):
    """Lo que contesta, en el paso actual, un alumno que hace todo bien."""
    st, k = s.step, s.step.kind
    truth = s._values_truth()

    def eq(i):
        q = s.refs[i]
        return " + ".join(f"({g(c)})*{v}" for v, c in q.coefs.items() if float(c)) + f" = {g(q.rhs)}"

    def vals(names):
        return ", ".join(f"{n} = {g(truth[n])}" for n in names)

    def row(name):
        i, v, p = s._branch_values(name)
        return f"{name}: I = {g(i)} A, V = {g(v)} V, P = {g(p)} W"

    if s.justify_pending is not None:
        return "porque las corrientes van en sentidos opuestos en esa rama"
    if k == "submit":
        units = {"I": "A", "V": "V", "P": "W"}
        parts = [eq(i) for i in range(len(s.refs))] + ([vals(s.vars)] if s.vars else [])
        parts += [f"Req = {g(s.reducer.req)} ohm"] if s.reducer else []
        parts += [f"{t.quantity} = {g(abs(v))} {units.get(t.quantity, '')}"
                  for t, v in zip(s.targets, s.target_truth)]
        return "\n".join(parts)
    if k == "reduce":
        mv = s.reducer.moves()[0]
        names = [display(n) for n in mv.members]
        return f"{', '.join(names[:-1])} y {names[-1]} en {mv.kind} = {g(mv.value)}"
    if k == "verify":
        pc = s._power_totals()
        return f"PE = {g(pc)} W y PC = {g(pc)} W" if st.data.get("balance") else "con el balance de potencias"
    if k == "sim":
        if s.vars:
            return vals(s.vars)
        if s.targets:
            return ", ".join(g(v) for v in s.target_truth)
        return f"En el simulador medí {g(s._branch_values(s.table_rows[0])[0])} A en {s.table_rows[0]}"
    return {
        "intro": lambda: "Me dan las fuentes y las resistencias y necesito las corrientes y voltajes",
        "mesh_count": lambda: str(len(s.loops)),
        "node_count": lambda: str(len(s.circuit.non_ground_nodes)),
        "mesh_define": lambda: ", ".join(s.loops[st.pending[0]].names),
        "mesh_types": lambda: ", ".join(f"{i + 1} {s.types[i]}" for i in st.pending),
        "node_types": lambda: ", ".join(f"{i} {s.types[i]}" for i in st.pending),
        "shared_id": lambda: ", ".join(st.data["shared"]),
        "shared_expr": lambda: "I{} - I{}".format(*(m + 1 for m in st.data["shared"][st.pending[0]])),
        "equations": lambda: eq(st.pending[0]),
        "values": lambda: vals(st.pending),
        "concept": lambda: "comparten el mismo voltaje; en serie la misma corriente; ley de ohm V = R*I",
        "targets": lambda: g(s.target_truth[st.pending[0]]),
        "table": lambda: "\n".join(row(n) for n in st.pending),
        "metacog": lambda: "Aprendí a revisar las ramas compartidas y las fuentes antes de cada ecuación",
    }[k]()


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("pid", sorted(BANK))
def test_every_problem_can_be_finished_in_every_mode(pid, mode):
    p = BANK[pid]
    s = Session(p.circuit, p.method, mode)
    s.start()
    for _ in range(60):
        if s.finished:
            break
        s.reply(correct_answer(s))
    assert s.finished
    assert s.indicators()["Códigos de error"] == ""


def test_verify_mode_reviews_results_without_equations():
    s = session("ohm-01", "verificar")
    msg = s.reply("I = V/R = 20/10 = 2 A, P = V*I = 40 W")
    assert "✔ la corriente en R1" in msg and "✔ la potencia en R1" in msg
    assert s.step.kind == "verify"


@pytest.mark.parametrize("msg, ok", [
    ("Con el balance de potencias: PE = 20*2 = 40 W es igual a la consumida PC = 40 W", True),
    ("PE = PC = 40 W", True),
    ("PE = 40 W y PC = 40 W", True),
    ("PE = 40 W y PC = 35 W", False),
    ("PE = 40, PC = 35", False),
])
def test_power_balance_reads_the_powers_not_the_factors(msg, ok):
    s = session("ohm-01", "verificar")
    s.reply("I = V/R = 20/10 = 2 A, P = V*I = 40 W")
    s.reply(msg)
    assert s.indicators()["Verificó con balance de potencias"] == ("Sí" if ok else "No")


def test_power_balance_asks_for_the_other_power():
    s = session("ohm-01", "verificar")
    s.reply("I = V/R = 20/10 = 2 A, P = V*I = 40 W")
    assert "la otra potencia" in s.reply("PE = 20*2 = 40")


def test_verify_mode_does_not_judge_results_as_equations():
    s = session("mallas-01", "verificar")
    msg = s.reply("30I1 - 20I2 = 0\n50I2 - 20I1 = 100\nI1 = 1.81818, I2 = 2.72727")
    assert "✘" not in msg
    s = session("nodos-01", "verificar")
    msg = s.reply("V1(1/4+1/2) - V2(1/2) = 5\nV2(1/2+1/10) - V1(1/2) = -7\nV1 = -2.5 V, V2 = -13.75 V")
    assert "✔ V1 = -2.5" in msg and "✔ V2 = -13.75" in msg
