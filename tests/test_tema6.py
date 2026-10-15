"""Divisor de voltaje y corriente, y superposición: solver, diálogos con errores típicos,
guardia de resultados pendientes, confianza y reanudación."""
import pytest

from escritorio.api import PASOS_ANOTADOS, TOPICS, _describe
from escritorio.config import PASOS_CON_CONFIANZA
from tutor.circuit import CircuitError, Target, parse_netlist
from tutor.engine import CONFIANZA_KINDS, MODES, Session, reconstruir
from tutor.errorbank import ErrorBank
from tutor.llm import guard
from tutor.problems import DEFAULT_DIR as RAIZ_PROBLEMAS, load_bank, parse_problem
from tutor.solver import apagar_fuentes, solve
from tests.test_flow import correct_answer

BANK = {p.id: p for p in load_bank()}


def banco(prefijo):
    return next(p for pid, p in BANK.items() if pid.startswith(prefijo))


INTRO = "Me dan las fuentes y las resistencias y necesito los voltajes y corrientes"


def sesion(pid, modo="aprender"):
    p = banco(pid)
    s = Session(p.circuit, p.method, modo)
    s.start()
    return s


def propio(texto, modo="aprender"):
    p = parse_problem(texto, "propio")
    s = Session(p.circuit, p.method, modo)
    s.start()
    return s


def dialogo(s, mensajes):
    return [s.reply(m) for m in mensajes]


def codigos(s):
    return s.c["errores"]


# ====================================================================== solver
CIRCUITO = parse_netlist("V1 a 0 10\nR1 a b 2\nR2 b 0 3\nI1 0 b 1")


def test_apagar_fuentes_deja_encendida_sola_la_activa():
    solo_v = apagar_fuentes(CIRCUITO, "V1")
    assert solo_v.get("I1").value == 0 and solo_v.get("V1").value == 10
    solo_i = apagar_fuentes(CIRCUITO, "I1")
    assert solo_i.get("V1").value == 0 and solo_i.get("I1").value == 1
    ninguna = apagar_fuentes(CIRCUITO, None)
    assert ninguna.get("V1").value == 0 and ninguna.get("I1").value == 0
    assert CIRCUITO.get("V1").value == 10 and CIRCUITO.get("I1").value == 1      # no modifica el original


def test_apagar_fuentes_valores_hechos_a_mano():
    # sólo V1: serie de 2 + 3 Ω con 10 V -> 2 A; sólo I1: 1 A en R2 ∥ (R1 + V1 en corto)
    v = solve(apagar_fuentes(CIRCUITO, "V1"))
    assert v.elem_i["R1"] == 2 and v.node_v["b"] == 6
    i = solve(apagar_fuentes(CIRCUITO, "I1"))
    assert i.node_v["b"] == pytest.approx(6 / 5)          # 1 A en 2 Ω ∥ 3 Ω = 6/5 Ω
    assert i.elem_i["R1"] == pytest.approx(-3 / 5)         # la corriente de I1 sale por b hacia a y 0


def test_apagar_fuentes_sin_fuentes_no_hay_voltajes():
    s = solve(apagar_fuentes(CIRCUITO, None))
    assert all(v == 0 for v in s.node_v.values())


def test_superposicion_suma_de_los_casos_es_el_circuito_completo():
    total = solve(CIRCUITO)
    parcial = [solve(apagar_fuentes(CIRCUITO, n)) for n in ("V1", "I1")]
    for e in CIRCUITO.by_kind("R"):
        assert sum(p.elem_i[e.name] for p in parcial) == total.elem_i[e.name]


def test_apagar_fuentes_rechaza_lo_que_no_es_una_fuente():
    with pytest.raises(CircuitError):
        apagar_fuentes(CIRCUITO, "R1")
    with pytest.raises(CircuitError):
        apagar_fuentes(CIRCUITO, "V9")


# ===================================================================== divisor
def test_divisor_de_voltaje_con_errores_tipicos():
    s = sesion("divisor-01")
    m = dialogo(s, [INTRO, "la misma corriente", "5.45 V"])
    assert codigos(s) == ["DIV_V_RT_INCORRECTA"]
    assert s.step.id == "targets" and s.level == 1
    assert "voltaje" in m[-1] and "serie" in m[-1]
    dialogo(s, ["3.75 V", "2.25 V", "6 V"])
    assert s.step.kind == "verify"
    assert codigos(s) == ["DIV_V_RT_INCORRECTA"]


@pytest.mark.parametrize("valor", ["7.5 V", "4.62 V"])
def test_divisor_de_voltaje_detecta_otras_resistencias_en_el_denominador(valor):
    s = sesion("divisor-01", "practicar")
    s.reply(valor)
    assert codigos(s) == ["DIV_V_RT_INCORRECTA"]


def test_divisor_de_corriente_de_dos_ramas_con_su_propia_resistencia():
    s = sesion("divisor-02")
    dialogo(s, [INTRO, "comparten el mismo voltaje", "1 A"])
    assert codigos(s) == ["DIV_I_R_PROPIA"]
    dialogo(s, ["4 A", "1 A"])
    assert s.step.kind == "verify"


def test_divisor_de_corriente_con_n_ramas_sin_la_equivalente():
    s = propio("METODO: divisor\nPIDE: I R1\n\nIT 0 a 12\nR1 a 0 2\nR2 a 0 3\nR3 a 0 6", "practicar")
    s.reply("66 A")                       # IT · (R1 + R2 + R3)/R1
    assert codigos(s) == ["DIV_I_SIN_REQ"]
    s.reply("6 A")
    assert s.step.kind == "verify"


def test_cada_mutacion_del_divisor_sólo_aparece_donde_tiene_sentido():
    serie = sesion("divisor-01")
    dos_ramas = sesion("divisor-02")
    tres_ramas = propio("METODO: divisor\nPIDE: I R1\n\nIT 0 a 12\nR1 a 0 2\nR2 a 0 3\nR3 a 0 6")
    en_serie = serie._target_mutations(serie.targets[0])
    assert "DIV_V_RT_INCORRECTA" in en_serie
    assert not {"DIV_I_R_PROPIA", "DIV_I_SIN_REQ"} & set(en_serie)
    en_dos = dos_ramas._target_mutations(dos_ramas.targets[0])
    assert "DIV_I_R_PROPIA" in en_dos
    assert not {"DIV_I_SIN_REQ", "DIV_V_RT_INCORRECTA"} & set(en_dos)
    en_tres = tres_ramas._target_mutations(tres_ramas.targets[0])
    assert "DIV_I_SIN_REQ" in en_tres
    assert not {"DIV_I_R_PROPIA", "DIV_V_RT_INCORRECTA"} & set(en_tres)


def test_divisor_sin_pedido_calcula_todas_las_resistencias():
    s = propio("METODO: divisor\n\nVT a 0 12\nR1 a b 5\nR2 b 0 7", "practicar")
    assert [t.describe() for t in s.targets] == ["el voltaje en R1", "el voltaje en R2"]


def test_divisor_rechaza_un_circuito_que_no_es_divisor():
    with pytest.raises(CircuitError):
        propio("METODO: divisor\n\nV1 a 0 12\nR1 a b 4\nR2 b 0 4\nR3 b 0 4")


@pytest.mark.parametrize("texto", [
    "sumo los voltajes de las resistencias y debe dar VT",
    "la suma de las corrientes de las ramas es It",
])
def test_divisor_verifica_que_las_partes_sumen_el_total(texto):
    s = sesion("divisor-01", "practicar")
    dialogo(s, ["3.75 V", "2.25 V", "6 V"])
    assert s.step.kind == "verify"
    assert "las partes deben sumar el total" in s.reply(texto)
    assert s.step.kind == "metacog"


def test_divisor_pide_una_comprobacion_concreta():
    s = sesion("divisor-01", "practicar")
    dialogo(s, ["3.75 V", "2.25 V", "6 V"])
    s.reply("lo reviso")
    assert codigos(s)[-1] == "VERIFY_VAGUE"


def test_divisor_de_voltaje_nivel_cinco_da_la_formula_sin_el_resultado():
    s = sesion("divisor-01", "practicar")
    for _ in range(5):
        s.reply("9 V")
    texto = s.ultimo_turno["mensaje_base"]
    assert "VT = 12 V" in texto and "5 + 3 + 8" in texto and "3.75" not in texto


# ============================================================== superposición
def apagar_ok(s):
    """Respuesta correcta al paso sup_apagar actual."""
    return correct_answer(s)


def test_superposicion_dos_fuentes_de_voltaje_con_sentidos_opuestos():
    s = sesion("superposicion-02")
    m = dialogo(s, [INTRO, "3", "2"])
    assert codigos(s) == ["TARGET_WRONG"] and s.step.id == "sup_apagar_1"
    # V1 está encendida: V2 es la que se apaga y debe quedar en corto
    m = dialogo(s, ["se deja abierta", "la pongo en cortocircuito",
                    "porque una fuente de voltaje apagada vale cero volts"])
    assert codigos(s)[-1] == "SUP_V_ABIERTA" and "{" not in m[0]
    assert s.c["justificaciones"] == 1 and s.step.id == "sup_parcial_1"
    # abrir V2 daría -1.5 A; el valor correcto es 0.5 A y el signo da el sentido respecto a R2
    dialogo(s, ["-1.5 A"])
    assert codigos(s)[-1] == "SUP_V_ABIERTA"
    s.reply("0.5 A")
    assert s.sup_sentidos[(0, 0)] == "a favor" and s.step.id == "sup_apagar_2"
    dialogo(s, ["en corto", "2.667 A"])
    assert s.sup_sentidos[(1, 0)] == "a favor" and s.step.id == "sup_suma"
    # las dos corrientes van en sentidos opuestos en R2: sumar las magnitudes es el error de signo
    s.reply("3.17 A")
    assert codigos(s)[-1] == "SUP_SIGNO"
    s.reply("2.667 A")
    assert codigos(s)[-1] == "SUP_FALTA_FUENTE"
    s.reply("2.17 A")
    assert s.step.kind == "verify"


def test_superposicion_apaga_la_fuente_de_corriente_en_corto():
    s = sesion("superposicion-01")
    dialogo(s, [INTRO, "2"])
    s.reply("se pone en corto")                    # I1 debe quedar abierta
    assert codigos(s) == ["SUP_I_EN_CORTO"]
    dialogo(s, ["queda abierta", "porque no deja pasar corriente alguna"])
    assert s.step.id == "sup_parcial_1"
    s.reply("0 A")                                 # el valor que sale al poner I1 en corto
    assert codigos(s)[-1] == "SUP_I_EN_CORTO"
    s.reply("2 A")
    assert s.step.id == "sup_apagar_2"


def test_superposicion_la_fuente_de_voltaje_abierta_da_el_valor_de_la_otra_fuente():
    s = sesion("superposicion-01", "practicar")
    s.reply("2 A")
    assert s.step.id == "sup_parcial_2"
    s.reply("9 A")                                 # V1 abierta: toda la corriente de I1 pasa por R2
    assert codigos(s) == ["SUP_V_ABIERTA"]
    s.reply("6 A")
    s.reply("4 A")                                 # 6 − 2: resta casos que van en el mismo sentido
    assert codigos(s)[-1] == "SUP_SIGNO"
    s.reply("6 A")
    assert codigos(s)[-1] == "SUP_FALTA_FUENTE"
    s.reply("8 A")
    assert s.step.kind == "verify"


def test_superposicion_con_dos_magnitudes_y_un_caso_parcial_nulo():
    s = sesion("superposicion-03", "practicar")
    assert [t.describe() for t in s.targets] == ["el voltaje en R1", "la corriente en R1"]
    # una sola magnitud por mensaje, o las dos juntas
    s.reply("5 V")
    assert s.step.id == "sup_parcial_1" and s.step.pending == [1]
    s.reply("1 A")
    assert s.step.id == "sup_parcial_2" and s.sup_parcial[1] == [0.0, 0.0]
    s.reply("V = 0 V, I = 0 A")
    assert s.step.id == "sup_suma"
    s.reply("5 V")
    s.reply("5 V")                                 # la corriente se pidió en A, pero el valor es otro
    assert codigos(s) == ["TARGET_WRONG"]
    s.reply("I = 1 A")
    assert s.step.kind == "verify" and codigos(s) == ["TARGET_WRONG"]


def test_superposicion_un_caso_nulo_mal_dado_se_marca():
    s = sesion("superposicion-03", "practicar")
    s.reply("V = 5 V, I = 1 A")
    s.reply("V = 5 V, I = 1 A")                    # con I apagada en abierto, VX no cambia; IX vale 0
    assert codigos(s) == ["TARGET_WRONG"]


def test_superposicion_con_fuentes_mixtas_pide_decir_que_se_hace_con_cada_tipo():
    s = propio("METODO: superposicion\nPIDE: I R3\n\nV1 a 0 10\nR1 a b 2\nV2 b c 4\nR2 c 0 3\n"
               "I1 0 b 1\nR3 b 0 5")
    dialogo(s, [INTRO, "3"])
    assert s.step.id == "sup_apagar_1"
    m = s.reply("las pongo en corto")
    assert "Dime también" in m or "Dime" in m
    assert codigos(s) == [] and s.step.id == "sup_apagar_1"      # sin penalización
    s.reply("la fuente de voltaje en corto y la de corriente en corto")
    assert codigos(s) == ["SUP_I_EN_CORTO"]
    s.reply("la fuente de voltaje en corto y la de corriente abierta")
    assert s.step.id == "sup_parcial_1" or s.justify_pending


def test_superposicion_rechaza_potencia_y_una_sola_fuente():
    with pytest.raises(CircuitError):
        propio("METODO: superposicion\nPIDE: P R2\n\nV1 a 0 10\nR1 a b 2\nR2 b 0 3\nI1 0 b 1")
    with pytest.raises(CircuitError):
        propio("METODO: superposicion\nPIDE: VIP\n\nV1 a 0 10\nR1 a b 2\nR2 b 0 3\nI1 0 b 1")
    with pytest.raises(CircuitError):
        propio("METODO: superposicion\nPIDE: I R2\n\nV1 a 0 10\nR1 a b 2\nR2 b 0 3")
    with pytest.raises(CircuitError):
        propio("METODO: superposicion\n\nV1 a 0 10\nR1 a b 2\nR2 b 0 3\nI1 0 b 1")


def test_superposicion_verificar_reconoce_los_resultados_de_un_solo_envio():
    s = sesion("superposicion-02", "verificar")
    m = s.reply("Caso V1: 0.5 A; caso V2: 2.667 A; total I R2 = 2.17 A")
    assert "con sólo V1" in m and "con sólo V2" in m and "con todas las fuentes" in m
    assert s.step.kind == "verify"


def test_superposicion_verificar_deja_pendiente_lo_que_no_trae():
    s = sesion("superposicion-02", "verificar")
    m = s.reply("En total 2.17 A")
    assert "con todas las fuentes" in m and s.step.id == "sup_parcial_1"


def test_superposicion_no_acepta_el_balance_de_potencias_de_los_casos():
    s = sesion("superposicion-02", "practicar")
    dialogo(s, ["0.5 A", "2.667 A", "2.17 A"])
    assert s.step.kind == "verify"
    m = s.reply("hago el balance de potencias de cada caso")
    assert "La potencia no se superpone" in m and s.step.kind == "verify"
    assert not s.indicators()["Verificó con balance de potencias"] == "Sí" and codigos(s) == []
    s.reply("con mallas, con todas las fuentes encendidas")
    assert s.step.kind == "metacog"


@pytest.mark.parametrize("pid, texto", [
    ("superposicion-02", "no la dejo abierta, la pongo en corto"),
    ("superposicion-02", "en corto, porque no tiene voltaje"),
    ("superposicion-01", "la fuente de corriente no se pone en corto, se abre"),
    ("superposicion-01", "no va en corto sino abierta"),
])
def test_superposicion_una_negacion_no_cuenta_como_error(pid, texto):
    s = sesion(pid)
    dialogo(s, [INTRO, "2"])
    s.reply(texto)
    assert codigos(s) == [] and s.step.id == "sup_parcial_1"


@pytest.mark.parametrize("pid, texto", [
    ("superposicion-01", "no se pone en corto, se abre"),
    ("superposicion-02", "no se deja abierta, se pone en corto"),
])
def test_una_respuesta_que_empieza_con_no_se_no_es_pedir_ayuda(pid, texto):
    s = sesion(pid)
    dialogo(s, [INTRO, "2"])
    s.reply(texto)
    assert codigos(s) == [] and s.step.id == "sup_parcial_1"


@pytest.mark.parametrize("texto, ayuda", [
    ("no se", True), ("no sé", True), ("no se.", True), ("no se como hacerlo", True), ("no se que hacer", True),
    ("no sé si va en serie", True), ("No se, ¿me ayudas?", True),
    ("no se pone en corto, se abre", False), ("no se deja abierta", False), ("no se toma en cuenta R1", False),
])
def test_no_se_sin_acento_sólo_es_ayuda_cuando_expresa_duda(texto, ayuda):
    from tutor.engine import HELP_RE
    assert bool(HELP_RE.search(texto)) is ayuda


@pytest.mark.parametrize("texto", ["quito las otras fuentes", "la apago", "no la dejo abierta"])
def test_superposicion_apagar_sin_decir_con_que_pide_precisar_sin_penalizar(texto):
    s = sesion("superposicion-02")
    dialogo(s, [INTRO, "2"])
    m = s.reply(texto)
    assert codigos(s) == [] and s.step.id == "sup_apagar_1"
    assert "cortocircuito" in m and "abierto" in m


def test_superposicion_acepta_el_balance_del_circuito_completo():
    s = sesion("superposicion-02", "practicar")
    dialogo(s, ["0.5 A", "2.667 A", "2.17 A"])
    m = s.reply("hago el balance de potencias del circuito completo, con todas las fuentes encendidas")
    assert "no se superpone" not in m and s.step.kind == "metacog"


def test_con_empate_el_cierre_nombra_el_primer_error():
    s = sesion("superposicion-02")
    dialogo(s, [INTRO, "2", "se deja abierta"])                 # SUP_V_ABIERTA
    for _ in range(30):
        if s.step.kind == "sup_suma":
            break
        s.reply(correct_answer(s))
    s.reply("0.5 + 2.667 = 3.17 A")                              # SUP_SIGNO
    assert codigos(s) == ["SUP_V_ABIERTA", "SUP_SIGNO"]
    for _ in range(30):
        if s.finished:
            break
        msg = s.reply(correct_answer(s))
    assert s.finished and ErrorBank().get("SUP_V_ABIERTA").descripcion[:40].lower() in msg.lower()


def test_superposicion_acepta_el_simulador_como_comprobacion():
    s = sesion("superposicion-02", "practicar")
    dialogo(s, ["0.5 A", "2.667 A", "2.17 A", "con Multisim"])
    assert s.step.kind == "sim"


# ================================================================ la guardia
def test_forbidden_incluye_parciales_y_suma_hasta_que_se_obtienen():
    s = sesion("superposicion-02", "practicar")
    prohibidos = s.forbidden_numbers()
    for valor in (0.5, 2.6667, 2.1667):
        assert any(abs(abs(valor) - abs(p)) < 1e-3 for p in prohibidos), valor
    s.reply("0.5 A")
    despues = s.forbidden_numbers()
    assert not any(abs(abs(p) - 0.5) < 1e-3 for p in despues)
    assert any(abs(p - 2.6667) < 1e-3 for p in despues)


def test_la_guardia_rechaza_decir_un_parcial_antes_de_tiempo():
    s = sesion("superposicion-02", "practicar")
    base = s.pregunta_actual()
    known = {float(e.value) for e in s.circuit.elements}
    ok, motivo = guard("Con V1 sola la corriente en R2 es de 0.5 A. " + base, base, s.forbidden_numbers(),
                       [], False, [], known)
    assert (ok, motivo) == (False, "filtra un resultado")
    s.reply("0.5 A")
    ok, motivo = guard("Con V1 sola la corriente en R2 es de 0.5 A. " + base, base, s.forbidden_numbers(),
                       [], False, [], known)
    assert motivo != "filtra un resultado"


def test_la_guardia_rechaza_decir_la_suma_antes_de_tiempo():
    s = sesion("superposicion-02", "practicar")
    base = s.pregunta_actual()
    ok, motivo = guard("El total es 2.17 A. " + base, base, s.forbidden_numbers(), [], False, [],
                       {float(e.value) for e in s.circuit.elements})
    assert (ok, motivo) == (False, "filtra un resultado")


def test_forbidden_protege_el_req_pendiente_del_paso_reduce():
    p = banco("serie-01")
    s = Session(p.circuit, p.method, "practicar")
    s.start()
    assert any(abs(x - 60) < 1e-9 for x in s.forbidden_numbers())
    base = s.pregunta_actual()
    ok, motivo = guard("La equivalente es de 60 Ω. " + base, base, s.forbidden_numbers(), [], False, [],
                       {float(e.value) for e in s.circuit.elements})
    assert (ok, motivo) == (False, "filtra un resultado")
    while s.step.kind == "reduce":
        s.reply(correct_answer(s))
    assert not any(abs(x - 60) < 1e-9 for x in s.forbidden_numbers())


# ================================================================== confianza
def test_los_pasos_nuevos_piden_confianza_salvo_los_casos():
    assert {"sup_apagar", "sup_parcial", "sup_suma"} <= CONFIANZA_KINDS
    assert "sup_casos" not in CONFIANZA_KINDS
    assert set(PASOS_CON_CONFIANZA) <= CONFIANZA_KINDS
    assert {"sup_apagar", "sup_parcial", "sup_suma"} <= set(PASOS_CON_CONFIANZA)
    s = sesion("superposicion-02")
    s.reply(INTRO)
    assert s.step.kind == "sup_casos" and not s.pide_confianza("2")
    s.reply("2")
    assert s.step.kind == "sup_apagar" and s.pide_confianza("la pongo en corto")
    s.reply("en corto")
    assert s.step.kind == "sup_parcial" and s.pide_confianza("0.5 A")


# ================================================================ reanudación
def _guion(pid, modo):
    """Una sesión con errores: sirve para cortarla en cada turno y reconstruirla."""
    s = sesion(pid, modo)
    guion = []
    errores = {
        "superposicion-02": ["se deja abierta", "-1.5 A", "3.17 A", "2.667 A"],
        "divisor-01": ["5.45 V", "no sé"],
        "divisor-02": ["1 A", "no sé"],
    }.get(pid, [])
    for malo in errores:
        if s.finished:
            break
        guion.append(malo)
        s.reply(malo)
        for _ in range(3):
            if s.finished:
                break
            texto = correct_answer(s)
            guion.append(texto)
            s.reply(texto)
    for _ in range(80):
        if s.finished:
            break
        texto = correct_answer(s)
        guion.append(texto)
        s.reply(texto)
    assert s.finished
    return guion


def _estado(s):
    return (s.idx, s.level, s.c, s.finished, s.justify_pending, s.item_failed, s.sup_sentidos,
            [(st.id, list(st.done), st.data.get("_intentos", 0)) for st in s.steps])


@pytest.mark.parametrize("modo", list(MODES))
@pytest.mark.parametrize("pid", ["divisor-01", "divisor-02", "superposicion-02", "superposicion-03"])
def test_reconstruir_cortada_en_cualquier_turno_queda_igual(pid, modo):
    guion = _guion(pid, modo)
    p = banco(pid)
    original = Session(p.circuit, p.method, modo)
    original.start()
    for corte, texto in enumerate(guion):
        original.reply(texto)
        copia = reconstruir(p.circuit, p.method, modo, guion[:corte + 1])
        assert _estado(copia) == _estado(original), (pid, modo, corte)


# ===================================================================== banco
NUEVOS = ["DIV_V_RT_INCORRECTA", "DIV_I_R_PROPIA", "DIV_I_SIN_REQ", "SUP_V_ABIERTA", "SUP_I_EN_CORTO",
          "SUP_SIGNO", "SUP_FALTA_FUENTE"]


@pytest.mark.parametrize("codigo", NUEVOS)
def test_cada_codigo_nuevo_tiene_todas_sus_columnas(codigo):
    e = ErrorBank().get(codigo)
    assert e is not None and e.descripcion and e.verificacion and e.metacognitiva
    assert all(e.niveles[n] for n in (1, 2, 3, 4))
    assert e.modulo if hasattr(e, "modulo") else True


def test_los_siete_codigos_se_producen_en_los_dialogos():
    vistos = set()
    for s, mensajes in [
        (sesion("divisor-01", "practicar"), ["5.45 V"]),
        (sesion("divisor-02", "practicar"), ["1 A"]),
        (propio("METODO: divisor\nPIDE: I R1\n\nIT 0 a 12\nR1 a 0 2\nR2 a 0 3\nR3 a 0 6", "practicar"), ["66 A"]),
        (sesion("superposicion-01", "practicar"), ["2 A", "9 A"]),
        (sesion("superposicion-01", "aprender"), [INTRO, "2", "en corto"]),
        (sesion("superposicion-02", "practicar"), ["0.5 A", "2.667 A", "3.17 A"]),
        (sesion("superposicion-02", "practicar"), ["0.5 A", "2.667 A", "2.667 A"]),
    ]:
        dialogo(s, mensajes)
        vistos |= set(codigos(s))
    assert set(NUEVOS) <= vistos


def test_los_textos_de_ayuda_no_dejan_marcadores_sin_llenar():
    s = sesion("superposicion-02")
    dialogo(s, [INTRO, "2", "se deja abierta"])
    for _ in range(4):
        texto = s.reply("se deja abierta")
        assert "…" not in texto and "{" not in texto


# ============================================================ interfaz y banco
def test_los_temas_nuevos_estan_en_la_lista_de_la_interfaz():
    assert TOPICS["divisor"] == "Divisor de voltaje y corriente" and TOPICS["superposicion"] == "Superposición"
    assert {p.topic for p in BANK.values()} <= set(TOPICS)
    assert _describe(banco("divisor-01"))["topicName"] == "Divisor de voltaje y corriente"
    assert _describe(banco("superposicion-03"))["topicName"] == "Superposición"


def test_sup_apagar_se_anota_como_concepto():
    assert PASOS_ANOTADOS["sup_apagar"] == "concepto"
    s = sesion("superposicion-01")
    dialogo(s, [INTRO, "2"])
    assert s.step.kind == "sup_apagar" and s.step.data["hint"]


@pytest.mark.parametrize("pid", ["divisor-01", "divisor-02", "superposicion-01", "superposicion-02",
                                 "superposicion-03", "superposicion-04"])
def test_los_problemas_nuevos_traen_fuente_respuesta_e_imagen(pid):
    p = banco(pid)
    assert p.source and p.expected and p.image
    assert "Material de clase" in p.source


def test_la_respuesta_de_la_practica_sale_de_la_solucion_por_thevenin():
    p = next(p for p in BANK.values() if p.id.startswith("superposicion-04"))
    assert "Thévenin" in p.source and "pp. 37–38" in p.source


def test_el_riel_inferior_del_ejemplo_3_une_los_extremos_de_r3_y_r4():
    c = banco("superposicion-03").circuit
    assert c.get("R3").n1 == c.get("R4").n2 == c.get("R1").n2 == c.get("R2").n2 == "0"


# ============================================================ Thévenin y Norton
from sympy import Rational  # noqa: E402

from tutor.problems import parse_targets, targets_to_text  # noqa: E402
from tutor.solver import corriente_corto, equivalente_thevenin, voltaje_abierto  # noqa: E402

EJEMPLO_1 = "V1 x 0 12\nR1 x A 3\nR2 A 0 6"
EJEMPLO_2 = "I1 0 x 4\nR1 x A 3\nR2 A 0 2"
EJEMPLO_3 = "I1 0 x 3\nR1 x m 2\nR3 x A 4\nR2 m A 6\nR4 m 0 1"
PRACTICA = "IX 0 tl 2\nR1 tl a 2\nR2 a b 1\nVX a c 5\nR3 b 0 6\nR4 b c 8\nR5 c 0 10"
FUENTES_MIXTAS = "V1 a 0 10\nR1 a b 2\nR2 b 0 3\nI1 0 b 1"
FUENTE_INVERTIDA = "V1 0 a 10\nR1 a A 3\nR2 A 0 6"


@pytest.mark.parametrize("red, a, b, carga, esperado", [
    (EJEMPLO_1, "A", "0", None, (8, 2, 4)),
    (EJEMPLO_2, "A", "0", None, (8, 2, 4)),
    (EJEMPLO_3, "A", "0", None, (6, 4, Rational(3, 2))),
    (PRACTICA, "a", "b", "R2", (Rational(35, 3), Rational(16, 3), Rational(35, 16))),
    # a mano: V1 aporta 10·3/5 = 6 V en b, I1 aporta 1 A·(2 ∥ 3) = 6/5 V; en corto pasan 10/2 + 1 = 6 A
    (FUENTES_MIXTAS, "b", "0", None, (Rational(36, 5), Rational(6, 5), 6)),
    # fuente al revés: A queda más negativa que 0 y la corriente por el corto va de 0 hacia A
    (FUENTE_INVERTIDA, "A", "0", None, (Rational(-20, 3), 2, Rational(-10, 3))),
])
def test_equivalente_de_thevenin_de_los_ejemplos_y_de_circuitos_a_mano(red, a, b, carga, esperado):
    c = parse_netlist(red)
    vth, rth, i_n = equivalente_thevenin(c, a, b, carga)
    assert (vth, rth, i_n) == tuple(Rational(x) for x in esperado)
    assert vth == i_n * rth
    assert voltaje_abierto(c, a, b, carga) == vth and corriente_corto(c, a, b, carga) == i_n


def test_sin_retirar_la_carga_el_voltaje_abierto_es_el_del_circuito_completo():
    c = parse_netlist(PRACTICA)
    assert voltaje_abierto(c, "a", "b") == solve(c).elem_v["R2"] == Rational(35, 19)
    assert voltaje_abierto(c, "a", "b", "R2") == Rational(35, 3)


def test_el_corto_entre_terminales_no_modifica_el_circuito_original():
    c = parse_netlist(EJEMPLO_1)
    corriente_corto(c, "A", "0")
    assert [e.name for e in c.elements] == ["V1", "R1", "R2"] and solve(c).elem_v["R2"] == 8


@pytest.mark.parametrize("a, b", [("A", "A"), ("A", "zz"), ("zz", "0")])
def test_el_solver_rechaza_terminales_que_no_sirven(a, b):
    c = parse_netlist(EJEMPLO_1)
    with pytest.raises(CircuitError):
        voltaje_abierto(c, a, b)
    with pytest.raises(CircuitError):
        corriente_corto(c, a, b)


# --------------------------------------------------------------------- pedidos
@pytest.mark.parametrize("texto, esperado", [
    ("VIP", [Target("VIP")]),
    ("I R2; P R3", [Target("I", "R2"), Target("P", "R3")]),
    ("V R1\nI R2", [Target("V", "R1"), Target("I", "R2")]),
    ("Req V1", [Target("Req", "V1")]),
    ("Req a-b", [Target("Req", None, ("a", "b"))]),
    ("Req A-0; V R2", [Target("Req", None, ("A", "0")), Target("V", "R2")]),
])
def test_los_pedidos_anteriores_se_leen_igual(texto, esperado):
    assert parse_targets(texto) == esperado
    assert parse_targets(targets_to_text(esperado)) == esperado


@pytest.mark.parametrize("texto, esperado", [
    ("TH A-B", [Target("TH", None, ("A", "B"))]),
    ("th a-0", [Target("TH", None, ("a", "0"))]),
    ("TH R2", [Target("TH", "R2")]),
    ("TH R2; V R2; I R2", [Target("TH", "R2"), Target("V", "R2"), Target("I", "R2")]),
])
def test_el_equivalente_se_pide_entre_terminales_o_como_lo_ve_una_carga(texto, esperado):
    assert parse_targets(texto) == esperado
    assert parse_targets(targets_to_text(esperado)) == esperado


def test_describe_dice_a_quien_ve_el_equivalente():
    assert "entre A y B" in Target("TH", None, ("A", "B")).describe()
    assert "que ve R2" in Target("TH", "R2").describe()


@pytest.mark.parametrize("texto", ["TH", "X R2", "TH-R2"])
def test_un_pedido_que_no_se_entiende_sigue_siendo_error(texto):
    with pytest.raises(CircuitError):
        parse_targets(texto)


@pytest.mark.parametrize("texto", [
    "METODO: thevenin\nPIDE: I R2\n\n" + EJEMPLO_1,                     # sin equivalente
    "METODO: thevenin\nPIDE: TH A-0; TH R2\n\n" + EJEMPLO_1,           # dos equivalentes
    "METODO: thevenin\nPIDE: TH A-zz\n\n" + EJEMPLO_1,                 # nodo que no existe
    "METODO: thevenin\nPIDE: TH V1\n\n" + EJEMPLO_1,                   # la carga debe ser una resistencia
    "METODO: thevenin\nPIDE: TH R9\n\n" + EJEMPLO_1,                   # elemento que no existe
    "METODO: thevenin\nPIDE: TH A-0; VIP\n\n" + EJEMPLO_1,             # la tabla no va con el equivalente
    "METODO: thevenin\nPIDE: TH A-0\n\nR1 A 0 3\nR2 A 0 6",            # sin fuentes
    "METODO: thevenin\nPIDE: TH x-0\n\nV1 x 0 12\nR1 x A 3\nR2 A 0 6",  # un voltaje directo entre las terminales
])
def test_thevenin_rechaza_pedidos_que_no_se_pueden_resolver(texto):
    with pytest.raises(CircuitError):
        propio(texto)


@pytest.mark.parametrize("metodo", ["ohm", "serie_paralelo", "divisor", "superposicion", "mallas", "nodos"])
def test_el_equivalente_se_pide_solo_con_el_metodo_thevenin(metodo):
    with pytest.raises(CircuitError, match="método thevenin"):
        propio(f"METODO: {metodo}\nPIDE: TH A-0\n\n" + EJEMPLO_1)


# ----------------------------------------------------------------- diálogos
INTRO_TH = "Me dan una fuente y dos resistencias y busco el equivalente entre A y B"


def test_thevenin_el_nombre_del_metodo_conserva_las_mayusculas():
    s = sesion("thevenin-01")
    assert "con Thévenin y Norton" in s.transcript[0]["texto"]


def test_thevenin_con_la_fuente_de_corriente_en_corto_da_la_resistencia_equivocada():
    s = sesion("thevenin-02")
    dialogo(s, [INTRO_TH])
    assert s.step.id == "th_rth" and "apagar las fuentes" in s.transcript[-1]["texto"]
    m = s.reply("1.2")                                  # R1 ∥ R2: la fuente de corriente quedó en corto
    assert codigos(s) == ["TH_FUENTES_ENCENDIDAS"] and s.level == 1
    assert "I1 en corto" in m and "{" not in m and "…" not in m
    m = s.reply("2 Ω")
    assert s.c["errores"] == ["TH_FUENTES_ENCENDIDAS"] and s.justify_pending is not None
    assert "R1 queda con una terminal suelta" in m and "no se toma en cuenta" in m
    dialogo(s, ["porque una fuente de corriente apagada deja el camino abierto"])
    assert s.step.id == "th_vth"


def test_thevenin_con_la_fuente_de_voltaje_abierta_da_la_resistencia_equivocada():
    s = sesion("thevenin-01", "practicar")
    m = s.reply("6")                                    # sólo queda R2: la fuente quedó abierta
    assert codigos(s) == ["TH_FUENTES_ENCENDIDAS"] and "V1 abierta" in m
    s.reply("2")
    assert s.step.id == "th_vth" and codigos(s) == ["TH_FUENTES_ENCENDIDAS"]


@pytest.mark.parametrize("pid, texto, fuente", [
    ("thevenin-01", "dejo la fuente de voltaje abierta", "V1"),
    ("thevenin-01", "la fuente queda abierta", "V1"),
    ("thevenin-02", "pongo la fuente de corriente en corto", "I1"),
    ("thevenin-02", "la fuente de corriente la dejo en cortocircuito", "I1"),
])
def test_thevenin_apagar_mal_las_fuentes_se_detecta_tambien_sin_el_valor(pid, texto, fuente):
    s = sesion(pid, "practicar")
    s.reply(texto)
    assert codigos(s) == ["TH_FUENTES_ENCENDIDAS"] and s.step.id == "th_rth"
    assert s.last_detail["fuente"] == fuente


@pytest.mark.parametrize("pid, texto", [
    ("thevenin-01", "pongo la fuente de voltaje en corto"),
    ("thevenin-01", "no la dejo abierta, la pongo en corto"),
    ("thevenin-02", "la fuente de corriente se abre"),
    ("thevenin-02", "la fuente de corriente no se pone en corto, queda abierta"),
    ("thevenin-02", "quito la fuente"),
    ("thevenin-01", "apago la fuente"),
])
def test_thevenin_apagar_bien_o_a_medias_no_se_penaliza_y_pide_la_resistencia(pid, texto):
    s = sesion(pid, "practicar")
    m = s.reply(texto)
    assert codigos(s) == [] and s.step.id == "th_rth"
    assert "No logré relacionar" not in m and "Con el circuito ya apagado, ¿qué resistencias quedan" in m


def test_thevenin_la_resistencia_suelta_se_dice_en_la_escalera_y_al_acertar():
    s = sesion("thevenin-02", "practicar")
    for _ in range(3):
        m = s.reply("pista")
    assert s.level == 3
    assert "R1 queda con una terminal suelta" in m and "Al abrir la fuente de corriente I1" in m
    s2 = sesion("thevenin-04", "practicar")
    for _ in range(3):
        m = s2.reply("pista")
    assert "R1 queda con una terminal suelta" in m and "IX" in m
    assert "suelta" in s2.reply("5.333")


def test_thevenin_sin_resistencias_sueltas_no_menciona_terminales_sueltas():
    s = sesion("thevenin-01", "practicar")
    for _ in range(3):
        m = s.reply("pista")
    assert "suelta" not in m
    assert "suelta" not in s.reply("2")


def test_thevenin_el_voltaje_de_thevenin_detecta_divisor_incompleto_y_signo():
    s = sesion("thevenin-01", "practicar")
    s.reply("2")
    s.reply("24 V")                                     # 12 V · 6 Ω / 3 Ω: sin la resistencia total
    assert codigos(s) == ["DIV_V_RT_INCORRECTA"] and s.step.id == "th_vth"
    m = s.reply("-8 V")
    assert codigos(s)[-1] == "VALUE_SIGN" and "V_A − V_0" in m and "terminal positiva" in m
    s.reply("8 V")
    assert s.step.id == "th_in"


def test_thevenin_la_mutacion_del_divisor_solo_aparece_donde_el_circuito_es_un_divisor():
    assert set(sesion("thevenin-01")._th_mutaciones_vth()) == {"DIV_V_RT_INCORRECTA"}
    for pid in ("thevenin-02", "thevenin-03", "thevenin-04"):
        assert sesion(pid)._th_mutaciones_vth() == {}
    s = sesion("thevenin-02", "practicar")
    s.reply("2")
    s.reply("5 V")
    assert codigos(s) == ["TARGET_WRONG"]


def test_thevenin_el_voltaje_acepta_la_cuenta_completa_y_varios_datos_en_el_mensaje():
    for texto in ("V_AB = 12(6)/(3 + 6) = 8 V", "Vth = 8 V, Rth = 2 Ω", "Vth = 8"):
        s = sesion("thevenin-01", "practicar")
        s.reply("2")
        s.reply(texto)
        assert s.step.id == "th_in" and codigos(s) == [], texto


def test_thevenin_la_corriente_de_norton_acepta_la_magnitud_y_registra_el_sentido():
    s = sesion("thevenin-03", "practicar")
    dialogo(s, ["4", "6"])
    m = s.reply("-1.5 A")                               # I3 de la diapositiva, con el sentido de su malla
    assert s.step.id == "th_equivalentes" and codigos(s) == []
    assert s.th_sentido_in == "en contra" and "magnitud" in m
    s = sesion("thevenin-03", "practicar")
    dialogo(s, ["4", "6", "1.5 A"])
    assert s.th_sentido_in == "a favor" and s.step.id == "th_equivalentes"


def test_thevenin_una_corriente_de_norton_equivocada_es_error():
    s = sesion("thevenin-03", "practicar")
    dialogo(s, ["4", "6", "6 A"])                       # Vth: no es la corriente del corto
    assert codigos(s) == ["TARGET_WRONG"] and s.step.id == "th_in"


@pytest.mark.parametrize("texto", [
    "Vth en serie con Rth e IN en paralelo con RN",
    "Thévenin en serie, Norton en paralelo",
    "la fuente de voltaje va en serie y la de corriente en paralelo",
    "IN no va en serie, va en paralelo; Vth no va en paralelo, va en serie",
    "Norton: no va en serie sino en paralelo. Thévenin en serie.",
    "Vth = 8 V en serie con Rth = 2 Ω; IN = 4 A en paralelo con RN = 2 Ω",
    "Rth va en serie con Vth. RN va en paralelo con IN",
    "Thévenin es Vth en serie con Rth; Norton es IN en paralelo con RN, y RN es igual a Rth",
    "Vth en serie con Rth, IN en paralelo con RN, y RN = Rth",
])
def test_equivalentes_se_leen_bien_con_negaciones_y_con_valores(texto):
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4", texto])
    assert s.step.kind == "verify" and codigos(s) == [], texto


@pytest.mark.parametrize("texto", [
    "Thévenin va en serie con Rth",
    "Norton va en paralelo",
    "Norton no va en serie",
    "la fuente de voltaje en serie",
])
def test_equivalentes_a_medias_piden_el_otro_sin_penalizar(texto):
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4"])
    m = s.reply(texto)
    assert s.step.id == "th_equivalentes" and codigos(s) == []
    assert "Eso está bien" in m and ("Norton" in m or "Thévenin" in m) and "?" in m


@pytest.mark.parametrize("texto", [
    "serie y paralelo",
    "Norton va en serie y en paralelo",
    "Vth en serie con Rth, pero también en paralelo",
])
def test_equivalentes_ambiguos_piden_precisar_sin_penalizar(texto):
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4"])
    s.reply(texto)
    assert s.step.id == "th_equivalentes" and codigos(s) == []


@pytest.mark.parametrize("texto, equivalente", [
    ("Vth en serie con Rth e IN en serie con RN", "Norton"),
    ("IN en serie con RN", "Norton"),
    ("los dos van en serie", "Norton"),
    ("Vth en paralelo con Rth", "Thévenin"),
    ("Thévenin no va en serie", "Thévenin"),
    ("la fuente de voltaje va en paralelo y la de corriente también", "Thévenin"),
])
def test_equivalentes_con_la_conexion_cambiada_dan_el_codigo(texto, equivalente):
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4"])
    m = s.reply(texto)
    assert codigos(s) == ["TH_NORTON_EN_SERIE"] and s.step.id == "th_equivalentes"
    assert equivalente in m and "{" not in m and "…" not in m


def test_equivalentes_sin_relacion_con_la_pregunta_dan_error_conceptual():
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4", "eso se resuelve con mallas"])
    assert codigos(s) == ["CONCEPT_WRONG"]


def test_equivalentes_corregir_el_error_en_aprender_pide_justificar():
    s = sesion("thevenin-01")
    dialogo(s, [INTRO_TH, "2", "porque no se ve ninguna", "8", "4"])
    s.reply("IN en serie con RN")
    assert codigos(s) == ["TH_NORTON_EN_SERIE"]
    s.reply("IN en paralelo con RN y Vth en serie con Rth")
    assert s.justify_pending is not None and s.step.kind == "verify"
    s.reply("porque la fuente de corriente comparte los dos nodos con su resistencia")
    assert s.justify_pending is None and s.c["justificaciones"] == 1


def test_thevenin_con_carga_pide_voltaje_y_corriente_con_el_equivalente():
    s = sesion("thevenin-04", "practicar")
    dialogo(s, ["5.333", "11.667", "2.1875", "Vth en serie con Rth, IN en paralelo con RN"])
    assert s.step.kind == "targets"
    assert "equivalente de Thévenin" in s.pregunta_actual()
    s.reply("2.1875 V")                                 # Vth·R/Rth: sin sumar la carga a Rth
    assert codigos(s) == ["DIV_V_RT_INCORRECTA"]
    s.reply("11.667 V")                                 # el voltaje abierto, sin dividir
    assert codigos(s) == ["DIV_V_RT_INCORRECTA"] * 2
    for _ in range(3):
        s.reply("pista")
    base = s.ultimo_turno["mensaje_base"]
    assert s.level == 5 and "Vth = 11.67 V" in base and "Rth = 5.333 Ω" in base and "1.842" not in base
    s.reply("1.842 V")
    assert s.step.kind == "targets"
    m = s.reply("1.842 A")
    assert s.step.kind == "verify" and "Correcto" in m


def test_thevenin_escalera_de_la_carga_nivel_dos_da_la_formula():
    s = sesion("thevenin-04", "practicar")
    dialogo(s, ["5.333", "11.667", "2.1875", "Vth en serie con Rth, IN en paralelo con RN"])
    s.reply("pista")
    s.reply("pista")
    assert "Vth · R2/(Rth + R2)" in s.ultimo_turno["mensaje_base"]


@pytest.mark.parametrize("texto", [
    "compruebo que Vth = IN·Rth", "IN = Vth/Rth", "Rth = Vth / IN", "8 = 4·2",
    "divido el voltaje de Thévenin entre la corriente de Norton",
    "multiplico IN por Rth y debe dar Vth",
])
def test_thevenin_verificacion_acepta_la_relacion_de_los_tres_resultados(texto):
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4", "Vth en serie con Rth, IN en paralelo con RN"])
    assert s.step.kind == "verify"
    s.reply(texto)
    assert s.step.kind == "metacog" and codigos(s) == [], texto


def test_thevenin_el_balance_de_potencias_no_comprueba_el_equivalente():
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4", "Vth en serie con Rth, IN en paralelo con RN"])
    m = s.reply("hago el balance de potencias")
    assert s.step.kind == "verify" and codigos(s) == [] and "no comprueba el equivalente" in m
    assert s.indicators()["Verificó con balance de potencias"] == "No"


def test_thevenin_una_verificacion_vaga_se_marca():
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4", "Vth en serie con Rth, IN en paralelo con RN", "lo reviso"])
    assert codigos(s) == ["VERIFY_VAGUE"]


def test_thevenin_verificar_reconoce_rth_vth_in_y_la_carga_por_su_valor():
    s = sesion("thevenin-01", "verificar")
    m = s.reply("Rth = 2 Ω, Vth = 8 V e IN = 4 A")
    assert "✔ Rth = 2 Ω" in m and "✔ Vth = 8 V" in m and "✔ IN = 4 A" in m
    assert s.step.id == "th_equivalentes"
    s = sesion("thevenin-01", "verificar")
    m = s.reply("2, 8 y 4")
    assert "✔ Rth" in m and "✔ Vth" in m and "✔ IN" in m
    s = sesion("thevenin-04", "verificar")
    m = s.reply("Rth = 5.333, Vth = 11.667; en R2: V = 1.842 V, I = 1.842 A")
    assert "✔ Rth" in m and "✔ Vth" in m and "el voltaje en R2" in m and "la corriente en R2" in m
    assert s.step.id == "th_in"


def test_thevenin_verificar_deja_pendiente_lo_que_esta_mal_o_falta():
    s = sesion("thevenin-03", "verificar")
    m = s.reply("Vth = 6 V; IN = 5 A")
    assert "✔ Vth = 6 V" in m and "✔ IN" not in m and "✔ Rth" not in m
    assert s.step.id == "th_rth"


# ----------------------------------------------------------------- la guardia
def test_forbidden_incluye_rth_vth_in_hasta_que_se_obtienen():
    s = sesion("thevenin-01", "practicar")
    assert {2, 8, 4} <= {round(x, 6) for x in s.forbidden_numbers()}
    s.reply("2")
    assert 2 not in {round(x, 6) for x in s.forbidden_numbers()}
    s.reply("8")
    assert {4} == {round(x, 6) for x in s.forbidden_numbers()}
    s.reply("4")
    assert s.forbidden_numbers() == []


def test_forbidden_protege_tambien_la_carga_pendiente():
    s = sesion("thevenin-04", "practicar")
    prohibidos = s.forbidden_numbers()
    for valor in (5.3333, 11.6667, 2.1875, 1.8421):
        assert any(abs(valor - p) < 1e-3 for p in prohibidos), valor
    dialogo(s, ["5.333", "11.667", "2.1875", "Vth en serie con Rth, IN en paralelo con RN"])
    assert [round(x, 3) for x in s.forbidden_numbers()] == [1.842, 1.842]
    s.reply("1.842 V")
    assert [round(x, 3) for x in s.forbidden_numbers()] == [1.842]


def test_la_guardia_rechaza_decir_rth_vth_o_in_antes_de_tiempo():
    s = sesion("thevenin-01", "practicar")
    conocidos = {float(e.value) for e in s.circuit.elements}

    def dicho(frase):
        base = s.pregunta_actual()
        return guard(frase + " " + base, base, s.forbidden_numbers(), [], False, [], conocidos)

    assert dicho("La resistencia de Thévenin es de 2 Ω.") == (False, "filtra un resultado")
    s.reply("2")
    assert dicho("La resistencia de Thévenin es de 2 Ω.")[1] != "filtra un resultado"
    assert dicho("El voltaje de Thévenin sale de 8 V.") == (False, "filtra un resultado")
    s.reply("8")
    assert dicho("La corriente de Norton es de 4 A.") == (False, "filtra un resultado")


# ------------------------------------------------------------------- confianza
def test_los_pasos_de_thevenin_piden_confianza():
    nuevos = {"th_vth", "th_in", "th_equivalentes"}
    assert nuevos <= CONFIANZA_KINDS and nuevos <= set(PASOS_CON_CONFIANZA)
    assert set(PASOS_CON_CONFIANZA) <= CONFIANZA_KINDS
    s = sesion("thevenin-01")
    s.reply(INTRO_TH)
    assert s.step.id == "th_rth" and s.pide_confianza("2")
    dialogo(s, ["2"])
    assert s.step.id == "th_vth" and s.pide_confianza("8 V")
    s.reply("8 V")
    assert s.step.id == "th_in" and s.pide_confianza("4 A")
    s.reply("4 A")
    assert s.step.id == "th_equivalentes" and s.pide_confianza("Thévenin en serie, Norton en paralelo")


# ------------------------------------------------------------------ reanudación
def _guion_th(pid, modo):
    """Una sesión de Thévenin con errores, para cortarla en cada turno y reconstruirla."""
    s = sesion(pid, modo)
    guion = []
    errores = {
        "thevenin-01": ["6", "24 V", "IN en serie con RN"],
        "thevenin-02": ["1.2", "no sé", "-8 V"],
        "thevenin-03": ["quito la fuente", "-1.5 A"],
        "thevenin-04": ["pongo la fuente de corriente en corto", "2.1875 V"],
    }[pid]
    for malo in errores:
        if s.finished:
            break
        guion.append(malo)
        s.reply(malo)
        for _ in range(3):
            if s.finished:
                break
            texto = correct_answer(s)
            guion.append(texto)
            s.reply(texto)
    for _ in range(80):
        if s.finished:
            break
        texto = correct_answer(s)
        guion.append(texto)
        s.reply(texto)
    assert s.finished
    return guion


@pytest.mark.parametrize("modo", list(MODES))
@pytest.mark.parametrize("pid", ["thevenin-01", "thevenin-02", "thevenin-03", "thevenin-04"])
def test_reconstruir_thevenin_cortada_en_cualquier_turno_queda_igual(pid, modo):
    guion = _guion_th(pid, modo)
    p = banco(pid)
    original = Session(p.circuit, p.method, modo)
    original.start()
    for corte, texto in enumerate(guion):
        original.reply(texto)
        copia = reconstruir(p.circuit, p.method, modo, guion[:corte + 1])
        assert _estado(copia) == _estado(original), (pid, modo, corte)
        assert copia.th_sentido_in == original.th_sentido_in


# ---------------------------------------------------------------------- banco
NUEVOS_TH = ["TH_FUENTES_ENCENDIDAS", "TH_NORTON_EN_SERIE"]


@pytest.mark.parametrize("codigo", NUEVOS_TH)
def test_cada_codigo_de_thevenin_tiene_todas_sus_columnas(codigo):
    e = ErrorBank().get(codigo)
    assert e is not None and e.descripcion and e.verificacion and e.metacognitiva
    assert all(e.niveles[n] for n in (1, 2, 3, 4)) and e.modulo == "thevenin"


def test_los_dos_codigos_de_thevenin_se_producen_en_los_dialogos():
    vistos = set()
    s = sesion("thevenin-02", "practicar")
    dialogo(s, ["1.2"])
    vistos |= set(codigos(s))
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4", "IN en serie con RN"])
    vistos |= set(codigos(s))
    assert set(NUEVOS_TH) <= vistos


@pytest.mark.parametrize("modo", list(MODES))
def test_thevenin_el_error_de_las_fuentes_se_detecta_en_los_cuatro_modos(modo):
    s = sesion("thevenin-02", modo)
    mensajes = ["Rth = 1.2 Ω"] if modo == "verificar" else ["1.2"]
    if modo in ("aprender", "simulador"):
        mensajes = [INTRO_TH] + mensajes
    dialogo(s, mensajes)
    if modo == "verificar":                              # sólo reconoce lo correcto: lo demás se pregunta
        assert codigos(s) == [] and s.step.id == "th_rth"
    else:
        assert codigos(s) == ["TH_FUENTES_ENCENDIDAS"] and s.step.id == "th_rth"


def test_los_textos_de_ayuda_de_thevenin_no_dejan_marcadores_sin_llenar():
    s = sesion("thevenin-02", "practicar")
    for _ in range(5):
        texto = s.reply("1.2")
        assert "…" not in texto and "{" not in texto
    s = sesion("thevenin-01", "practicar")
    dialogo(s, ["2", "8", "4"])
    for _ in range(5):
        texto = s.reply("IN en serie con RN")
        assert "…" not in texto and "{" not in texto


# ------------------------------------------------------------- interfaz y banco
def test_thevenin_esta_en_la_lista_de_temas_de_la_interfaz():
    assert TOPICS["thevenin"] == "Thévenin y Norton"
    assert {p.topic for p in BANK.values()} <= set(TOPICS)
    assert _describe(banco("thevenin-01"))["topicName"] == "Thévenin y Norton"
    assert PASOS_ANOTADOS["th_equivalentes"] == "concepto"


@pytest.mark.parametrize("pid", ["thevenin-01", "thevenin-02", "thevenin-03", "thevenin-04"])
def test_los_problemas_de_thevenin_traen_fuente_respuesta_e_imagen(pid):
    p = banco(pid)
    assert p.source and p.expected and p.image and p.method == "thevenin" and p.topic == "thevenin"
    assert "Material de clase" in p.source and "Thévenin" in p.source
    assert set(p.expected) >= {"Rth", "Vth"}


def test_los_problemas_de_thevenin_traen_las_respuestas_publicadas():
    esperado = {"thevenin-01": (2, 8, 4), "thevenin-02": (2, 8, 4), "thevenin-03": (4, 6, 1.5)}
    for pid, (rth, vth, i_n) in esperado.items():
        p = banco(pid)
        assert (p.expected["Rth"], p.expected["Vth"], p.expected["IN"]) == (rth, vth, i_n)
    p = banco("thevenin-04")
    assert p.expected["Rth"] == 5.333 and p.expected["Vth"] == 11.666
    assert p.expected["V R2"] == p.expected["I R2"] == 1.842
    assert "pp. 37–38" in p.source and "p. 36" in p.source


def test_la_serie_04_sigue_igual():
    p = banco("serie-04")
    assert p.method == "serie_paralelo" and [t.quantity for t in p.circuit.targets] == ["Req", "V"]


def test_con_el_corto_el_ejemplo_2_deja_pasar_toda_la_corriente_de_la_fuente():
    c = banco("thevenin-02").circuit
    assert corriente_corto(c, "A", "0") == c.get("I1").value


# ----------------------------------------------------------------- los dibujos
@pytest.mark.parametrize("pid", ["thevenin-01", "thevenin-02", "thevenin-03", "thevenin-04"])
def test_los_dibujos_de_thevenin_marcan_las_terminales_en_los_nodos_correctos(pid):
    dibujos = pytest.importorskip("tools.dibujos")
    p = banco(pid)
    spec = dibujos.DIBUJOS[p.id]
    dibujos.check(p.id, spec, p.circuit)               # mismos elementos, nodos y polaridades que la tabla
    terminales = {t[0]: t[1] for t in spec["terminales"]}
    assert set(terminales) == {"A", "B"}
    objetivo = p.circuit.targets[0]
    if objetivo.element:
        carga = p.circuit.get(objetivo.element)
        esperado = {"A": carga.n1, "B": carga.n2}
    else:
        esperado = {"A": objetivo.nodes[0], "B": objetivo.nodes[1]}
    assert {k: dibujos.node_at(spec, v) for k, v in terminales.items()} == {k: [v] for k, v in esperado.items()}
    svg = (RAIZ_PROBLEMAS / p.image).read_text(encoding="utf-8")
    assert svg.count(">A<") == 1 and svg.count(">B<") == 1


def test_una_duda_de_teoria_en_los_pasos_de_concepto_se_atiende_como_pista():
    from escritorio.config import PASOS_DE_CONCEPTO
    # la ficha del tema trae la respuesta de estos pasos: se dan como pista y se anotan como concepto
    assert set(PASOS_DE_CONCEPTO) == {"concept", "sup_apagar", "th_equivalentes"}
    assert all(PASOS_ANOTADOS[k] == "concepto" for k in PASOS_DE_CONCEPTO)
