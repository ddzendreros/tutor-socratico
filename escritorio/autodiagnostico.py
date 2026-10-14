"""Módulo de autodiagnóstico del Tutor Socrático.

Ejecuta una verificación técnica completa sin interfaz gráfica:
equipo, arranque del modelo, precalentamiento y un diálogo de 3 turnos
en una carpeta de datos temporal que se limpia al terminar.
Escribe autodiagnostico.txt en la carpeta de la app y sale con código 0 o 1.
"""
from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path

import psutil

from escritorio import __version__
from escritorio import equipo
from escritorio.alumnos import generar
from escritorio.api import ApiTutor
from escritorio.modelo import ServidorModelo
from escritorio.rutas import (
    buscar_llama,
    buscar_modelo,
    configurar_logging,
    obtener_carpeta_app,
)
from escritorio.ventana import _es_instancia_viva, liberar_instancia

logger = logging.getLogger("tutor_socratico.autodiagnostico")

# valor fijo con el que el autodiagnóstico contesta la pregunta de confianza
CONFIANZA_PRUEBA = 3
LARGO_MUESTRA = 80

TURNOS = [
    ("Turno 1 (Duda teórica)", "¿cómo se calcula la resistencia equivalente?"),
    ("Turno 2 (Petición de ayuda)", "pista"),
    ("Turno 3 (Pregunta de paso)", "¿cuál es el siguiente paso?"),
]


def _reservar_instancia(ruta_pid: Path) -> bool:
    """False si el tutor (o otro autodiagnóstico) ya está abierto; si no, reserva la instancia."""
    try:
        if _es_instancia_viva(ruta_pid.read_text(encoding="utf-8")):
            return False
    except OSError:
        pass
    yo = psutil.Process()
    ruta_pid.parent.mkdir(parents=True, exist_ok=True)
    ruta_pid.write_text(f"{yo.pid} {yo.create_time()}", encoding="utf-8")
    return True


def ejecutar_autodiagnostico(
    carpeta_app_destino: Path | None = None,
    timeout_carga: float = 180.0,
) -> int:
    """Ejecuta el autodiagnóstico completo y escribe autodiagnostico.txt en la carpeta de la app."""
    carpeta_app = Path(carpeta_app_destino) if carpeta_app_destino else obtener_carpeta_app()
    carpeta_app.mkdir(parents=True, exist_ok=True)
    configurar_logging(carpeta_app / "registro-app.log")
    logger.info("Iniciando autodiagnóstico...")

    ruta_informe = carpeta_app / "autodiagnostico.txt"
    ruta_instancia = carpeta_app / "instancia.pid"
    lineas: list[str] = []

    def log_inf(msg: str = "") -> None:
        lineas.append(msg)
        print(msg)

    def terminar(exito: bool) -> int:
        log_inf("")
        log_inf("RESULTADO: EXITO" if exito else "RESULTADO: FALLO")
        _escribir_informe(lineas, ruta_informe)
        return 0 if exito else 1

    log_inf("=== AUTODIAGNÓSTICO TUTOR SOCRÁTICO ===")
    log_inf(f"Fecha: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    log_inf(f"Versión de la aplicación: {__version__}")
    log_inf("")

    # el arranque del modelo termina cualquier llama-server de la misma ruta, incluido el del tutor abierto
    if not _reservar_instancia(ruta_instancia):
        log_inf("ERROR: El tutor está abierto. Ciérralo y vuelve a ejecutar el autodiagnóstico:")
        log_inf("al probar el modelo se cerraría el que está usando el tutor.")
        return terminar(False)

    try:
        return _diagnosticar(carpeta_app, timeout_carga, log_inf, terminar)
    finally:
        liberar_instancia(ruta_instancia)


def _diagnosticar(carpeta_app: Path, timeout_carga: float, log_inf, terminar) -> int:
    def escribir_equipo(variante_cpu: str) -> None:
        log_inf("--- INFORMACIÓN DEL EQUIPO ---")
        info = equipo.info_equipo(
            carpeta_datos=carpeta_app,
            modo="autodiagnostico",
            pantalla=None,
            variante_cpu=variante_cpu,
        )
        for k, v in info.items():
            log_inf(f"{k}: {v}")
        log_inf("")

    log_inf("--- COMPONENTES ---")
    ruta_mod = buscar_modelo()
    ruta_srv = buscar_llama()
    log_inf(f"Ruta llama-server: {ruta_srv} (existe: {ruta_srv.exists()})")
    log_inf(f"Ruta modelo: {ruta_mod} (existe: {bool(ruta_mod and ruta_mod.exists())})")
    log_inf("")

    if not ruta_srv.exists():
        escribir_equipo("")
        log_inf("ERROR: No se encontró llama-server.exe")
        return terminar(False)

    if not ruta_mod or not ruta_mod.exists():
        escribir_equipo("")
        log_inf("ERROR: No se encontró el archivo del modelo GGUF")
        return terminar(False)

    servidor_modelo: ServidorModelo | None = None
    exito = False

    with tempfile.TemporaryDirectory() as temp_dir:
        dir_datos_temp = Path(temp_dir) / "datos"
        dir_datos_temp.mkdir(parents=True, exist_ok=True)
        log_inf(f"Carpeta de datos temporal: {dir_datos_temp}")
        log_inf("")

        try:
            log_inf("--- CARGA DEL MODELO ---")
            servidor_modelo = ServidorModelo(
                ruta_modelo=ruta_mod,
                ruta_servidor=ruta_srv,
                ruta_log=carpeta_app / "registro-modelo-autodiagnostico.log",
                ruta_pid=carpeta_app / "modelo-autodiagnostico.pid",
                timeout_carga=timeout_carga,
            )
            t0_carga = time.perf_counter()
            arranco = servidor_modelo.arrancar()
            t_carga = round((time.perf_counter() - t0_carga) * 1000)

            log_inf(f"Arranque exitoso: {arranco}")
            log_inf(f"Tiempo de carga: {t_carga} ms ({t_carga/1000:.2f} s)")
            log_inf(f"Variante CPU detectada: {servidor_modelo.variante_cpu or 'no detectada'}")
            log_inf(f"Hilos de inferencia: {servidor_modelo.hilos}")
            log_inf(f"Contexto: {servidor_modelo.ctx}")
            log_inf("")
            escribir_equipo(servidor_modelo.variante_cpu)

            if not arranco:
                log_inf("ERROR: El servidor del modelo no respondió dentro del tiempo límite.")
                return terminar(False)

            log_inf("--- PRECALENTAMIENTO ---")
            # sin cuestionarios: uno que se aplique antes de un tema bloquearía el problema de prueba
            api = ApiTutor(carpeta_datos=dir_datos_temp,
                           carpeta_cuestionarios=Path(temp_dir) / "sin_cuestionarios")
            t0_precal = time.perf_counter()
            api.conectar_modelo(servidor_modelo)
            t_precal = round((time.perf_counter() - t0_precal) * 1000)
            log_inf(f"Tiempo de precalentamiento: {t_precal} ms ({t_precal/1000:.2f} s)")
            log_inf(f"Estado del modelo en API: {api.estado_modelo()}")

            if api.estado_modelo() != "listo":
                log_inf(f"ERROR: El estado del modelo es '{api.estado_modelo()}', se esperaba 'listo'.")
                return terminar(False)

            log_inf("")
            log_inf("--- DIÁLOGO DE PRUEBA (3 TURNOS) ---")
            codigo_prueba = generar(1)[0]
            log_inf(f"Código de prueba generado: {codigo_prueba}")

            st, res = api.post_entrar({"codigo": codigo_prueba})
            if st != 200:
                log_inf(f"ERROR: Fallo al ingresar ({st}): {res}")
                return terminar(False)

            api.post_consentimiento({"acepta": True})

            problemas = list(api.banco.keys())
            if not problemas:
                log_inf("ERROR: El banco de problemas está vacío.")
                return terminar(False)

            pid = problemas[0]
            log_inf(f"Iniciando problema: {pid}")
            st, res = api.post_iniciar({"problema": pid, "modo": "aprender"})
            if st != 200:
                log_inf(f"ERROR: Fallo al iniciar problema ({st}): {res}")
                return terminar(False)

            tiempos_turnos: list[int] = []
            mensajes: list[tuple[str, str]] = []
            for etiqueta, entrada in TURNOS:
                t0_turno = time.perf_counter()
                st, res = api.post_responder({"texto": entrada})
                if st == 200 and res.get("necesita_confianza"):
                    # el mensaje del tutor llega al contestar la confianza
                    st, res = api.post_confianza({"valor": CONFIANZA_PRUEBA, "ms": 1000})
                dt_ms = round((time.perf_counter() - t0_turno) * 1000)
                tiempos_turnos.append(dt_ms)

                if st != 200:
                    log_inf(f"ERROR en {etiqueta} ({st}): {res}")
                    return terminar(False)

                fuente = res.get("fuente", "desconocida")
                msg_txt = res.get("mensaje") or ""
                mensajes.append((fuente, msg_txt))
                log_inf(f"{etiqueta}: {dt_ms} ms ({dt_ms/1000:.2f} s) | fuente: {fuente} | caracteres: {len(msg_txt)}")
                log_inf(f"  mensaje: {msg_txt[:LARGO_MUESTRA]!r}")

            api.post_salir({"motivo": "boton"})
            log_inf("")
            log_inf("--- RESUMEN DE RENDIMIENTO ---")
            log_inf(f"Tiempo de carga: {t_carga} ms")
            log_inf(f"Tiempo de precalentamiento: {t_precal} ms")
            for i, ms in enumerate(tiempos_turnos, 1):
                log_inf(f"Turno {i}: {ms} ms ({ms/1000:.2f} s)")
            log_inf(f"Promedio de turnos: {sum(tiempos_turnos)/len(tiempos_turnos):.1f} ms")

            if any(not txt.strip() for _, txt in mensajes):
                log_inf("ERROR: Algún mensaje del tutor llegó vacío.")
            elif not any(fuente == "modelo" for fuente, _ in mensajes):
                log_inf("ERROR: Ningún mensaje lo redactó el modelo (todos salieron de las plantillas).")
            else:
                exito = True

        except Exception as e:
            logger.exception("Excepción durante el autodiagnóstico")
            log_inf(f"EXCEPCIÓN NO CONTROLADA: {type(e).__name__}: {e}")
            exito = False
        finally:
            if servidor_modelo:
                try:
                    servidor_modelo.detener()
                except Exception:
                    pass

    return terminar(exito)


def _escribir_informe(lineas: list[str], ruta: Path) -> None:
    contenido = "\n".join(lineas) + "\n"
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(contenido, encoding="utf-8")
    except OSError as e:
        logger.error("No se pudo escribir el informe en %s: %s", ruta, e)
