"""Control de ciclo de vida de la aplicación, instancia única y ventana pywebview."""
from __future__ import annotations

import logging
import os
import sys
import threading
import webbrowser
from pathlib import Path

import psutil

from escritorio.api import ApiTutor
from escritorio.modelo import ServidorModelo
from escritorio.rutas import (
    buscar_llama,
    buscar_modelo,
    configurar_logging,
    obtener_carpeta_app,
)
from escritorio.servidor import ServidorTutor

logger = logging.getLogger("tutor_socratico.ventana")


def _es_instancia_viva(contenido: str) -> bool:
    """El archivo guarda PID y hora de creación: un PID reciclado por Windows no cuenta como instancia."""
    try:
        pid_txt, creado_txt = contenido.split()
        return abs(psutil.Process(int(pid_txt)).create_time() - float(creado_txt)) < 1.0
    except (ValueError, psutil.Error):
        return False


def asegurar_instancia_unica(ruta_pid: Path | None = None) -> bool:
    """Verifica el archivo PID para garantizar una sola instancia activa."""
    ruta_pid = ruta_pid or obtener_carpeta_app() / "instancia.pid"
    try:
        if _es_instancia_viva(ruta_pid.read_text(encoding="utf-8")):
            mostrar_aviso_instancia_abierta()
            return False
    except OSError:
        pass
    yo = psutil.Process()
    ruta_pid.parent.mkdir(parents=True, exist_ok=True)
    ruta_pid.write_text(f"{yo.pid} {yo.create_time()}", encoding="utf-8")
    return True


def liberar_instancia(ruta_pid: Path | None = None) -> None:
    """Elimina el archivo PID al salir, si es de esta instancia."""
    ruta_pid = ruta_pid or obtener_carpeta_app() / "instancia.pid"
    try:
        if ruta_pid.read_text(encoding="utf-8").split()[0] == str(os.getpid()):
            ruta_pid.unlink()
    except (OSError, IndexError):
        pass


def mostrar_aviso_instancia_abierta() -> None:
    """Muestra un mensaje al usuario indicando que el tutor ya está abierto."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "El tutor ya está abierto", "Tutor socrático", 0x40)
            return
        except Exception:
            pass
    logger.warning("El tutor ya está abierto.")


def iniciar_modelo_segundo_plano(servidor_modelo: ServidorModelo, api: ApiTutor) -> None:
    """Inicia el modelo de lenguaje en un hilo secundario sin congelar la interfaz."""
    def _tarea():
        servidor_modelo.arrancar()
        if servidor_modelo.estado == "listo":
            api.conectar_modelo(servidor_modelo)

    hilo = threading.Thread(target=_tarea, daemon=True)
    hilo.start()


_WEBVIEW2 = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


def version_webview2() -> str | None:
    """Versión del runtime de WebView2, o None si no está. Sin él, pywebview no falla: cae en silencio
    a MSHTML (Internet Explorer), donde la interfaz no funciona."""
    if sys.platform != "win32":
        return None
    import winreg
    claves = [
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{_WEBVIEW2}"),
    ]
    for raiz, ruta in claves:
        try:
            with winreg.OpenKey(raiz, ruta) as clave:
                version, _ = winreg.QueryValueEx(clave, "pv")
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return str(version)
    return None


def arrancar_aplicacion(
    forzar_navegador: bool = False,
    sin_modelo: bool = False,
    carpeta_datos: str | Path | None = None,
    puerto: int = 0,
) -> int:
    """Punto de entrada principal para la ejecución de la aplicación de escritorio."""
    configurar_logging()
    logger.info("Iniciando aplicación de escritorio...")

    if not asegurar_instancia_unica():
        return 1

    servidor_modelo: ServidorModelo | None = None
    api = ApiTutor(carpeta_datos=carpeta_datos)

    if not sin_modelo:
        ruta_mod = buscar_modelo()
        ruta_srv = buscar_llama()
        if ruta_mod and ruta_srv.exists():
            carpeta_app = obtener_carpeta_app()
            servidor_modelo = ServidorModelo(
                ruta_modelo=ruta_mod,
                ruta_servidor=ruta_srv,
                ruta_log=carpeta_app / "registro-modelo.log",
                ruta_pid=carpeta_app / "modelo.pid",
            )
            api.servidor_modelo = servidor_modelo
            iniciar_modelo_segundo_plano(servidor_modelo, api)
        else:
            logger.warning("Modelo (%s) o llama-server no encontrados; se ejecutará sin modelo.", ruta_mod)

    servidor_http = ServidorTutor(api=api, puerto=puerto)
    hilo_http = threading.Thread(target=servidor_http.serve_forever, daemon=True)
    hilo_http.start()
    logger.info("Servidor disponible en el puerto %d", servidor_http.puerto_real)
    url = servidor_http.url_inicio

    try:
        usar_navegador = forzar_navegador
        if not usar_navegador and version_webview2() is None:
            logger.warning("WebView2 no está instalado; se abre el navegador.")
            usar_navegador = True
        if not usar_navegador:
            try:
                import webview
                webview.create_window("Tutor socrático", url, width=1100, height=760, min_size=(820, 600))
                webview.start(gui="edgechromium", storage_path=str(obtener_carpeta_app() / "webview"))
            except Exception as e:
                logger.warning("No se pudo abrir la ventana (%s); se abre el navegador.", e)
                usar_navegador = True
        if usar_navegador:
            servidor_http.iniciar_vigilante_latidos()
            webbrowser.open(url)
            while hilo_http.is_alive():
                hilo_http.join(timeout=1.0)
    finally:
        logger.info("Cerrando aplicación y liberando recursos...")
        try:
            with api.lock_estado:
                api.post_salir({"motivo": "cierre"})
        except Exception:
            logger.exception("Error al guardar el estado al cerrar")
        try:
            servidor_http.detener()
        except Exception:
            pass
        if servidor_modelo:
            try:
                servidor_modelo.detener()
            except Exception:
                pass
        liberar_instancia()

    return 0
