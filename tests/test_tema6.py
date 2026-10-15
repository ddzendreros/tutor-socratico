"""Divisor de voltaje y corriente, y superposición: solver, diálogos con errores típicos,
guardia de resultados pendientes, confianza y reanudación."""
import pytest

from escritorio.api import PASOS_ANOTADOS, TOPICS, _describe
from escritorio.config import PASOS_CON_CONFIANZA
from tutor.circuit import CircuitError, Target, parse_netlist
from tutor.engine import CONFIANZA_KINDS, MODES, Session, reconstruir
from tutor.errorbank import ErrorBank
from tutor.llm import guard
from tutor.problems import load_bank, parse_problem
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
