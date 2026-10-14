"""Pruebas del autodiagnóstico: no dependen de la máquina ni tocan sus carpetas reales."""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from escritorio import autodiagnostico
from escritorio.autodiagnostico import ejecutar_autodiagnostico


@pytest.fixture(autouse=True)
def entorno_aislado(tmp_path, monkeypatch):
    """Rutas de la máquina parchadas y un LOCALAPPDATA falso, para detectar escrituras en el real."""
    local_falso = tmp_path / "local_falso"
    monkeypatch.setenv("LOCALAPPDATA", str(local_falso))
    servidor = tmp_path / "componentes" / "llama-server.exe"
    servidor.parent.mkdir()
    servidor.write_bytes(b"")
    monkeypatch.setattr(autodiagnostico, "buscar_llama", lambda: servidor)
    monkeypatch.setattr(autodiagnostico, "buscar_modelo", lambda: None)

    logger = logging.getLogger("tutor_socratico")
    previos = list(logger.handlers)
    for h in previos:
        logger.removeHandler(h)
    yield
    for h in list(logger.handlers):
        logger.removeHandler(h)
        h.close()
    for h in previos:
        logger.addHandler(h)


@pytest.fixture
def carpeta_app(tmp_path):
    return tmp_path / "app"


def _informe(carpeta_app: Path) -> str:
    return (carpeta_app / "autodiagnostico.txt").read_text(encoding="utf-8")


def test_falla_si_no_hay_modelo(carpeta_app):
    assert ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app) == 1
    contenido = _informe(carpeta_app)
    assert "RESULTADO: FALLO" in contenido
    assert "No se encontró el archivo del modelo GGUF" in contenido
    assert "--- INFORMACIÓN DEL EQUIPO ---" in contenido


def test_falla_si_no_hay_servidor(carpeta_app, tmp_path, monkeypatch):
    monkeypatch.setattr(autodiagnostico, "buscar_llama", lambda: tmp_path / "no_existe.exe")
    assert ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app) == 1
    contenido = _informe(carpeta_app)
    assert "RESULTADO: FALLO" in contenido
    assert "No se encontró llama-server.exe" in contenido


def test_no_escribe_en_la_carpeta_actual(carpeta_app, tmp_path, monkeypatch):
    actual = tmp_path / "actual"
    actual.mkdir()
    monkeypatch.chdir(actual)
    ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app)
    assert list(actual.iterdir()) == []
    assert (carpeta_app / "autodiagnostico.txt").is_file()


def test_no_escribe_en_el_registro_real(carpeta_app, tmp_path):
    ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app)
    assert not (tmp_path / "local_falso").exists()
    assert (carpeta_app / "registro-app.log").is_file()


def test_libera_la_instancia_al_terminar(carpeta_app):
    ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app)
    assert not (carpeta_app / "instancia.pid").exists()


def test_se_niega_si_el_tutor_esta_abierto(carpeta_app, monkeypatch):
    class NoDebeArrancar:
        def __init__(self, *a, **k):
            raise AssertionError("no debe arrancar el modelo con el tutor abierto")

    monkeypatch.setattr(autodiagnostico, "ServidorModelo", NoDebeArrancar)
    vivo = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        carpeta_app.mkdir()
        pid_archivo = carpeta_app / "instancia.pid"
        contenido = f"{vivo.pid} {psutil.Process(vivo.pid).create_time()}"
        pid_archivo.write_text(contenido, encoding="utf-8")

        assert ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app) == 1
        assert "El tutor está abierto" in _informe(carpeta_app)
        assert "RESULTADO: FALLO" in _informe(carpeta_app)
        # la instancia del tutor no se toca
        assert pid_archivo.read_text(encoding="utf-8") == contenido
    finally:
        vivo.kill()
        vivo.wait()


def test_un_pid_reciclado_no_cuenta_como_instancia(carpeta_app):
    carpeta_app.mkdir()
    (carpeta_app / "instancia.pid").write_text(f"{psutil.Process().pid} 1.0", encoding="utf-8")
    ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app)
    assert "El tutor está abierto" not in _informe(carpeta_app)


# ------------------------------------------------------------------ diálogo con un servidor y una API falsos
class ModeloFalso:
    estado = "listo"
    variante_cpu = "variante-de-prueba"
    hilos = 2
    ctx = 4096

    def __init__(self, *a, **k):
        self.detenido = False

    def arrancar(self):
        return True

    def detener(self):
        self.detenido = True


class ApiFalsa:
    """Contesta con lo que dicte `respuestas`; el primer turno pide confianza si `con_confianza`."""
    respuestas: list[dict] = []
    con_confianza = False
    llamadas_confianza: list[dict] = []

    def __init__(self, carpeta_datos=None, **opciones):
        type(self).opciones = opciones
        self.banco = {"p-01": object()}
        self._turno = 0

    def conectar_modelo(self, servidor):
        pass

    def estado_modelo(self):
        return "listo"

    def post_entrar(self, datos):
        return 200, {}

    def post_consentimiento(self, datos):
        return 200, {}

    def post_iniciar(self, datos):
        return 200, {}

    def post_responder(self, datos):
        i, self._turno = self._turno, self._turno + 1
        if i == 0 and self.con_confianza:
            return 200, {"necesita_confianza": True}
        return 200, self.respuestas[i]

    def post_confianza(self, datos):
        type(self).llamadas_confianza.append(datos)
        return 200, self.respuestas[0]

    def post_salir(self, datos):
        return 200, {}


@pytest.fixture
def dialogo_falso(monkeypatch, tmp_path):
    modelo = tmp_path / "componentes" / "modelo.gguf"
    modelo.write_bytes(b"")
    monkeypatch.setattr(autodiagnostico, "buscar_modelo", lambda: modelo)
    monkeypatch.setattr(autodiagnostico, "ServidorModelo", ModeloFalso)
    monkeypatch.setattr(autodiagnostico, "ApiTutor", ApiFalsa)
    ApiFalsa.con_confianza = False
    ApiFalsa.llamadas_confianza = []

    def preparar(fuentes, textos=("uno", "dos", "tres")):
        ApiFalsa.respuestas = [{"mensaje": t, "fuente": f} for t, f in zip(textos, fuentes)]

    return preparar


def test_exito_con_un_mensaje_del_modelo(carpeta_app, dialogo_falso):
    dialogo_falso(["plantilla", "modelo", "plantilla"])
    assert ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app) == 0
    contenido = _informe(carpeta_app)
    assert "RESULTADO: EXITO" in contenido
    assert "mensaje: 'uno'" in contenido


def test_falla_si_ningun_mensaje_es_del_modelo(carpeta_app, dialogo_falso):
    dialogo_falso(["plantilla"] * 3)
    assert ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app) == 1
    assert "RESULTADO: FALLO" in _informe(carpeta_app)


def test_falla_si_un_mensaje_llega_vacio(carpeta_app, dialogo_falso):
    dialogo_falso(["modelo"] * 3, textos=("uno", "  ", "tres"))
    assert ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app) == 1
    assert "RESULTADO: FALLO" in _informe(carpeta_app)


def test_contesta_la_confianza_para_obtener_el_mensaje(carpeta_app, dialogo_falso):
    dialogo_falso(["modelo"] * 3)
    ApiFalsa.con_confianza = True
    assert ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app) == 0
    assert ApiFalsa.llamadas_confianza == [{"valor": autodiagnostico.CONFIANZA_PRUEBA, "ms": 1000}]
    assert "mensaje: 'uno'" in _informe(carpeta_app)


def test_el_informe_recorta_los_mensajes_a_80_caracteres(carpeta_app, dialogo_falso):
    dialogo_falso(["modelo"] * 3, textos=("x" * 200, "dos", "tres"))
    ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app)
    contenido = _informe(carpeta_app)
    assert "x" * 80 in contenido and "x" * 81 not in contenido


def test_la_variante_de_cpu_es_la_del_modelo_arrancado(carpeta_app, dialogo_falso):
    dialogo_falso(["modelo"] * 3)
    ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app)
    assert "variante_cpu: variante-de-prueba" in _informe(carpeta_app)


def test_no_usa_los_cuestionarios_del_programa(carpeta_app, dialogo_falso):
    dialogo_falso(["modelo"] * 3)
    ejecutar_autodiagnostico(carpeta_app_destino=carpeta_app)
    carpeta = ApiFalsa.opciones["carpeta_cuestionarios"]
    assert carpeta.name == "sin_cuestionarios" and not carpeta.exists()
