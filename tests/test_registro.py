"""Pruebas del registro del alumno: consentimiento, sobre, secuencia, privacidad y anotación."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

from escritorio import equipo
from escritorio.alumnos import formatear, generar, normalizar
from escritorio.api import ApiTutor
from escritorio.cifrado import caja_estado, generar_llaves, guardar_llave_publica
from escritorio.conversacion import Conversador
from escritorio.registro import ESQUEMA_VERSION, leer_eventos
from tests.test_servidor import ENTRADAS_MALLAS, MALLAS, OHM
from tutor.llm import LLM
from tutor.privacidad import scrub

RUBRICA = {"expresa_idea": "si", "claridad": 3, "usa_terminos_tecnicos": True, "idea_erronea": "",
           "tipo": "explicacion"}
CAMPOS_TURNO = {
    "alumno": {"texto", "caracteres", "boton"},
    "intencion": {"etiqueta", "modelo", "ms"},
    "motor": {"respondio", "paso_id", "paso_tipo", "correcto", "codigo", "nivel_antes", "nivel_despues",
              "pista", "intentos_paso", "terminado"},
    "tutor": {"texto_base", "texto_mostrado", "fuente", "guardia", "reintentos", "intentos", "ms",
              "tokens_prompt", "tokens_salida", "tokens_cache"},
}
COMPOSICION = {"ms_desde_tutor": 5400, "ms_primera_tecla": 1200, "ms_escribiendo": 4200, "teclas": 6,
               "borrados": 1, "pegados": 0, "caracteres_pegados": 0, "pausas_2s": 1, "foco_perdido": 0,
               "eventos": [[0, "insertText", "d", 1], [150, "insertText", "o", 2], [300, "insertText", "s", 3]]}


class ModeloFalso(httpx.BaseTransport):
    """Clasifica la intención con una tabla, anota con una rúbrica fija y no redacta (queda la plantilla)."""

    def __init__(self, intenciones: dict | None = None, anotacion_valida: bool = True):
        self.intenciones = intenciones or {}
        self.anotacion_valida = anotacion_valida
        self.anotaciones = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        cuerpo = json.loads(request.content)
        formato = str(cuerpo.get("response_format", ""))
        ultimo = cuerpo["messages"][-1]["content"]
        if "clasificacion_intencion" in formato:
            contenido = json.dumps({"intencion": self.intenciones.get(ultimo.split("Mensaje del alumno: ")[-1],
                                                                      "respuesta")})
        elif "evaluacion_respuesta_abierta" in formato:
            self.anotaciones += 1
            contenido = json.dumps(RUBRICA) if self.anotacion_valida else "no es json"
        else:
            contenido = ""
        return httpx.Response(200, json={"choices": [{"message": {"content": contenido}}],
                                         "usage": {"prompt_tokens": 40, "completion_tokens": 20},
                                         "timings": {"cache_n": 10}})


def _api(tmp_path: Path, transporte: httpx.BaseTransport | None = None) -> ApiTutor:
    llm = LLM(base_url="http://modelo-falso", transport=transporte or ModeloFalso())
    privada, publica = generar_llaves()
    guardar_llave_publica(publica, tmp_path / "llave_publica.txt")
    api = ApiTutor(carpeta_datos=tmp_path / "datos", conversador=Conversador(llm=llm),
                   llave_publica=tmp_path / "llave_publica.txt", carpeta_exportaciones=tmp_path / "exportaciones")
    # sólo las pruebas guardan la privada, para leer lo que el programa no puede leer
    api.privada_prueba = privada
    return api


def _entrar(api: ApiTutor, acepta: bool | None = True, codigo: str | None = None) -> str:
    codigo = codigo or generar(1)[0]
    assert api.post_entrar({"codigo": codigo, "pantalla": {"ancho": 1920, "alto": 1080, "escala": 1.25}})[0] == 200
    if acepta is not None:
        api.post_consentimiento({"acepta": acepta})
    return codigo


def _responder(api: ApiTutor, texto: str, confianza: int | None = 3, composicion: dict | None = None) -> dict:
    st, r = api.post_responder({"texto": texto, "composicion": composicion})
    if r.get("necesita_confianza"):
        st, r = api.post_confianza({"valor": confianza, "ms": 900})
    assert st == 200, r
    return r


def _eventos(api: ApiTutor, codigo: str) -> list[dict]:
    ruta = api.registro.almacen.ruta_eventos(normalizar(codigo))
    return leer_eventos(ruta.read_bytes(), api.privada_prueba)


def _de_tipo(eventos: list[dict], tipo: str) -> list[dict]:
    return [e for e in eventos if e["tipo"] == tipo]


def test_scrub_conserva_valores_y_quita_datos_personales():
    for valor in ("1.8181818182 A", "100 1000 2200 ohms", "1200 W, 3300 W", "I = 0.0181818182 A",
                  "100 220 1000 Ω", "330 470 1000 ohms", "R1=330 R2=470 R3=1000"):
        assert scrub(valor) == valor
    assert scrub("mi boleta es 2024640123") == "mi boleta es [número]"
    assert scrub("escríbeme a alumno.prueba@ipn.mx") == "escríbeme a [correo]"
    assert scrub("mi cel es 55 1234 5678") == "mi cel es [teléfono]"
    assert scrub("o al 555-123-4567") == "o al [teléfono]"
    assert scrub("+52 55 1234 5678") == "[teléfono]"


def test_quien_no_acepta_no_deja_registro(tmp_path: Path):
    api = _api(tmp_path)
    codigo = _entrar(api, acepta=False)
    api.post_eventos({"eventos": [{"tipo": "ventana", "datos": {"estado": "oculta"}}]})
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    for texto in ENTRADAS_MALLAS[:7]:
        _responder(api, texto)
    api.post_pista()
    api.anotar_pendiente(reposo=0)
    api.post_salir({"motivo": "boton"})
    # tampoco quien todavía no contesta el aviso
    _entrar(api, acepta=None)
    api.post_eventos({"eventos": [{"tipo": "clic", "datos": {"control": "salir"}}]})
    api.post_salir({"motivo": "boton"})

    carpeta = tmp_path / "datos" / normalizar(codigo)
    assert list((tmp_path / "datos").rglob("eventos.*")) == []
    assert sorted(p.name for p in carpeta.iterdir()) == ["estado.bin"]
    caja = caja_estado(normalizar(codigo), api.registro.sal)
    assert json.loads(caja.decrypt((carpeta / "estado.bin").read_bytes()))["cola_anotaciones"] == []


def test_un_problema_completo_deja_el_registro_esperado(tmp_path: Path):
    api = _api(tmp_path)
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    for i, texto in enumerate(ENTRADAS_MALLAS):
        r = _responder(api, texto, composicion=COMPOSICION if i == 0 else None)
    assert r["terminado"]
    api.post_salir({"motivo": "boton"})
    eventos = _eventos(api, codigo)

    tipos = [e["tipo"] for e in eventos]
    assert tipos[:3] == ["consentimiento", "sesion_iniciada", "intento_iniciado"]
    assert tipos[-2:] == ["intento_terminado", "sesion_terminada"]
    assert "intento_pausado" not in tipos
    assert tipos.count("turno") == len(ENTRADAS_MALLAS)

    assert [e["seq"] for e in eventos] == list(range(1, len(eventos) + 1))
    for e in eventos:
        assert e["v"] == ESQUEMA_VERSION and e["origen"] == "app" and e["codigo"] == formatear(normalizar(codigo))
        assert e["instalacion"] == api.registro.instalacion and e["arranque"] == api.registro.arranque
        assert datetime.fromisoformat(e["t"]).utcoffset() is not None
        assert abs(datetime.fromisoformat(e["t"]).timestamp() * 1000 - e["ms"]) < 2

    inicio = _de_tipo(eventos, "sesion_iniciada")[0]["datos"]
    assert inicio["equipo"]["pantalla"] == {"ancho": 1920, "alto": 1080, "escala": 1.25}
    assert inicio["equipo"]["modo"] == "ventana" and inicio["version_consentimiento"]
    assert inicio["versiones"]["banco_hash"] == equipo.banco_hash()
    assert _de_tipo(eventos, "intento_iniciado")[0]["datos"]["tutor"]["texto_mostrado"]

    turnos = [e["datos"] for e in _de_tipo(eventos, "turno")]
    assert all(e["intento"] == f"{MALLAS}:aprender" for e in _de_tipo(eventos, "turno"))
    for t in turnos:
        for grupo, campos in CAMPOS_TURNO.items():
            assert set(t[grupo]) == campos, grupo
        assert t["teoria"] is None
    assert turnos[0]["composicion"]["teclas"] == 6 and len(turnos[0]["composicion"]["eventos"]) == 3
    assert turnos[0]["composicion"]["teclas_sin_texto"] is False
    assert turnos[1]["composicion"] is None

    # la confianza va sólo en el turno que se evaluó después de preguntarla
    preguntadas = [t for t in turnos if t["confianza"] is not None]
    assert preguntadas and all(t["confianza"] == {"valor": 3, "ms": 900} for t in preguntadas)
    assert all(t["motor"]["intentos_paso"] == 1 for t in preguntadas)

    por_texto = {t["alumno"]["texto"]: t for t in turnos}
    error = por_texto["I1 + I2"]["motor"]
    assert error["correcto"] is False and error["codigo"] and error["nivel_despues"] > error["nivel_antes"]
    justificacion = por_texto[ENTRADAS_MALLAS[8]]["motor"]
    assert justificacion["respondio"] and justificacion["correcto"] is None

    final = _de_tipo(eventos, "intento_terminado")[0]["datos"]
    assert len(final["indicadores"]) == 16 and final["indicadores"]["Completado"] == "Sí"
    assert final["segundos_activos"] >= 0


class ModeloIntenciones(ModeloFalso):
    def __init__(self):
        super().__init__({"¿qué me pides?": "pregunta_paso", "¿qué es una supermalla?": "pregunta_teoria",
                          "¿qué dice la ley de ohm?": "pregunta_teoria"})


def test_los_turnos_sin_motor_no_copian_el_turno_anterior(tmp_path: Path):
    api = _api(tmp_path, ModeloIntenciones())
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    _responder(api, ENTRADAS_MALLAS[0])
    _responder(api, "¿qué me pides?")
    _responder(api, "¿qué es una supermalla?")
    api.post_pista()
    api.post_iniciar({"problema": OHM, "modo": "aprender"})
    _responder(api, "Me dan el voltaje y la resistencia, necesito la corriente")
    _responder(api, "¿qué dice la ley de ohm?")
    turnos = [e["datos"] for e in _de_tipo(_eventos(api, codigo), "turno")]

    assert turnos[0]["motor"]["respondio"] and turnos[0]["motor"]["correcto"] is True
    paso = turnos[1]
    assert paso["ruta"] == "ambiguo" and paso["intencion"] == {"etiqueta": "pregunta_paso",
                                                               "modelo": "pregunta_paso", "ms": paso["intencion"]["ms"]}
    assert paso["motor"]["respondio"] is False and paso["motor"]["correcto"] is None
    assert paso["motor"]["codigo"] is None and paso["tutor"]["texto_base"].startswith("Lo que te pido")
    teoria = turnos[2]
    assert teoria["motor"]["respondio"] is False and teoria["teoria"]["encontro"] is True
    assert teoria["teoria"]["fichas"] and teoria["tutor"]["texto_base"]
    pista = turnos[3]
    assert pista["alumno"]["boton"] is True and pista["composicion"] is None and pista["ruta"] == "motor"
    assert pista["intencion"]["etiqueta"] == "ayuda" and pista["motor"]["pista"] is True
    # en un paso de concepto la duda de teoría se atiende como pista, pero se registra lo que dijo el modelo
    concepto = turnos[5]
    assert concepto["motor"]["paso_tipo"] == "concept"
    assert concepto["intencion"]["etiqueta"] == "ayuda" and concepto["intencion"]["modelo"] == "pregunta_teoria"


def test_seq_por_alumno_con_varios_en_el_mismo_arranque(tmp_path: Path):
    api = _api(tmp_path)
    a, b = generar(2)
    _entrar(api, codigo=a)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    _responder(api, ENTRADAS_MALLAS[0])
    _entrar(api, codigo=b)  # entra otro código sin cerrar sesión
    api.post_eventos({"eventos": [{"tipo": "clic", "datos": {"control": "modo", "modo": "practicar"}}]})
    api.post_salir({"motivo": "boton"})
    _entrar(api, acepta=None, codigo=a)
    api.post_salir({"motivo": "boton"})

    for codigo in (a, b):
        eventos = _eventos(api, codigo)
        assert [e["seq"] for e in eventos] == list(range(1, len(eventos) + 1))
        assert {e["arranque"] for e in eventos} == {api.registro.arranque}
    tipos_a = [e["tipo"] for e in _eventos(api, a)]
    assert tipos_a[-2:] == ["sesion_iniciada", "sesion_terminada"]
    pausa = [e for e in _eventos(api, a) if e["tipo"] in ("intento_pausado", "sesion_terminada")][:2]
    assert [e["tipo"] for e in pausa] == ["intento_pausado", "sesion_terminada"]
    assert pausa[0]["datos"]["motivo"] == pausa[1]["datos"]["motivo"] == "cambio"


def test_eventos_de_la_interfaz(tmp_path: Path):
    api = _api(tmp_path)
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    hace_5s = time.time() * 1000 - 5000
    st, r = api.post_eventos({"mono_envio": 10_000, "eventos": [
        {"tipo": "ventana", "datos": {"estado": "desenfocada"}, "ms": hace_5s, "mono": 9_000},
        {"tipo": "copiar", "datos": {"origen": "tutor", "texto": "llámame al 55 1234 5678" + "x" * 600}},
        {"tipo": "clic", "datos": {"control": "pista", "anidado": {"no": "va"}}},
        {"tipo": "turno", "datos": {"alumno": {"texto": "inventado"}}},
        {"tipo": "dibujo", "datos": "no es objeto"},
        "no es evento",
    ]})
    assert st == 200 and r["registrados"] == 3
    ui = [e for e in _eventos(api, codigo) if e["origen"] == "ui"]
    assert [e["tipo"] for e in ui] == ["ventana", "copiar", "clic"]
    assert all(e["intento"] == f"{MALLAS}:aprender" for e in ui)
    # la hora es la del evento y `mono` queda en el reloj del arranque
    assert ui[0]["ms"] == round(hace_5s)
    assert abs(datetime.fromisoformat(ui[0]["t"]).timestamp() * 1000 - hace_5s) < 2
    assert ui[0]["mono"] <= api.registro.mono_ahora() - 1000 + 1
    assert ui[1]["datos"]["texto"].startswith("llámame al [teléfono]") and len(ui[1]["datos"]["texto"]) <= 500
    assert ui[2]["datos"] == {"control": "pista"}
    assert api.post_eventos({"eventos": "no es lista"})[0] == 400


def test_la_composicion_no_guarda_datos_personales_tecleados(tmp_path: Path):
    api = _api(tmp_path)
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    tecleado = [[i * 100, "insertText", c, i + 1] for i, c in enumerate("2024640123")]
    _responder(api, "mi boleta es 2024640123", composicion={**COMPOSICION, "eventos": tecleado})
    _responder(api, "dos", composicion={**COMPOSICION, "eventos": [[0, "paste", "de juan@ipn.mx", 0]]})
    _responder(api, "VX, R1, R2 y VY", composicion={"teclas": "muchas", "eventos": [["x", 1, 2], [5, 7, "a", 1]]})
    turnos = [e["datos"] for e in _de_tipo(_eventos(api, codigo), "turno")]

    assert turnos[0]["alumno"]["texto"] == "mi boleta es [número]"
    assert turnos[0]["composicion"]["teclas_sin_texto"] is True
    assert {ev[2] for ev in turnos[0]["composicion"]["eventos"]} == {""}
    assert turnos[1]["composicion"]["eventos"] == [[0, "paste", "de [correo]", 0]]
    assert turnos[2]["composicion"]["teclas"] is None and turnos[2]["composicion"]["eventos"] == []


def test_pausar_retomar_y_cerrar(tmp_path: Path):
    api = _api(tmp_path)
    codigo = _entrar(api)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    _responder(api, ENTRADAS_MALLAS[0])
    api.post_iniciar({"problema": OHM, "modo": "practicar"})
    api.post_salir({"motivo": "inactividad"})
    _entrar(api, acepta=None, codigo=codigo)
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    eventos = [e for e in _eventos(api, codigo) if e["tipo"].startswith(("intento", "sesion"))]

    assert [(e["tipo"], e["intento"]) for e in eventos] == [
        ("sesion_iniciada", None),
        ("intento_iniciado", f"{MALLAS}:aprender"),
        ("intento_pausado", f"{MALLAS}:aprender"),
        ("intento_iniciado", f"{OHM}:practicar"),
        ("intento_pausado", f"{OHM}:practicar"),
        ("sesion_terminada", None),
        ("sesion_iniciada", None),
        ("intento_iniciado", f"{MALLAS}:aprender"),
    ]
    assert eventos[2]["datos"]["motivo"] == "otro_problema"
    assert eventos[4]["datos"]["motivo"] == eventos[5]["datos"]["motivo"] == "inactividad"
    retomado = eventos[-1]["datos"]
    assert retomado["retomado"] is True and retomado["segundos_activos"] == eventos[2]["datos"]["segundos_activos"]
    assert retomado["paso_id"] == "mesh_count"


class ServidorFalso:
    estado = "cargando"
    vivo = True
    ms_carga = 0
    variante_cpu = ""
    hilos = 4
    ctx = 4096
    ruta_modelo = Path("modelo-de-prueba.gguf")


def test_el_estado_del_modelo_se_registra_al_cambiar(tmp_path: Path):
    api = _api(tmp_path)
    api.servidor_modelo = ServidorFalso()
    codigo = _entrar(api)
    api.registrar_estado_modelo()
    api.servidor_modelo.estado, api.servidor_modelo.ms_carga = "listo", 8100
    api.servidor_modelo.variante_cpu = "haswell"
    api.registrar_estado_modelo()
    api.registrar_estado_modelo()
    api.servidor_modelo.vivo = False  # el proceso se cayó
    api.get_estado()
    eventos = _eventos(api, codigo)

    assert _de_tipo(eventos, "sesion_iniciada")[0]["datos"]["modelo"]["estado"] == "cargando"
    cambios = [e["datos"] for e in _de_tipo(eventos, "modelo_estado")]
    assert [c["estado"] for c in cambios] == ["listo", "error"]
    assert cambios[0]["ms_carga"] == 8100 and cambios[0]["variante_cpu"] == "haswell" and cambios[0]["ctx"] == 4096


def _recorrer_mallas(api: ApiTutor, anotar: bool) -> list[dict]:
    api.post_iniciar({"problema": MALLAS, "modo": "aprender"})
    respuestas = []
    for texto in ENTRADAS_MALLAS:
        respuestas.append(_responder(api, texto))
        if anotar:
            api.anotar_pendiente(reposo=0)
    return respuestas


def test_la_anotacion_no_altera_el_flujo(tmp_path: Path):
    sin = _api(tmp_path / "sin")
    _entrar(sin)
    esperado = _recorrer_mallas(sin, anotar=False)

    transporte = ModeloFalso()
    con = _api(tmp_path / "con", transporte)
    codigo = _entrar(con)
    assert _recorrer_mallas(con, anotar=True) == esperado
    assert con.estado_alumno["cola_anotaciones"] == []

    eventos = _eventos(con, codigo)
    turnos = {e["seq"]: e for e in _de_tipo(eventos, "turno")}
    anotaciones = [e["datos"] for e in _de_tipo(eventos, "anotacion")]
    assert transporte.anotaciones == len(anotaciones)
    assert [a["tipo"] for a in anotaciones] == ["justificacion", "verificacion", "verificacion", "metacognicion"]
    for a in anotaciones:
        turno = turnos[a["seq_turno"]]
        assert a["arranque_turno"] == turno["arranque"] and a["rubrica"] == RUBRICA and a["idea_esperada"]
    assert turnos[anotaciones[0]["seq_turno"]]["datos"]["alumno"]["texto"] == ENTRADAS_MALLAS[8]
    assert "rama compartida" in anotaciones[0]["idea_esperada"]


def test_la_anotacion_espera_al_modelo_y_reintenta_una_vez(tmp_path: Path):
    transporte = ModeloFalso(anotacion_valida=False)
    api = _api(tmp_path, transporte)
    codigo = _entrar(api)
    api.post_iniciar({"problema": OHM, "modo": "aprender"})
    _responder(api, "Me dan el voltaje y la resistencia, necesito la corriente")
    _responder(api, "la ley de ohm")
    assert len(api.estado_alumno["cola_anotaciones"]) == 1

    assert api.anotar_pendiente() is False  # el modelo acaba de redactar
    api._ultimo_uso_modelo -= 30
    api._peticiones_alumno = 1
    assert api.anotar_pendiente() is False  # hay una petición del alumno en curso
    api._peticiones_alumno = 0
    assert transporte.anotaciones == 0

    assert api.anotar_pendiente() is False and api.estado_alumno["cola_anotaciones"][0]["fallos"] == 1
    assert api.anotar_pendiente() is False and api.estado_alumno["cola_anotaciones"] == []
    anotacion = _de_tipo(_eventos(api, codigo), "anotacion")[0]["datos"]
    assert transporte.anotaciones == 2 and anotacion["tipo"] == "concepto" and anotacion["rubrica"] is None


def test_equipo_y_huella_del_modelo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    info = equipo.info_equipo(tmp_path, "navegador", {"ancho": 800}, "")
    assert info["modo"] == "navegador" and info["pantalla"] == {"ancho": 800} and info["variante_cpu"] is None
    assert info["nucleos_logicos"] >= 1 and info["ram_total_gb"] > 0 and info["disco_libre_gb"] > 0

    monkeypatch.setattr(equipo, "obtener_carpeta_app", lambda: tmp_path / "app")
    modelo = tmp_path / "modelo.gguf"
    modelo.write_bytes(b"pesos del modelo" * 1000)
    assert equipo.versiones(modelo)["modelo_sha256"] is None  # entrar nunca lee el modelo
    huella = hashlib.sha256(modelo.read_bytes()).hexdigest()[:12]
    assert equipo.huella_modelo(modelo) == huella
    assert equipo.versiones(modelo) == {"app": equipo.__version__, "banco_hash": equipo.banco_hash(),
                                        "prompts_hash": equipo.PROMPTS_HASH, "modelo": "modelo.gguf",
                                        "modelo_sha256": huella}
    assert huella in (tmp_path / "app" / "huellas.json").read_text("utf-8")
