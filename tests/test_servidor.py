"""Pruebas del servidor local HTTP y la API del Tutor Socrático."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import httpx
import psutil
import pytest

from escritorio import __version__
from escritorio.alumnos import generar, normalizar
from escritorio.api import ApiTutor
from escritorio.cifrado import caja_estado
from escritorio.config import MODELO_ARCHIVO, PASOS_CON_CONFIANZA
from escritorio.conversacion import Conversador
from escritorio.rutas import buscar_modelo
from escritorio.servidor import ServidorTutor
from escritorio.ventana import _es_instancia_viva
from tutor.engine import CONFIANZA_KINDS
from tutor.llm import LLM

MALLAS = "mallas-01-caso1-malla-real"
OHM = "ohm-01-ley-de-ohm"
# un alumno real resolviendo MALLAS en Aprender, con un error y su justificación
ENTRADAS_MALLAS = [
    "Me dan dos fuentes de 100 V y tres resistencias, necesito V, I y P de cada una",
    "dos",
    "VX, R1, R2 y VY",
    "VY, R2 y R3",
    "R2 y VY",
    "I1 + I2",
    "2 A",
    "I1 - I2",
    "porque las corrientes van en sentidos opuestos en esa rama",
    "I1(R1 + R2) - I2R2 = VX - VY",
    "50I2 - 20I1 = 100 V",
    "I1 = 1.82 A, I2 = 2.73 A",
    "R1: I = 1.82 A, V = 18.2 V, P = 33.1 W",
    "R2: I1 - I2 = 0.91 A, 18.2 V, 16.6 W",
    "R3: 2.73 A, 81.9 V, 223.6 W",
    "con el balance de potencias",
    "PE = 273.3 W y PC = 273.2 W",
    "Me equivoqué al sumar las corrientes de la resistencia compartida, ahora sé que se restan",
]


class MockTransport(httpx.BaseTransport):
    """Transporte HTTP falso para simular respuestas del modelo de lenguaje."""

    def __init__(self):
        super().__init__()
        self.peticiones = 0

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        self.peticiones += 1
        cuerpo_req = json.loads(request.content.decode("utf-8")) if request.content else {}
        messages = cuerpo_req.get("messages", [])
        ultimo = messages[-1]["content"] if messages else ""

        if "clasificacion_intencion" in str(cuerpo_req.get("response_format", "")):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": json.dumps({"intencion": "respuesta"})}}],
                    "usage": {"prompt_tokens": 15, "completion_tokens": 5},
                    "timings": {"cache_n": 0, "predicted_per_second": 30.0},
                },
            )

        # la guardia rechaza esta voz (agrega un veredicto): el turno sale con el mensaje base
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": f"Excelente avance. {ultimo[:30]}"}}],
                "usage": {"prompt_tokens": 30, "completion_tokens": 15},
                "timings": {"cache_n": 0, "predicted_per_second": 25.0},
            },
        )


def _levantar(carpeta_datos: Path, transporte: MockTransport | None = None):
    llm_falso = LLM(base_url="http://modelo-falso", transport=transporte or MockTransport())
    api = ApiTutor(carpeta_datos=carpeta_datos, conversador=Conversador(llm=llm_falso))
    servidor = ServidorTutor(api=api, puerto=0)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return api, servidor


@pytest.fixture
def servidor_prueba(tmp_path: Path):
    """Levanta un servidor real en un puerto efímero con almacenamiento temporal."""
    transporte = MockTransport()
    api, servidor = _levantar(tmp_path / "datos", transporte)
    yield {
        "servidor": servidor,
        "base_url": f"http://127.0.0.1:{servidor.puerto_real}",
        "token": servidor.token,
        "carpeta_datos": tmp_path / "datos",
        "api": api,
        "transporte": transporte,
    }
    servidor.detener()


@pytest.fixture
def cliente(servidor_prueba):
    with httpx.Client(base_url=servidor_prueba["base_url"],
                      headers={"X-Tutor-Token": servidor_prueba["token"]}) as c:
        yield c


def _entrar(client: httpx.Client, acepta: bool = True) -> str:
    codigo = generar(1)[0]
    assert client.post("/api/entrar", json={"codigo": codigo}).status_code == 200
    client.post("/api/consentimiento", json={"acepta": acepta})
    return codigo


def _turno(client: httpx.Client, texto: str, confianza: int | None = 3) -> dict:
    """Manda una respuesta y, si el tutor pregunta la confianza, la contesta."""
    r = client.post("/api/responder", json={"texto": texto})
    assert r.status_code == 200, r.text
    res = r.json()
    if res.get("necesita_confianza"):
        r = client.post("/api/confianza", json={"valor": confianza, "ms": 1200})
        assert r.status_code == 200, r.text
        res = r.json()
        res["pidio_confianza"] = True
    return res


def _estado(servidor_prueba, codigo: str) -> dict:
    ruta = servidor_prueba["carpeta_datos"] / normalizar(codigo) / "estado.bin"
    caja = caja_estado(normalizar(codigo), servidor_prueba["api"].registro.sal)
    return json.loads(caja.decrypt(ruta.read_bytes()).decode("utf-8"))


def test_403_sin_token_y_acceso_con_token(servidor_prueba):
    base_url = servidor_prueba["base_url"]
    token = servidor_prueba["token"]

    with httpx.Client(base_url=base_url) as client:
        assert client.get("/api/estado").status_code == 403
        assert client.get("/").status_code == 403

        r_con_hdr = client.get("/api/estado", headers={"X-Tutor-Token": token})
        assert r_con_hdr.status_code == 200
        assert r_con_hdr.json()["version"] == __version__

        # la URL inicial deja la cookie para las imágenes
        r_con_query = client.get(f"/?t={token}")
        assert r_con_query.status_code == 200
        assert "tutor_token" in r_con_query.headers.get("Set-Cookie", "")
        assert "HttpOnly" in r_con_query.headers["Set-Cookie"]
        assert client.get(f"/img/{MALLAS}.svg").status_code == 200


def test_la_api_solo_acepta_el_encabezado(servidor_prueba):
    base_url = servidor_prueba["base_url"]
    token = servidor_prueba["token"]
    with httpx.Client(base_url=base_url) as client:
        assert client.get(f"/api/estado?t={token}").status_code == 403
        r = client.post("/api/entrar", json={"codigo": generar(1)[0]}, headers={"Cookie": f"tutor_token={token}"})
        assert r.status_code == 403
        assert client.get("/api/estado", headers={"X-Tutor-Token": token + "x"}).status_code == 403


def test_estaticos_solo_web_y_dibujos(cliente):
    assert cliente.get("/app.js").headers["Content-Type"].startswith("text/javascript")
    assert cliente.get(f"/img/{MALLAS}.svg").headers["Content-Type"] == "image/svg+xml"
    # los .txt del banco traen la solución
    assert cliente.get(f"/img/{MALLAS}.txt").status_code == 404
    for ruta in ("/img/../escritorio/api.py", "/img/..%2fescritorio%2fapi.py", "/..%2fescritorio%2fapi.py",
                 "/../problemas/" + MALLAS + ".txt", "/img/..%2fweb_x%2fa.svg"):
        assert cliente.get(ruta).status_code == 404, ruta
    assert cliente.get("/api/no-existe").status_code == 404


def test_texto_del_consentimiento(cliente):
    res = cliente.get("/api/consentimiento").json()
    assert res["version"] == "1.0"
    assert "Acepto" not in res["texto"] and "**Qué no se registra:**" in res["texto"]


def test_flujo_completo_mallas_en_aprender(servidor_prueba, cliente):
    codigo = generar(1)[0]
    assert cliente.post("/api/entrar", json={"codigo": "CODIGO-MALO"}).status_code == 400
    r_entrar = cliente.post("/api/entrar", json={"codigo": codigo})
    assert r_entrar.json()["requiere_consentimiento"] is True
    assert cliente.post("/api/consentimiento", json={"acepta": True}).json()["consentimiento"] is True

    p_mallas = next(p for p in cliente.get("/api/problemas").json()["problems"] if p["id"] == MALLAS)
    assert p_mallas["estados"]["aprender"] == "nuevo"

    r_ini = cliente.post("/api/iniciar", json={"problema": MALLAS, "modo": "aprender"})
    assert r_ini.status_code == 200 and r_ini.json()["retomado"] is False

    confianzas = 0
    for n, texto in enumerate(ENTRADAS_MALLAS):
        res = _turno(cliente, texto, confianza=None if n % 2 else 4)
        confianzas += bool(res.get("pidio_confianza"))
        assert res["terminado"] is (n == len(ENTRADAS_MALLAS) - 1), (n, texto, res["mensaje"])
    assert confianzas == 3
    assert len(res["indicadores"]) == 16

    # el intento terminado sale de los en curso y abrirlo otra vez empieza uno nuevo
    estado = _estado(servidor_prueba, codigo)
    assert estado["terminados"] == [f"{MALLAS}:aprender"]
    assert estado["intentos_en_curso"] == {}
    p_mallas = next(p for p in cliente.get("/api/problemas").json()["problems"] if p["id"] == MALLAS)
    assert p_mallas["estados"]["aprender"] == "terminado"
    assert cliente.post("/api/responder", json={"texto": "otra"}).status_code == 400
    assert cliente.post("/api/iniciar", json={"problema": MALLAS, "modo": "aprender"}).json()["retomado"] is False


def test_confianza_guardada_y_una_vez_por_paso(servidor_prueba, cliente):
    codigo = _entrar(cliente)
    cliente.post("/api/iniciar", json={"problema": OHM, "modo": "verificar"})

    # el motor no entiende la primera respuesta y sigue en el mismo paso: no vuelve a preguntar la confianza
    assert cliente.post("/api/responder", json={"texto": "la corriente es grande"}).json() == {"necesita_confianza": True}
    assert cliente.post("/api/confianza", json={"valor": 7}).status_code == 400
    assert cliente.post("/api/confianza", json={"valor": True}).status_code == 400
    r = cliente.post("/api/confianza", json={"valor": 5, "ms": 2300.4})
    assert r.status_code == 200 and not r.json()["terminado"]
    assert cliente.post("/api/confianza", json={"valor": 5}).status_code == 400

    res = _turno(cliente, "I = V/R = 20/10 = 2 A, P = V*I = 40 W")
    assert "pidio_confianza" not in res
    intento = _estado(servidor_prueba, codigo)["intentos_en_curso"][f"{OHM}:verificar"]
    assert intento["confianzas"] == [{"paso_id": "submit", "valor": 5, "ms": 2300}]
    assert intento["pasos_confianza"] == ["submit"]

    _turno(cliente, "La potencia entregada por la fuente PE = 20*2 = 40 W es igual a la consumida PC = 40 W")
    res = _turno(cliente, "1) Despejar la corriente. 2) Revisando unidades. 3) La ley de Ohm y la potencia.")
    assert res["terminado"] is True


def test_pista_y_pregunta_de_teoria_no_piden_confianza(servidor_prueba, cliente):
    codigo = _entrar(cliente)
    cliente.post("/api/iniciar", json={"problema": OHM, "modo": "verificar"})
    res = cliente.post("/api/pista", json={}).json()
    assert res["mensaje"] and res["terminado"] is False
    assert cliente.post("/api/responder", json={"texto": "no sé cómo empezar"}).json().get("mensaje")
    intento = _estado(servidor_prueba, codigo)["intentos_en_curso"][f"{OHM}:verificar"]
    assert intento["pasos_confianza"] == []
    assert intento["entradas"][0] == "pista"
    assert intento["chat"][1] == {"rol": "alumno", "texto": "Dame una pista"}


def test_reanudacion_tras_reinicio_servidor(servidor_prueba):
    carpeta_datos = servidor_prueba["carpeta_datos"]
    codigo = generar(1)[0]

    with httpx.Client(base_url=servidor_prueba["base_url"], headers={"X-Tutor-Token": servidor_prueba["token"]}) as c:
        c.post("/api/entrar", json={"codigo": codigo})
        c.post("/api/consentimiento", json={"acepta": True})
        c.post("/api/iniciar", json={"problema": MALLAS, "modo": "aprender"})
        for texto in ENTRADAS_MALLAS[:12]:
            _turno(c, texto)
        c.post("/api/salir", json={"motivo": "cierre"})
    servidor_prueba["servidor"].detener()

    api2, servidor2 = _levantar(carpeta_datos)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{servidor2.puerto_real}",
                          headers={"X-Tutor-Token": servidor2.token}) as c:
            r_entrar = c.post("/api/entrar", json={"codigo": codigo})
            assert r_entrar.json()["requiere_consentimiento"] is False

            res_ini = c.post("/api/iniciar", json={"problema": MALLAS, "modo": "aprender"}).json()
            assert res_ini["retomado"] is True
            assert len(res_ini["chat"]) == 1 + 2 * 12
            assert api2.sesion_activa.t0 == api2.t0_intento
            # el motor quedó en el mismo paso: lo que falta termina el problema
            for texto in ENTRADAS_MALLAS[12:]:
                res = _turno(c, texto)
            assert res["terminado"] is True
    finally:
        servidor2.detener()


def test_dos_alumnos_no_se_mezclan(servidor_prueba, cliente):
    cod_a = _entrar(cliente)
    cliente.post("/api/iniciar", json={"problema": MALLAS, "modo": "aprender"})
    _turno(cliente, ENTRADAS_MALLAS[0])

    # B entra sin que A cierre sesión: el intento de A se guarda y no queda activo
    cod_b = generar(1)[0]
    cliente.post("/api/entrar", json={"codigo": cod_b})
    cliente.post("/api/consentimiento", json={"acepta": False})
    assert cliente.post("/api/responder", json={"texto": "dos"}).status_code == 400
    p_mallas = next(p for p in cliente.get("/api/problemas").json()["problems"] if p["id"] == MALLAS)
    assert p_mallas["estados"]["aprender"] == "nuevo"
    assert cliente.get("/api/estado").json()["consentimiento"] is False

    cliente.post("/api/salir", json={"motivo": "boton"})
    cliente.post("/api/entrar", json={"codigo": cod_a})
    p_mallas_a = next(p for p in cliente.get("/api/problemas").json()["problems"] if p["id"] == MALLAS)
    assert p_mallas_a["estados"]["aprender"] == "en_curso"
    assert len(_estado(servidor_prueba, cod_a)["intentos_en_curso"][f"{MALLAS}:aprender"]["chat"]) == 3
    assert _estado(servidor_prueba, cod_b)["intentos_en_curso"] == {}


def test_problema_propio(cliente):
    _entrar(cliente)
    circuito_propio = "V1 a 0 12\nR1 a b 4\nR2 b 0 2\nMALLA 1: V1 R1 R2"

    r_propio = cliente.post(
        "/api/iniciar",
        json={"problema": "", "modo": "aprender", "propio": circuito_propio, "metodo": "mallas"},
    )
    assert r_propio.status_code == 200
    assert r_propio.json()["retomado"] is False
    r_otra_vez = cliente.post(
        "/api/iniciar",
        json={"problema": "", "modo": "aprender", "propio": circuito_propio, "metodo": "mallas"},
    )
    assert r_otra_vez.json()["retomado"] is True

    r_err = cliente.post(
        "/api/iniciar",
        json={"problema": "", "modo": "aprender", "propio": "INVALIDO", "metodo": "mallas"},
    )
    assert r_err.status_code == 400


def test_peticiones_invalidas(servidor_prueba, cliente):
    assert cliente.post("/api/iniciar", json={"problema": MALLAS}).status_code == 401
    _entrar(cliente)
    assert cliente.post("/api/iniciar", json={"problema": MALLAS, "modo": "examen"}).status_code == 400
    assert cliente.post("/api/iniciar", json={"problema": "no-existe", "modo": "aprender"}).status_code == 400
    assert cliente.post("/api/iniciar", json={"problema": "", "modo": "aprender"}).status_code == 400
    assert cliente.post("/api/responder", content=b"[1, 2]",
                        headers={"Content-Type": "application/json"}).status_code == 400
    assert cliente.post("/api/consentimiento", json={"acepta": "false"}).json()["consentimiento"] is False
    assert cliente.post("/api/exportar", json={}).status_code == 409

    def falla():
        raise RuntimeError("falla de prueba")
    servidor_prueba["api"].get_problemas = falla
    r = cliente.get("/api/problemas")
    assert r.status_code == 500 and r.json() == {"error": "Error interno del tutor."}


class ModeloCaido:
    """Un ServidorModelo cuyo proceso terminó."""
    estado = "listo"
    vivo = False


def test_modelo_caido_usa_los_mensajes_base(servidor_prueba, cliente):
    api, transporte = servidor_prueba["api"], servidor_prueba["transporte"]
    api.servidor_modelo = ModeloCaido()
    _entrar(cliente)
    r_ini = cliente.post("/api/iniciar", json={"problema": MALLAS, "modo": "aprender"}).json()
    assert r_ini["fuente"] == "plantilla"
    _turno(cliente, ENTRADAS_MALLAS[0])
    _turno(cliente, "no le entiendo, ¿me explicas qué es una malla?")
    assert transporte.peticiones == 0
    assert cliente.get("/api/estado").json()["modelo_estado"] == "error"
    assert cliente.post("/api/latido", json={}).json()["modelo_estado"] == "error"


def test_pasos_con_confianza_existen_en_el_motor():
    assert set(PASOS_CON_CONFIANZA) <= CONFIANZA_KINDS


def test_buscar_modelo_elige_el_configurado(tmp_path: Path):
    modelos = tmp_path / "vendor" / "modelos"
    modelos.mkdir(parents=True)
    for nombre in ("aaa-otro.gguf", MODELO_ARCHIVO, "zzz-otro.gguf"):
        (modelos / nombre).write_bytes(b"")
    assert buscar_modelo(tmp_path) == (modelos / MODELO_ARCHIVO).resolve()
    (modelos / MODELO_ARCHIVO).unlink()
    assert buscar_modelo(tmp_path) is None


def test_instancia_viva_no_confunde_un_pid_reciclado():
    yo = psutil.Process()
    assert _es_instancia_viva(f"{yo.pid} {yo.create_time()}")
    assert not _es_instancia_viva(f"{yo.pid} {yo.create_time() - 3600}")
    assert not _es_instancia_viva("12345")
    assert not _es_instancia_viva("")
