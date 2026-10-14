"""Servidor HTTP local del Tutor Socrático."""
from __future__ import annotations

import json
import logging
import mimetypes
import secrets
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from escritorio.api import ApiTutor
from escritorio.config import TIMEOUT_LATIDO_SERVIDOR
from escritorio.rutas import RAIZ

logger = logging.getLogger("tutor_socratico.servidor")

# los tipos no se toman del registro de Windows, que a veces da text/plain para .js
TIPOS = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
}
# de problemas/ sólo se sirven los dibujos: los .txt traen la solución
IMAGENES = {".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp"}
MAX_CUERPO = 2 * 1024 * 1024


class ManejadorHTTP(BaseHTTPRequestHandler):
    """Manejador de peticiones estáticas y endpoints de la API local."""

    server: ServidorTutor

    def log_message(self, fmt: str, *args) -> None:
        logger.debug(fmt, *args)

    def _responder_json(self, codigo: int, datos: dict) -> None:
        cuerpo = json.dumps(datos, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(cuerpo)

    def _responder_error(self, codigo: int, mensaje: str) -> None:
        self._responder_json(codigo, {"error": mensaje})

    def _token_valido(self, valor: str | None) -> bool:
        return bool(valor) and secrets.compare_digest(valor, self.server.token)

    def _verificar_token(self, parsed, estatico: bool) -> bool:
        """La API sólo acepta el encabezado: un sitio ajeno no puede ponerlo sin pasar por CORS.
        Los estáticos aceptan además ?t= (la URL inicial) y la cookie (las imágenes)."""
        if self._token_valido(self.headers.get("X-Tutor-Token")):
            return True
        if not estatico:
            return False
        if self._token_valido(parse_qs(parsed.query).get("t", [""])[0]):
            return True
        for parte in self.headers.get("Cookie", "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "tutor_token" and self._token_valido(v):
                return True
        return False

    def _leer_json_body(self) -> dict:
        try:
            longitud = int(self.headers.get("Content-Length", 0))
            if longitud <= 0 or longitud > MAX_CUERPO:
                return {}
            datos = json.loads(self.rfile.read(longitud).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            logger.warning("Cuerpo JSON ilegible: %s", e)
            return {}
        return datos if isinstance(datos, dict) else {}

    def _atender_api(self, manejador, *args) -> None:
        # el estado del alumno se toca desde un solo hilo a la vez
        try:
            with self.server.api.lock_estado:
                resultado = manejador(*args)
        except Exception:
            logger.exception("Error atendiendo %s", self.path)
            self._responder_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Error interno del tutor.")
            return
        status, datos = resultado if isinstance(resultado, tuple) else (HTTPStatus.OK, resultado)
        self._responder_json(status, datos)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        api = self.server.api
        rutas = {
            "/api/estado": api.get_estado,
            "/api/problemas": api.get_problemas,
            "/api/consentimiento": api.get_consentimiento,
            "/api/cuestionario/pendiente": api.get_cuestionario_pendiente,
        }
        es_api = parsed.path.startswith("/api/")
        if not self._verificar_token(parsed, estatico=not es_api):
            self._responder_error(HTTPStatus.FORBIDDEN, "Acceso no autorizado: token inválido o ausente.")
            return
        if not es_api:
            self._servir_estatico(parsed)
        elif parsed.path in rutas:
            self._atender_api(rutas[parsed.path])
        else:
            self._responder_error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not self._verificar_token(parsed, estatico=False):
            self._responder_error(HTTPStatus.FORBIDDEN, "Acceso no autorizado: token inválido o ausente.")
            return

        api = self.server.api
        ruta = parsed.path
        body = self._leer_json_body()
        if ruta == "/api/latido":
            # sin el candado: un turno largo del modelo no debe atrasar el latido
            self.server.actualizar_latido()
            self._responder_json(HTTPStatus.OK, {"ok": True, "modelo_estado": api.estado_modelo()})
            return
        rutas = {
            "/api/entrar": api.post_entrar,
            "/api/consentimiento": api.post_consentimiento,
            "/api/salir": api.post_salir,
            "/api/iniciar": api.post_iniciar,
            "/api/responder": api.post_responder,
            "/api/confianza": api.post_confianza,
            "/api/pista": api.post_pista,
            "/api/eventos": api.post_eventos,
            "/api/exportar": api.post_exportar,
            "/api/abrir_carpeta": api.post_abrir_carpeta,
            "/api/cuestionario/respuesta": api.post_cuestionario_respuesta,
        }
        if ruta in rutas:
            self._atender_api(rutas[ruta], body)
        else:
            self._responder_error(HTTPStatus.NOT_FOUND, "Ruta no encontrada.")

    def _servir_estatico(self, parsed) -> None:
        ruta = unquote(parsed.path).lstrip("/")
        if ruta.startswith("img/"):
            base = self.server.carpeta_problemas
            archivo = base / ruta[4:]
            if archivo.suffix.lower() not in IMAGENES:
                self._responder_error(HTTPStatus.NOT_FOUND, "Archivo no encontrado.")
                return
        else:
            base = self.server.carpeta_web
            archivo = base / (ruta or "index.html")

        try:
            archivo = archivo.resolve()
            valido = archivo.is_relative_to(base) and archivo.is_file()
        except (OSError, ValueError):
            valido = False
        if not valido:
            self._responder_error(HTTPStatus.NOT_FOUND, "Archivo no encontrado.")
            return

        tipo = TIPOS.get(archivo.suffix.lower()) or mimetypes.guess_type(archivo.name)[0] or "application/octet-stream"
        try:
            contenido = archivo.read_bytes()
        except OSError as e:
            logger.error("Error sirviendo archivo %s: %s", archivo, e)
            self._responder_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Error de lectura.")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(contenido)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if archivo.name == "index.html":
            # las <img> no pueden mandar el encabezado del token
            self.send_header("Set-Cookie", f"tutor_token={self.server.token}; Path=/; HttpOnly; SameSite=Strict")
        self.end_headers()
        self.wfile.write(contenido)


class ServidorTutor(ThreadingHTTPServer):
    """Servidor HTTP del tutor con token por arranque y vigilancia de latidos."""

    def __init__(
        self,
        api: ApiTutor,
        puerto: int = 0,
        token: str | None = None,
        carpeta_web: Path | None = None,
        carpeta_problemas: Path | None = None,
    ):
        super().__init__(("127.0.0.1", puerto), ManejadorHTTP)
        self.api = api
        self.token = token or secrets.token_urlsafe(16)
        self.carpeta_web = (carpeta_web or (RAIZ / "web")).resolve()
        self.carpeta_problemas = (carpeta_problemas or (RAIZ / "problemas")).resolve()
        self.puerto_real = int(self.server_address[1])
        self.ultimo_latido = time.time()
        self._vigilante_activo = False

    @property
    def url_inicio(self) -> str:
        return f"http://127.0.0.1:{self.puerto_real}/?t={self.token}"

    def actualizar_latido(self) -> None:
        self.ultimo_latido = time.time()

    def iniciar_vigilante_latidos(self, limite: float = TIMEOUT_LATIDO_SERVIDOR) -> None:
        """En modo navegador no hay ventana que cerrar: el servidor sale cuando dejan de llegar latidos."""
        self._vigilante_activo = True
        self.ultimo_latido = time.time()

        def _vigilar():
            while self._vigilante_activo:
                time.sleep(min(5.0, limite / 2))
                if time.time() - self.ultimo_latido > limite:
                    logger.info("Cierre de servidor por falta de latidos.")
                    self.shutdown()
                    break

        threading.Thread(target=_vigilar, daemon=True).start()

    def detener(self) -> None:
        self._vigilante_activo = False
        self.shutdown()
        self.server_close()
