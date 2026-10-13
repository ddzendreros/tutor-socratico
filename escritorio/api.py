"""Manejador de la API REST local para el Tutor Socrático."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from tutor.circuit import CircuitError
from tutor.engine import MODES, Session, reconstruir
from tutor.problems import load_bank, parse_problem
from escritorio import __version__
from escritorio.alumnos import es_valido, formatear, normalizar
from escritorio.config import INACTIVIDAD_MIN, INTERVALO_LATIDO, PASOS_CON_CONFIANZA, VELOCIDAD_ESCRITURA_CPS
from escritorio.conversacion import Conversador, contexto_guardia, plantilla_para_intencion
from escritorio.modelo import ServidorModelo
from escritorio.rutas import RAIZ, obtener_carpeta_datos

logger = logging.getLogger("tutor_socratico.api")

TOPICS = {
    "ohm": "Ley de Ohm",
    "serie_paralelo": "Serie y paralelo",
    "mallas": "Método de mallas",
    "nodos": "Método de nodos",
}

# intenciones que se contestan con una plantilla sin que el motor evalúe nada
SIN_MOTOR = ("pregunta_paso", "animo", "fuera_de_tema", "charla")


def _describe(p) -> dict:
    c = p.circuit
    return {
        "id": p.id,
        "title": p.title,
        "topic": p.topic,
        "topicName": TOPICS.get(p.topic, p.topic),
        "level": p.level,
        "method": p.method,
        "statement": p.statement,
        "source": p.source,
        "elements": c.describe().splitlines(),
        "meshes": [f"Malla {k+1}: {', '.join(m)}" for k, m in enumerate(c.meshes)],
        "image": p.image,
    }


class ApiTutor:
    """Implementa la lógica de los endpoints locales de la aplicación."""

    def __init__(
        self,
        carpeta_datos: str | Path | None = None,
        servidor_modelo: ServidorModelo | None = None,
        conversador: Conversador | None = None,
        banco: dict | None = None,
    ):
        self.carpeta_datos = carpeta_datos
        self.servidor_modelo = servidor_modelo
        self.conversador = conversador or Conversador(llm=None)
        self._sin_modelo = Conversador(llm=None)
        self.banco = banco if banco is not None else {p.id: p for p in load_bank(RAIZ / "problemas")}

        self.lock_estado = threading.RLock()
        self.lock_motor = threading.Lock()
        self.lock_modelo = threading.Lock()

        self.codigo_activo: str | None = None
        self.estado_alumno: dict | None = None
        self.sesion_activa: Session | None = None
        self.problema_activo: Any = None
        self.clave_intento_activa: str | None = None
        self.t0_intento: float | None = None
        # respuesta que espera la confianza del alumno antes de evaluarse: {texto, ruta, intencion}
        self.pendiente: dict | None = None

    def conectar_modelo(self, servidor_modelo: ServidorModelo) -> None:
        """Conecta un servidor de modelo arrancado en segundo plano."""
        self.servidor_modelo = servidor_modelo
        from tutor.llm import LLM
        llm = LLM(base_url=servidor_modelo.url_base)
        llm.configurar(base_url=servidor_modelo.url_base, llave=servidor_modelo.llave)
        self.conversador = Conversador(llm=llm)

    def estado_modelo(self) -> str:
        if self.servidor_modelo is not None:
            # mientras carga, arrancar() se encarga de los fallos (y el proceso aún puede no existir)
            if self.servidor_modelo.estado == "listo" and not self.servidor_modelo.vivo:
                logger.warning("El proceso del modelo terminó; se sigue con los mensajes base.")
                self.servidor_modelo.estado = "error"
            return self.servidor_modelo.estado
        if self.conversador.llm is not None and self.conversador.llm.enabled:
            return "listo"
        return "sin_modelo"

    def _conversador(self) -> Conversador:
        """El conversador con modelo sólo si el modelo responde; si no, el de plantillas."""
        return self.conversador if self.estado_modelo() == "listo" else self._sin_modelo

    # ------------------------------------------------------------- Almacenamiento
    def _ruta_estado(self, codigo_norm: str) -> Path:
        return obtener_carpeta_datos(self.carpeta_datos) / codigo_norm / "estado.json"

    def _cargar_estado(self, codigo_norm: str) -> dict | None:
        ruta = self._ruta_estado(codigo_norm)
        if not ruta.exists():
            return None
        try:
            estado = json.loads(ruta.read_text(encoding="utf-8"))
            if not isinstance(estado, dict):
                raise ValueError("no es un objeto")
        except (OSError, ValueError) as e:
            # se aparta en vez de sobrescribirlo, por si se puede recuperar
            apartado = ruta.with_name(f"estado-ilegible-{int(time.time())}.json")
            logger.error("Estado ilegible de %s (%s); se aparta como %s", codigo_norm, e, apartado.name)
            try:
                os.replace(ruta, apartado)
            except OSError:
                pass
            return None
        estado.setdefault("consentimiento", None)
        estado.setdefault("intentos_en_curso", {})
        estado.setdefault("terminados", [])
        return estado

    def _guardar_estado(self) -> None:
        if not self.codigo_activo or self.estado_alumno is None:
            return
        ruta_final = self._ruta_estado(self.codigo_activo)
        ruta_final.parent.mkdir(parents=True, exist_ok=True)
        ruta_tmp = ruta_final.with_suffix(".tmp")
        try:
            with open(ruta_tmp, "w", encoding="utf-8") as f:
                json.dump(self.estado_alumno, f, ensure_ascii=False, indent=2)
            os.replace(ruta_tmp, ruta_final)
        except OSError as e:
            logger.error("Error guardando estado de alumno %s: %s", self.codigo_activo, e)

    def _intento(self) -> dict:
        return self.estado_alumno["intentos_en_curso"][self.clave_intento_activa]

    def _hay_intento(self) -> bool:
        return (self.sesion_activa is not None and self.estado_alumno is not None
                and self.clave_intento_activa in self.estado_alumno["intentos_en_curso"])

    def _guardar_tiempo_activo(self) -> None:
        if self._hay_intento() and self.t0_intento:
            self._intento()["segundos_activos"] = round(time.time() - self.t0_intento, 1)

    def _soltar_intento(self) -> None:
        """Pausa el intento activo: guarda su tiempo y deja de atenderlo."""
        self._guardar_tiempo_activo()
        self.sesion_activa = None
        self.problema_activo = None
        self.clave_intento_activa = None
        self.t0_intento = None
        self.pendiente = None

    def _terminar_intento(self) -> None:
        """Un intento terminado sale de los en curso: abrirlo otra vez empieza uno nuevo."""
        clave = self.clave_intento_activa
        if clave not in self.estado_alumno["terminados"]:
            self.estado_alumno["terminados"].append(clave)
        self.estado_alumno["intentos_en_curso"].pop(clave, None)
        self._soltar_intento()

    # ------------------------------------------------------------------ Endpoints
    def get_estado(self) -> dict:
        consentimiento = self.estado_alumno.get("consentimiento") if self.estado_alumno else None
        return {
            "version": __version__,
            "modelo_estado": self.estado_modelo(),
            "codigo": formatear(self.codigo_activo) if self.codigo_activo else None,
            "consentimiento": consentimiento,
            "inactividad_min": INACTIVIDAD_MIN,
            "intervalo_latido": INTERVALO_LATIDO,
            "caracteres_por_segundo": VELOCIDAD_ESCRITURA_CPS,
        }

    def get_consentimiento(self) -> dict:
        texto = (RAIZ / "textos" / "consentimiento.md").read_text(encoding="utf-8")
        version = re.search(r"versión\s+([\w.\-]+)", texto)
        return {"version": version.group(1) if version else "", "texto": texto}

    def post_entrar(self, datos: dict) -> tuple[int, dict]:
        codigo_raw = str(datos.get("codigo") or "")
        if not es_valido(codigo_raw):
            return 400, {"error": "El código no es válido. Revísalo con tu profesor."}

        if self.codigo_activo:
            self.post_salir({"motivo": "cambio"})
        codigo_norm = normalizar(codigo_raw)
        estado = self._cargar_estado(codigo_norm)
        if estado is None:
            estado = {"consentimiento": None, "intentos_en_curso": {}, "terminados": []}
        self.codigo_activo = codigo_norm
        self.estado_alumno = estado
        self._guardar_estado()
        return 200, {
            "ok": True,
            "codigo": formatear(codigo_norm),
            "requiere_consentimiento": estado["consentimiento"] is None,
            "consentimiento": estado["consentimiento"],
        }

    def post_consentimiento(self, datos: dict) -> tuple[int, dict]:
        if not self.codigo_activo or self.estado_alumno is None:
            return 401, {"error": "No hay sesión de alumno activa."}

        acepta = datos.get("acepta") is True
        self.estado_alumno["consentimiento"] = acepta
        self._guardar_estado()
        return 200, {"ok": True, "consentimiento": acepta}

    def post_salir(self, datos: dict) -> tuple[int, dict]:
        self._soltar_intento()
        self._guardar_estado()
        self.codigo_activo = None
        self.estado_alumno = None
        return 200, {"ok": True}

    def get_problemas(self) -> dict:
        order = list(TOPICS)
        items = sorted(
            self.banco.values(),
            key=lambda p: (order.index(p.topic) if p.topic in order else 9, p.id),
        )
        terminados = set(self.estado_alumno["terminados"]) if self.estado_alumno else set()
        en_curso = set(self.estado_alumno["intentos_en_curso"]) if self.estado_alumno else set()

        lista = []
        for p in items:
            desc = _describe(p)
            estados_modo = {}
            for m in MODES:
                clave = f"{p.id}:{m}"
                if clave in en_curso:
                    estados_modo[m] = "en_curso"
                elif clave in terminados:
                    estados_modo[m] = "terminado"
                else:
                    estados_modo[m] = "nuevo"
            desc["estados"] = estados_modo
            lista.append(desc)

        return {"problems": lista, "modes": MODES}

    def _problema(self, pid: str, propio: str, metodo: str):
        if pid:
            return self.banco[pid]
        head = f"METODO: {metodo}\n" if metodo else ""
        p = parse_problem(head + propio, "propio")
        if p.method == "mallas" and not p.circuit.meshes:
            raise CircuitError("Para el método de mallas declara las mallas como están dibujadas, "
                               "una por renglón: MALLA 1: V1 R1 R2 (en sentido horario).")
        return p

    def post_iniciar(self, datos: dict) -> tuple[int, dict]:
        if not self.codigo_activo or self.estado_alumno is None:
            return 401, {"error": "No hay sesión de alumno activa."}

        pid = str(datos.get("problema") or "")
        modo = str(datos.get("modo") or "aprender")
        propio = str(datos.get("propio") or "")
        metodo = str(datos.get("metodo") or "")
        if modo not in MODES:
            return 400, {"error": "Modo desconocido."}
        if pid and pid not in self.banco:
            return 400, {"error": "Problema desconocido."}
        if not pid and not propio.strip():
            return 400, {"error": "Escribe el circuito de tu problema."}

        if pid:
            clave = f"{pid}:{modo}"
        else:
            h = hashlib.sha256((propio + metodo).encode("utf-8")).hexdigest()[:8]
            clave = f"propio_{h}:{modo}"

        self._soltar_intento()
        self._guardar_estado()

        intentos = self.estado_alumno["intentos_en_curso"]
        if clave in intentos:
            intento = intentos[clave]
            try:
                p = self._problema(pid, intento.get("propio", propio), intento.get("metodo", metodo))
                with self.lock_motor:
                    s = reconstruir(p.circuit, p.method, modo=modo, entradas=intento.get("entradas", []))
                if s.finished:
                    raise ValueError("el intento guardado ya había terminado")
            except Exception as e:
                # un intento que ya no se puede reproducir (p. ej. cambió el banco) se reemplaza por uno nuevo
                logger.error("No se pudo retomar %s (%s); se empieza de nuevo.", clave, e)
                del intentos[clave]
            else:
                self.sesion_activa = s
                self.problema_activo = p
                self.clave_intento_activa = clave
                self.t0_intento = time.time() - intento.get("segundos_activos", 0.0)
                # la "Duración" de los indicadores cuenta también el tiempo antes de la pausa
                s.t0 = self.t0_intento
                chat = intento.get("chat", [])
                return 200, {
                    "retomado": True,
                    "problem": _describe(p),
                    "chat": chat,
                    "message": chat[-1]["texto"] if chat else "",
                    "finished": False,
                }

        try:
            p = self._problema(pid, propio, metodo)
            with self.lock_motor:
                s = Session(p.circuit, p.method, mode=modo)
                msg_base = s.start()
        except Exception as e:
            logger.info("Problema rechazado: %s", e)
            return 400, {"error": str(e)}

        self.sesion_activa = s
        self.problema_activo = p
        self.clave_intento_activa = clave
        self.t0_intento = time.time()

        red = self._redactar(msg_base, "", self._contexto())
        chat = [{"rol": "tutor", "texto": red["texto"], "fuente": red["fuente"]}]
        intentos[clave] = {
            "problema": pid,
            "modo": modo,
            "entradas": [],
            "chat": chat,
            "propio": propio,
            "metodo": p.method,
            "segundos_activos": 0.0,
            "pasos_confianza": [],
            "confianzas": [],
        }
        self._guardar_estado()

        return 200, {
            "retomado": False,
            "problem": _describe(p),
            "message": red["texto"],
            "chat": chat,
            "fuente": red["fuente"],
            "finished": False,
        }

    def post_responder(self, datos: dict) -> tuple[int, dict]:
        if not self._hay_intento():
            return 400, {"error": "No hay ningún problema en curso."}

        texto = str(datos.get("texto") or "").strip()
        if not texto:
            return 400, {"error": "Mensaje vacío."}
        # una respuesta que se quedó esperando la confianza (p. ej. al recargar) se descarta
        self.pendiente = None

        s = self.sesion_activa
        ruta = s.ruta_rapida(texto)
        intencion = "respuesta"
        if ruta == "ambiguo" and self.estado_modelo() == "listo":
            with self.lock_modelo:
                info = self.conversador.entender(texto, s.pregunta_actual())
            if info:
                intencion = info["intencion"]
        # en un paso concept la ficha contiene la respuesta del paso: se atiende como pista
        if intencion == "pregunta_teoria" and s.step.kind == "concept":
            intencion = "ayuda"

        if intencion == "respuesta" and self._pide_confianza(texto):
            self.pendiente = {"texto": texto, "ruta": ruta, "intencion": intencion}
            self._guardar_estado()
            return 200, {"necesita_confianza": True}

        return 200, self._evaluar_turno(texto, ruta, intencion)

    def _pide_confianza(self, texto: str) -> bool:
        """A lo más una vez por paso: un texto que el motor no pudo leer no vuelve a preguntarla."""
        s = self.sesion_activa
        preguntados = self._intento().setdefault("pasos_confianza", [])
        if s.step.kind not in PASOS_CON_CONFIANZA or s.step.id in preguntados or not s.pide_confianza(texto):
            return False
        preguntados.append(s.step.id)
        return True

    def post_confianza(self, datos: dict) -> tuple[int, dict]:
        if not self._hay_intento() or self.pendiente is None:
            return 400, {"error": "No hay respuesta pendiente de evaluación."}

        valor = datos.get("valor")
        if valor is not None and (type(valor) is not int or not 1 <= valor <= 5):
            return 400, {"error": "La confianza va de 1 a 5, o vacía."}
        ms = datos.get("ms")
        ms = round(ms) if type(ms) in (int, float) and ms >= 0 else None

        pendiente, self.pendiente = self.pendiente, None
        self._intento().setdefault("confianzas", []).append(
            {"paso_id": self.sesion_activa.step.id, "valor": valor, "ms": ms})
        return 200, self._evaluar_turno(pendiente["texto"], pendiente["ruta"], pendiente["intencion"])

    def post_pista(self, datos: dict | None = None) -> tuple[int, dict]:
        if not self._hay_intento():
            return 400, {"error": "No hay ningún problema en curso."}
        self.pendiente = None
        return 200, self._evaluar_turno("Dame una pista", ruta="motor", intencion="ayuda")

    def post_eventos(self, datos: dict) -> tuple[int, dict]:
        return 200, {"ok": True}

    def post_exportar(self, datos: dict) -> tuple[int, dict]:
        return 501, {"error": "La exportación todavía no está disponible."}

    def post_abrir_carpeta(self, datos: dict) -> tuple[int, dict]:
        return 501, {"error": "La exportación todavía no está disponible."}

    def _contexto(self, historial: list[str] | None = None, ruta: str = "", intencion: str = "") -> dict:
        """Contexto de la redacción; se calcula después de reply() para que la guardia vea el paso nuevo."""
        s = self.sesion_activa
        ctx = contexto_guardia(s)
        ctx.update(
            titulo_problema=self.problema_activo.title,
            descripcion_paso=s.pregunta_actual(),
            ultimos_intercambios=historial or [],
            ruta=ruta,
            intencion=intencion,
        )
        return ctx

    def _evaluar_turno(self, texto: str, ruta: str, intencion: str) -> dict:
        s = self.sesion_activa
        intento = self._intento()
        historial = [f"{m['rol']}: {m['texto']}" for m in intento["chat"]]

        if intencion == "pregunta_teoria":
            # el motor no avanza
            conv = self._conversador()
            with self.lock_modelo:
                red = conv.responder_teoria(texto, s.pregunta_actual(), self._contexto(historial, ruta, intencion))
        else:
            if intencion == "ayuda":
                with self.lock_motor:
                    base = s.reply("pista")
            elif intencion in SIN_MOTOR:
                base = plantilla_para_intencion(intencion, s.pregunta_actual())
            else:
                with self.lock_motor:
                    base = s.reply(texto)
            red = self._redactar(base, texto, self._contexto(historial, ruta, intencion))

        intento["entradas"] = list(s.entradas)
        intento["chat"].append({"rol": "alumno", "texto": texto})
        intento["chat"].append({"rol": "tutor", "texto": red["texto"], "fuente": red["fuente"]})

        indicadores = None
        if s.finished:
            indicadores = s.indicators()
            self._terminar_intento()
        else:
            self._guardar_tiempo_activo()
        self._guardar_estado()
        return {
            "mensaje": red["texto"],
            "terminado": s.finished,
            "indicadores": indicadores,
            "fuente": red["fuente"],
        }

    def _redactar(self, base: str, alumno: str, ctx: dict) -> dict:
        conv = self._conversador()
        with self.lock_modelo:
            return conv.redactar(base, alumno, ctx)
