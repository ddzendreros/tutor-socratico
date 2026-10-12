"""Pruebas de la API del motor para la aplicación de escritorio."""
import pytest

from tests.test_flow import BANK, correct_answer
from tutor.engine import CONFIANZA_KINDS, MODES, Session, reconstruir


# ==================================================================== reconstruir
def test_reconstruir_dialogo_completo():
    """Reproduce el diálogo completo de mallas-01 y confirma identidad."""
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    s.start()
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
    for m in script:
        s.reply(m)
    assert s.finished

    s2 = reconstruir(p.circuit, p.method, "aprender", s.entradas, s.bank)
    assert s2.idx == s.idx
    assert s2.level == s.level
    assert s2.c == s.c
    assert s2.finished == s.finished
    assert s2.entradas == s.entradas


@pytest.mark.parametrize("pid,method,mode,script", [
    ("mallas-01-caso1-malla-real", "mallas", "practicar", [
        "I1(R1 + R2) - I2R2 = VX - VY",
        "I2(R2 + R3) - I1R2 = VY",
    ]),
    ("mallas-01-caso1-malla-real", "mallas", "practicar", [
        "-30I1 + 20I2 = 0",
        "20I1 - 50I2 = 100",
        "I1 = -1.82 A, I2 = -2.73 A",
    ]),
    ("mallas-02-supermalla", "mallas", "aprender", [
        "Una fuente de corriente de 2 A y una de voltaje de 50 V", "3",
        "R1, IX y R3", "R2, R4 y IX", "R3, R4 y VX",
        "la 1 es fantasma, la 2 real y la 3 real",
        "1 y 2 forman una supermalla y la 3 es real",
    ]),
    ("mallas-02-supermalla", "mallas", "practicar", [
        "I2 - I1 = 2",
        "40I1 + 60I2 - 70I3 = 0 V",
        "70I3 - 30I1 - 40I2 = -50 V",
    ]),
    ("nodos-03-supernodo", "nodos", "aprender", [
        "10 V, 31 V, 5 A y seis resistencias", "4", "1 fantasma, 2 y 3 supernodo, 5 real",
        "V1 = 10",
    ]),
    ("nodos-01-nodos-reales", "nodos", "practicar", [
        "I1 + I2 = 5",
    ]),
    ("serie-03-reto-mixto", "serie_paralelo", "practicar", [
        "R2 y R3 en paralelo = 20 ohm",
        "RT1 en serie con R4 = 40",
        "R4 en paralelo con RT1 = 10",
        "R1 + RT2 = 20",
    ]),
    ("ohm-01-ley-de-ohm", "ohm", "verificar", [
        "I = V/R = 20/10 = 2 A, P = V*I = 40 W",
        "PE = 20*2 = 40 W es igual a la consumida PC = 40 W",
    ]),
])
def test_reconstruir_dialogos_de_flujo(pid, method, mode, script):
    """Verifica que reconstruir replica el estado de diversos diálogos de prueba."""
    p = BANK[pid]
    s = Session(p.circuit, method, mode)
    s.start()
    for m in script:
        s.reply(m)

    s2 = reconstruir(p.circuit, method, mode, s.entradas, s.bank)
    assert s2.idx == s.idx
    assert s2.level == s.level
    assert s2.c == s.c
    assert s2.finished == s.finished
    assert s2.entradas == s.entradas


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("pid", sorted(BANK))
def test_reconstruir_todos_los_problemas_en_todos_los_modos(pid, mode):
    """Reutiliza el recorrido completo del banco en los 4 modos y comprueba reconstruir."""
    p = BANK[pid]
    s = Session(p.circuit, p.method, mode)
    s.start()
    for _ in range(60):
        if s.finished:
            break
        s.reply(correct_answer(s))
    assert s.finished

    s2 = reconstruir(p.circuit, p.method, mode, s.entradas, s.bank)
    assert s2.idx == s.idx
    assert s2.level == s.level
    assert s2.c == s.c
    assert s2.finished == s.finished
    assert s2.entradas == s.entradas


# ==================================================================== ultimo_turno
def test_ultimo_turno_en_start():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    msg = s.start()
    u = s.ultimo_turno
    assert u["paso_id"] == "intro"
    assert u["paso_tipo"] == "intro"
    assert u["correcto"] is None
    assert u["codigo"] is None
    assert u["nivel_antes"] == 0
    assert u["nivel_despues"] == 0
    assert u["pista"] is False
    assert u["intentos_paso"] == 0
    assert u["mensaje_base"] == msg
    assert u["permite_ecuaciones"] is False
    assert u["terminado"] is False


def test_ultimo_turno_caso_correcto():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    s.start()
    s.reply("Me dan dos fuentes de 100 V y tres resistencias, necesito V, I y P")
    u = s.ultimo_turno
    assert u["paso_id"] == "intro"
    assert u["paso_tipo"] == "intro"
    assert u["correcto"] is True
    assert u["codigo"] == "OK"
    assert u["nivel_antes"] == 0
    assert u["nivel_despues"] == 0
    assert u["pista"] is False
    assert u["intentos_paso"] == 1
    assert u["terminado"] is False


def test_ultimo_turno_caso_incorrecto():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    s.start()
    s.reply("Me dan dos fuentes de 100 V y tres resistencias, necesito V, I y P")
    s.reply("cinco")
    u = s.ultimo_turno
    assert u["paso_id"] == "mesh_count"
    assert u["paso_tipo"] == "mesh_count"
    assert u["correcto"] is False
    assert u["codigo"] == "MESH_COUNT_WRONG"
    assert u["nivel_antes"] == 0
    assert u["nivel_despues"] == 1
    assert u["pista"] is True
    assert u["intentos_paso"] == 1
    assert u["terminado"] is False


def test_ultimo_turno_caso_ayuda():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    s.start()
    s.reply("ayuda")
    u = s.ultimo_turno
    assert u["paso_id"] == "intro"
    assert u["paso_tipo"] == "intro"
    assert u["correcto"] is None
    assert u["codigo"] is None
    assert u["nivel_antes"] == 0
    assert u["nivel_despues"] == 1
    assert u["pista"] is True
    assert u["intentos_paso"] == 0
    assert u["terminado"] is False


def test_ultimo_turno_permite_ecuaciones_en_nivel_alto():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    s.start()
    for _ in range(4):
        s.reply("ayuda")
    assert s.level >= 4
    assert s.ultimo_turno["permite_ecuaciones"] is True


def test_pregunta_actual():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    s.start()
    assert s.pregunta_actual() == s._step_prompt(s.step)


def test_pide_confianza():
    p = BANK["mallas-01-caso1-malla-real"]
    # En modo aprender, el paso inicial 'intro' no pide confianza
    s_apr = Session(p.circuit, p.method, "aprender")
    s_apr.start()
    assert s_apr.step.kind == "intro"
    assert s_apr.step.kind not in CONFIANZA_KINDS
    assert s_apr.pide_confianza() is False

    # En modo practicar, el paso inicial es 'equations', que sí pide confianza
    s = Session(p.circuit, p.method, "practicar")
    s.start()
    assert s.step.kind == "equations"
    assert s.step.kind in CONFIANZA_KINDS
    assert s.pide_confianza() is True

    # Pide ayuda: no cuenta como intento evaluado
    s.reply("ayuda")
    assert s.step.data.get("_intentos", 0) == 0
    assert s.pide_confianza() is True

    # Primer intento evaluado: pasa el intento y ya no pide confianza
    s.reply("I1(R1 + R2) - I2R2 = VX - VY")
    assert s.step.data.get("_intentos") == 1
    assert s.pide_confianza() is False


def test_pide_confianza_no_aplica_a_ayuda_ni_justificacion():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "practicar")
    s.start()
    assert s.pide_confianza("I1(R1 + R2) - I2R2 = VX - VY") is True
    assert s.pide_confianza("dame una pista") is False
    assert s.pide_confianza("resuélvelo tú") is False

    s = Session(p.circuit, p.method, "aprender")
    s.start()
    for m in ["Me dan dos fuentes de 100 V y tres resistencias, necesito V, I y P de cada una",
              "dos", "VX, R1, R2 y VY", "VY, R2 y R3", "R2 y VY", "I1 + I2", "2 A", "I1 - I2"]:
        s.reply(m)
    assert s.justify_pending is not None and s.step.kind == "equations"
    assert s.pide_confianza() is False
    assert s.pide_confianza("porque las corrientes van en sentidos opuestos") is False
    assert s.pide_confianza("I1(R1 + R2) - I2R2 = VX - VY") is True   # contesta el paso en vez de justificar


# ==================================================================== estado completo
def _estado(s):
    return (s.idx, s.level, s.finished, s.c, s.justify_pending, s.item_failed, s.last_code, s._why,
            s.metacog_asked, [(st.id, list(st.done), st.data.get("_intentos", 0)) for st in s.steps])


RUIDO = ["no sé", "cinco", "I1 = 3 A", "ayuda", "dame la respuesta", "¿por qué?", "R1 y R2 en serie",
         "xyz", "0", "hola", "V1 = 0", "I1 + I2", "la misma", "?"]


@pytest.mark.parametrize("mode", list(MODES))
@pytest.mark.parametrize("pid", sorted(BANK))
def test_retomar_a_media_sesion_con_errores_y_ayudas(pid, mode):
    """Retoma a la mitad de un diálogo con errores y ayudas: mismo estado y mismas respuestas después."""
    p = BANK[pid]
    s = Session(p.circuit, p.method, mode)
    s.start()
    salidas = []
    for k in range(150):
        if s.finished:
            break
        texto = RUIDO[(k // 3) % len(RUIDO)] if k % 3 == 1 else correct_answer(s)
        salidas.append(s.reply(texto))
        assert salidas[-1] == s.ultimo_turno["mensaje_base"]
        assert s.ultimo_turno["terminado"] == s.finished
    assert s.finished

    mitad = len(s.entradas) // 2
    r = reconstruir(p.circuit, p.method, mode, s.entradas[:mitad], s.bank)
    s_mitad = Session(p.circuit, p.method, mode)
    s_mitad.start()
    for texto in s.entradas[:mitad]:
        s_mitad.reply(texto)
    assert _estado(r) == _estado(s_mitad)
    for texto, esperado in zip(s.entradas[mitad:], salidas[mitad:]):
        assert r.reply(texto) == esperado
    assert _estado(r) == _estado(s)


# ==================================================================== ruta_rapida
CASOS_RUTA_RAPIDA = [
    # Ecuaciones con variables, '=' o números
    ("I1(R1 + R2) - I2R2 = VX - VY", "motor"),
    ("I1 - I2 = 0", "motor"),
    ("V1 = 10", "motor"),
    ("6I1 - 2I2 = 12", "motor"),
    # Valores numéricos con y sin unidades
    ("2 A", "motor"),
    ("100 V", "motor"),
    ("50 W", "motor"),
    ("10 ohms", "motor"),
    ("20 Ω", "motor"),
    ("0.5 mA", "motor"),
    ("12", "motor"),
    # Unidades solas
    ("volts", "motor"),
    ("amperes", "motor"),
    ("watts", "motor"),
    # Peticiones directas o de ayuda según regexes del motor
    ("ayuda", "motor"),
    ("dame una pista", "motor"),
    ("no sé", "motor"),
    ("dame la respuesta", "motor"),
    ("resuélvelo tú", "motor"),
    # Respuestas textuales/conceptuales (en cualquier otro caso)
    ("malla real", "motor"),
    ("fantasma", "motor"),
    ("supernodo", "motor"),
    ("la misma", "motor"),
    ("voltaje", "motor"),
    ("con el balance de potencias", "motor"),
    # Preguntas que parecen pregunta (llevan interrogativo o '?') -> "ambiguo"
    ("¿por qué se resta en la rama compartida?", "ambiguo"),
    ("¿qué me estás pidiendo?", "ambiguo"),
    ("¿cómo se calcula eso?", "ambiguo"),
    ("¿cuál es la ecuación auxiliar?", "ambiguo"),
    ("¿dónde coloco la referencia?", "ambiguo"),
    ("¿cuándo se forma una supermalla?", "ambiguo"),
    ("¿para qué sirve este método?", "ambiguo"),
    # Expresiones de emoción o charla -> "ambiguo"
    ("hola tutor", "ambiguo"),
    ("muchas gracias", "ambiguo"),
    ("ya me cansé", "ambiguo"),
    ("me frustra este ejercicio", "ambiguo"),
    ("jaja no puede ser", "ambiguo"),
    # Una letra suelta no es una unidad
    ("A ver, ¿qué hago aquí?", "ambiguo"),
    ("¿A qué te refieres?", "ambiguo"),
    ("¿Qué es la V?", "ambiguo"),
    # Casos difíciles del plan
    ("¿2 A?", "motor"),
    ("¿qué me pides?", "ambiguo"),
    ("no entiendo", "motor"),
    # La ley de Ohm no es una unidad; ánimo y saludos sin signo de interrogación
    ("¿es la ley de Ohm?", "ambiguo"),
    ("son 4 ohms", "motor"),
    ("ohms", "motor"),
    ("estoy muy cansado ya", "ambiguo"),
    ("esto está bien difícil", "ambiguo"),
    ("jajaja qué buena explicación", "ambiguo"),
    ("buenos días", "ambiguo"),
]


@pytest.mark.parametrize("texto,esperado", CASOS_RUTA_RAPIDA)
def test_ruta_rapida_casos_escritos_a_mano(texto, esperado):
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    assert s.ruta_rapida(texto) == esperado


def test_ruta_rapida_con_justificacion_pendiente():
    p = BANK["mallas-01-caso1-malla-real"]
    s = Session(p.circuit, p.method, "aprender")
    s.justify_pending = "Explica por qué"
    assert s.ruta_rapida("¿por qué se resta?") == "motor"
    assert s.ruta_rapida("hola") == "motor"
    assert s.ruta_rapida("porque van en sentidos opuestos") == "motor"
