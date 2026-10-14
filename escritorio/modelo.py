"""Administrador del proceso local de llama-server.

Arranca el servidor en 127.0.0.1 con los parámetros fijados del sistema,
supervisa su ciclo de vida y limpia procesos huérfanos.
"""
from __future__ import annotations

import os
import re
import secrets
import socket
import subprocess
import time
from pathlib import Path

import httpx
import psutil

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CTX = 4096


def buscar_puerto_libre(host: str = "127.0.0.1") -> int:
    """Encuentra un puerto TCP libre en la interfaz indicada."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


class ServidorModelo:
    """Controla el proceso hijo llama-server y su estado de servicio."""

    def __init__(
        self,
        ruta_modelo: str | Path,
        ruta_servidor: str | Path = Path("vendor/llama/llama-server.exe"),
        ruta_log: str | Path = Path("registro-modelo.log"),
        ruta_pid: str | Path = Path("registro-modelo.pid"),
        hilos: int | None = None,
        puerto: int | None = None,
        timeout_carga: float = 180.0,
    ):
        self.ruta_modelo = Path(ruta_modelo).resolve()
        self.ruta_servidor = Path(ruta_servidor).resolve()
        self.ruta_log = Path(ruta_log).resolve()
        self.ruta_pid = Path(ruta_pid).resolve()
        if hilos is None:
            nucleos = psutil.cpu_count(logical=False)
            self.hilos = nucleos if nucleos and nucleos > 0 else 2
        else:
            self.hilos = hilos
        self.puerto = puerto or buscar_puerto_libre()
        self.timeout_carga = timeout_carga
        self.ctx = CTX

        self.llave: str = secrets.token_urlsafe(24)
        self.estado: str = "detenido"
        self.ms_carga: int = 0
        self.variante_cpu: str = ""
        self.pid: int | None = None

        self._proceso: subprocess.Popen | None = None
        self._log_file = None

    @property
    def url_base(self) -> str:
        return f"http://127.0.0.1:{self.puerto}"

    @property
    def vivo(self) -> bool:
        return self._proceso is not None and self._proceso.poll() is None

    def arrancar(self) -> bool:
        """Termina huérfanos previos, inicia el proceso y espera /health."""
        self._limpiar_huerfanos()

        if not self.ruta_servidor.exists():
            self.estado = "error"
            return False
        if not self.ruta_modelo.exists():
            self.estado = "error"
            return False

        cmd = [
            str(self.ruta_servidor),
            "-m", str(self.ruta_modelo),
            "--host", "127.0.0.1",
            "--port", str(self.puerto),
            "-c", str(self.ctx),
            # voz, intención y anotación: con el contexto unificado, la tercera ranura no ocupa más memoria
            "-np", "3",
            "--kv-unified",
            "-t", str(self.hilos),
            "--cache-ram", "0",
            "--fit", "off",
            "--reasoning", "off",
            "--jinja",
            "--no-webui",
            "--no-slots",
            "-lv", "4",
        ]

        env = os.environ.copy()
        env["LLAMA_API_KEY"] = self.llave

        # se reescribe en cada arranque: con -lv 4 crece rápido, y la variante de CPU
        # debe leerse de este arranque
        self.ruta_log.parent.mkdir(parents=True, exist_ok=True)
        self._log_file = open(self.ruta_log, "wb")

        self.estado = "cargando"
        inicio = time.perf_counter()
        try:
            self._proceso = subprocess.Popen(
                cmd,
                env=env,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
            )
            self.pid = self._proceso.pid
            self.ruta_pid.write_text(str(self.pid), encoding="utf-8")
        except Exception:
            self.estado = "error"
            if self._log_file and not self._log_file.closed:
                self._log_file.close()
            return False

        listo = self._esperar_listo(inicio)
        if listo:
            self.estado = "listo"
            self.ms_carga = round((time.perf_counter() - inicio) * 1000)
            self.variante_cpu = self._leer_variante_cpu()
            return True

        self.estado = "error"
        self.detener()
        return False

    def _esperar_listo(self, inicio: float) -> bool:
        url_health = f"{self.url_base}/health"
        with httpx.Client(timeout=1.0) as client:
            while time.perf_counter() - inicio < self.timeout_carga:
                if self._proceso is not None and self._proceso.poll() is not None:
                    return False
                try:
                    resp = client.get(url_health)
                    if resp.status_code == 200:
                        data = resp.json()
                        if data.get("status") in ("ok", "ready"):
                            return True
                except (httpx.HTTPError, ValueError):
                    pass
                time.sleep(0.2)
        return False

    def _leer_variante_cpu(self) -> str:
        if not self.ruta_log.exists():
            return ""
        try:
            texto = self.ruta_log.read_text(encoding="utf-8", errors="replace")
            m = re.search(r"ggml-cpu-([a-z0-9]+)\.dll", texto, re.IGNORECASE)
            if m:
                return m.group(1).lower()
        except Exception:
            pass
        return ""

    def _limpiar_huerfanos(self) -> None:
        if self.ruta_pid.exists():
            try:
                pid_txt = self.ruta_pid.read_text(encoding="utf-8").strip()
                if pid_txt.isdigit():
                    pid = int(pid_txt)
                    if psutil.pid_exists(pid):
                        proc = psutil.Process(pid)
                        if self._es_mismo_ejecutable(proc):
                            self._matar_proceso(proc)
            except Exception:
                pass
            finally:
                try:
                    self.ruta_pid.unlink(missing_ok=True)
                except Exception:
                    pass

        for proc in psutil.process_iter(["pid", "name"]):
            try:
                if self._es_mismo_ejecutable(proc):
                    self._matar_proceso(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _es_mismo_ejecutable(self, proc: psutil.Process) -> bool:
        try:
            nombre = proc.name().lower()
            if nombre in ("llama-server.exe", "llama-server"):
                exe = proc.exe()
                if exe and Path(exe).resolve() == self.ruta_servidor:
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return False

    def _matar_proceso(self, proc: psutil.Process) -> None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except psutil.TimeoutExpired:
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    def detener(self) -> None:
        """Detiene el servidor y limpia descriptores y archivo PID."""
        if self._proceso is not None:
            try:
                self._proceso.terminate()
                try:
                    self._proceso.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._proceso.kill()
            except Exception:
                pass
            self._proceso = None

        if self.ruta_pid.exists():
            try:
                self.ruta_pid.unlink(missing_ok=True)
            except Exception:
                pass

        if self._log_file is not None and not self._log_file.closed:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

        self.estado = "detenido"

    def __enter__(self) -> ServidorModelo:
        self.arrancar()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.detener()
