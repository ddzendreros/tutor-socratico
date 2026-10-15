"""Manejador de la API REST local para el Tutor Socrático."""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from nacl.exceptions import CryptoError

from tutor.circuit import CircuitError
from tutor.engine import DIRECT_RE, HELP_RE, MODES, Session, reconstruir
from tutor.privacidad import scrub
from tutor.problems import load_bank, parse_problem
from escritorio import __version__, equipo
from escritorio.alumnos import es_valido, formatear, normalizar
from escritorio.config import (ANOTACION_REPOSO, INACTIVIDAD_MIN, INTERVALO_LATIDO, MODELO_ARCHIVO,
                               PASOS_CON_CONFIANZA, PASOS_DE_CONCEPTO, VELOCIDAD_ESCRITURA_CPS)
from escritorio.conversacion import Conversador, contexto_guardia, plantilla_para_intencion
from escritorio.modelo import ServidorModelo
from escritorio.cifrado import caja_estado, leer_llave_publica
from escritorio.cuestionarios import Cuestionario, cargar_cuestionarios, toca, validar_respuesta
from escritorio.exportar import SinDatos, exportar
from escritorio.registro import Registro
from escritorio.rutas import RAIZ, obtener_carpeta_datos, obtener_carpeta_exportaciones, ruta_llave_publica

logger = logging.getLogger("tutor_socratico.api")

TOPICS = {
    "ohm": "Ley de Ohm",
    "serie_paralelo": "Serie y paralelo",
    "mallas": "Método de mallas",
    "nodos": "Método de nodos",
    "divisor": "Divisor de voltaje y corriente",
    "superposicion": "Superposición",
    "thevenin": "Thévenin y Norton",
}

# intenciones que se contestan con una plantilla sin que el motor evalúe nada
SIN_MOTOR = ("pregunta_paso", "animo", "fuera_de_tema", "charla")

# eventos que puede mandar la interfaz; los demás sólo los escribe la aplicación
EVENTOS_UI = {"ventana", "inactividad", "clic", "copiar", "cortar", "pegar", "chat_desplazamiento", "dibujo",
              "pantalla", "tamano_ventana"}
MOTIVOS_SALIDA = {"boton", "inactividad", "cierre", "cambio"}
MAX_EVENTOS_COMPOSICION = 5000

# respuestas abiertas que el modelo anota con la rúbrica, sólo como dato
PASOS_ANOTADOS = {"concept": "concepto", "sup_apagar": "concepto", "th_equivalentes": "concepto",
                  "verify": "verificacion", "metacog": "metacognicion"}
IDEAS_ESPERADAS = {
    "verificacion": ("Una forma concreta de comprobar los resultados: balance de potencias (PE = PC), sustituir "
                     "los valores en las ecuaciones originales (LVK o LCK) o medir en un simulador."),
    "metacognicion": ("Nombra un error o una dificultad concreta, explica por qué ocurrió y propone una regla "
                      "que pueda usar en otro circuito."),
}


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


def _numero(v: Any, minimo: float | None = None) -> int | None:
    if type(v) not in (int, float) or not math.isfinite(v) or (minimo is not None and v < minimo):
        return None
    return round(v)


def _composicion(c: Any, texto: str) -> dict | None:
    """Composición del mensaje que manda la interfaz, con tipos y tamaños revisados."""
    if not isinstance(c, dict):
        return None
    comp = {k: _numero(c.get(k)) for k in ("ms_desde_tutor", "ms_primera_tecla")}
    comp.update({k: _numero(c.get(k), 0) for k in ("ms_escribiendo", "teclas", "borrados", "pegados",
                                                     "caracteres_pegados", "pausas_2s", "foco_perdido")})
    eventos = []
    for ev in c.get("eventos") if isinstance(c.get("eventos"), list) else []:
        if (isinstance(ev, list) and len(ev) == 4 and _numero(ev[0]) is not None and isinstance(ev[1], str)
                and isinstance(ev[2], str) and _numero(ev[3]) is not None):
            eventos.append([_numero(ev[0]), ev[1][:40], ev[2][:2000], _numero(ev[3])])
            if len(eventos) >= MAX_EVENTOS_COMPOSICION:
                break
    # scrub() no reconoce un dato personal tecleado letra por letra: si el mensaje o lo tecleado
    # lo contienen, se guardan las teclas sin su texto
    tecleado = "".join(e[2] for e in eventos if e[1].startswith("insert"))
    oculto = scrub(tecleado) != tecleado or scrub(texto) != texto
    comp["eventos"] = [[dt, tipo, "", pos] for dt, tipo, _, pos in eventos] if oculto else eventos
    comp["teclas_sin_texto"] = oculto
    return comp


def _datos_ui(d: dict) -> dict:
    """Los eventos de la interfaz traen sólo valores simples."""
    out = {}
    for k, v in list(d.items())[:20]:
        if isinstance(v, str):
            out[str(k)[:40]] = v[:500]
        elif v is None or isinstance(v, bool) or (type(v) in (int, float) and math.isfinite(v)):
            out[str(k)[:40]] = v
    return out


class ApiTutor:
    """Implementa la lógica de los endpoints locales de la aplicación."""

    def __init__(
        self,
        carpeta_datos: str | Path | None = None,
        servidor_modelo: ServidorModelo | None = None,
        conversador: Conversador | None = None,
        banco: dict | None = None,
        llave_publica: Path | None = None,
        carpeta_exportaciones: Path | None = None,
        carpeta_cuestionarios: Path | None = None,
    ):
        self.carpeta_datos = carpeta_datos
        self.servidor_modelo = servidor_modelo
        self.conversador = conversador or Conversador(llm=None)
        self._sin_modelo = Conversador(llm=None)
        self.banco = banco if banco is not None else {p.id: p for p in load_bank(RAIZ / "problemas")}
        self.cuestionarios = cargar_cuestionarios(carpeta_cuestionarios,
                                                  temas={p.topic for p in self.banco.values()})
        ruta_publica = Path(llave_publica) if llave_publica else ruta_llave_publica()
        publica = None
        if ruta_publica.is_file():
            try:
                publica = leer_llave_publica(ruta_publica)
            except (ValueError, TypeError, OSError) as e:
                logger.error("la llave pública del estudio no se pudo leer (%s); no se registrará nada", e)
        else:
            logger.error("no está la llave pública del estudio; no se registrará nada")
        self.publica = publica
        self.registro = Registro(carpeta_datos, publica)
        self.carpeta_exportaciones = Path(carpeta_exportaciones) if carpeta_exportaciones \
            else obtener_carpeta_exportaciones()
        # derivada una vez por sesión de alumno (argon2id tarda unas decenas de ms)
        self._caja = None
        # "ventana" o "navegador"; lo fija quien abre la interfaz
        self.modo_ventana = "ventana"

        self.lock_estado = threading.RLock()
        self.lock_motor = threading.Lock()
        self.lock_modelo = threading.Lock()

        self.codigo_activo: str | None = None
        self.estado_alumno: dict | None = None
        self.sesion_activa: Session | None = None
        self.problema_activo: Any = None
        self.clave_intento_activa: str | None = None
        self.t0_intento: float | None = None
        # respuesta que espera la confianza del alumno antes de evaluarse
        self.pendiente: dict | None = None

        self.pantalla: dict | None = None
        self.t0_sesion: float | None = None
        self.ms_precalentar: int | None = None
        self._modelo_registrado: str | None = None
        # la anotación sólo usa el modelo cuando el alumno no lo está usando
        self._peticiones_alumno = 0
        self._ultimo_uso_modelo = time.monotonic()

    # --------------------------------------------------------------------- Modelo
    def conectar_modelo(self, servidor_modelo: ServidorModelo) -> None:
        """Se llama desde el hilo que arrancó el modelo, haya cargado o no."""
        self.servidor_modelo = servidor_modelo
        if servidor_modelo.estado == "listo":
            from tutor.llm import LLM
            llm = LLM(base_url=servidor_modelo.url_base)
            llm.configurar(base_url=servidor_modelo.url_base, llave=servidor_modelo.llave)
            conversador = Conversador(llm=llm)
            t0 = time.perf_counter()
            conversador.precalentar()
            self.ms_precalentar = round((time.perf_counter() - t0) * 1000)
            self.conversador = conversador
        with self.lock_estado:
            self.registrar_estado_modelo()

    def estado_modelo(self) -> str:
        if self.servidor_modelo is not None:
            # mientras carga, arrancar() se encarga de los fallos (y el proceso aún puede no existir)
            if self.servidor_modelo.estado == "listo" and not self.servidor_modelo.vivo:
                logger.warning("El proceso del modelo terminó; se sigue con los mensajes base.")
                self.servidor_modelo.estado = "error"
                self.registrar_estado_modelo()
            if self.servidor_modelo.estado == "listo" and self.conversador.llm is None:
                return "cargando"  # se está precalentando
            return self.servidor_modelo.estado
        if self.conversador.llm is not None and self.conversador.llm.enabled:
            return "listo"
        return "sin_modelo"

    def _datos_modelo(self) -> dict:
        sm = self.servidor_modelo
        return {
            "estado": self.estado_modelo(),
            "ms_carga": getattr(sm, "ms_carga", None),
            "ms_precalentar": self.ms_precalentar,
            "variante_cpu": getattr(sm, "variante_cpu", None) or None,
            "hilos": getattr(sm, "hilos", None),
            "ctx": getattr(sm, "ctx", None),
            "error": None,
        }

    def registrar_estado_modelo(self) -> None:
        """Registra el estado del modelo cuando cambia durante la sesión del alumno."""
        if not self.tiene_consentimiento():
            return
        datos = self._datos_modelo()
        if datos["estado"] != self._modelo_registrado:
            self._modelo_registrado = datos["estado"]
            self._registrar("modelo_estado", datos)

    def _conversador(self) -> Conversador:
        """El conversador con modelo sólo si el modelo responde; si no, el de plantillas."""
        return self.conversador if self.estado_modelo() == "listo" else self._sin_modelo

    @contextmanager
    def _turno_modelo(self):
        """Una petición del alumno al modelo; la anotación espera a que el modelo descanse."""
        self._peticiones_alumno += 1
        try:
            with self.lock_modelo:
                yield
        finally:
            self._peticiones_alumno -= 1
            self._ultimo_uso_modelo = time.monotonic()

    # ------------------------------------------------------------------- Registro
    def tiene_consentimiento(self) -> bool:
        return bool(self.codigo_activo and self.estado_alumno and self.estado_alumno.get("consentimiento") is True)

    def _registrar(self, tipo: str, datos: dict) -> dict | None:
        """Evento del alumno activo; quien no aceptó participar no deja ningún registro."""
        if not self.tiene_consentimiento():
            return None
        return self.registro.registrar(self.codigo_activo, tipo, datos, intento=self.clave_intento_activa)

    def _registrar_sesion_iniciada(self, version_consentimiento: str) -> None:
        if not self.tiene_consentimiento():
            return
        sm = self.servidor_modelo
        ruta_modelo = getattr(sm, "ruta_modelo", None)
        modelo = self._datos_modelo()
        self._modelo_registrado = modelo["estado"]
        self._registrar("sesion_iniciada", {
            "equipo": equipo.info_equipo(obtener_carpeta_datos(self.carpeta_datos), self.modo_ventana,
                                         self.pantalla, modelo["variante_cpu"]),
            "versiones": equipo.versiones(ruta_modelo),
            "modelo": modelo,
            "version_consentimiento": version_consentimiento,
        })

    def _datos_intento(self, p, modo: str) -> dict:
        datos = {"problema": p.id if p.id in self.banco else "propio", "tema": p.topic, "metodo": p.method,
                 "modo": modo}
        if datos["problema"] == "propio":
            datos["circuito_propio"] = self._intento().get("propio", "")
        return datos

    @staticmethod
    def _datos_tutor(red: dict, base: str) -> dict:
        return {
            "texto_base": red.get("base", base),
            "texto_mostrado": red["texto"],
            "fuente": red["fuente"],
            "guardia": red.get("guardia"),
            "reintentos": red.get("reintentos", 0),
            "intentos": red.get("intentos", []),
            "ms": red.get("ms", 0),
            "tokens_prompt": red.get("tokens_prompt", 0),
            "tokens_salida": red.get("tokens_salida", 0),
            "tokens_cache": red.get("tokens_cache", 0),
        }

    # ------------------------------------------------------------- Almacenamiento
    def _ruta_estado(self, codigo_norm: str) -> Path:
        return obtener_carpeta_datos(self.carpeta_datos) / codigo_norm / "estado.bin"

    def _cargar_estado(self, codigo_norm: str) -> dict | None:
        ruta = self._ruta_estado(codigo_norm)
        # versión anterior: el estado iba en claro en estado.json; se lee y el primer guardado lo borra
        ruta_vieja = ruta.with_name("estado.json")
        origen = ruta if ruta.exists() else ruta_vieja
        if not origen.exists():
            return None
        try:
            if origen is ruta:
                estado = json.loads(self._caja.decrypt(ruta.read_bytes()).decode("utf-8"))
            else:
                estado = json.loads(ruta_vieja.read_text(encoding="utf-8"))
            if not isinstance(estado, dict):
                raise ValueError("no es un objeto")
        except (CryptoError, ValueError, OSError) as e:
            # se aparta en vez de sobrescribirlo, por si se puede recuperar
            apartado = origen.with_name(f"estado-ilegible-{int(time.time())}{origen.suffix}")
            logger.error("Estado ilegible de %s (%s); se aparta como %s", codigo_norm, e, apartado.name)
            try:
                os.replace(origen, apartado)
            except OSError:
                pass
            return None
        estado.setdefault("consentimiento", None)
        estado.setdefault("intentos_en_curso", {})
        estado.setdefault("terminados", [])
        estado.setdefault("cola_anotaciones", [])
        estado.setdefault("cuestionarios_respondidos", {})
        estado.setdefault("cuestionario_en_curso", None)
        return estado

    def _guardar_estado(self) -> None:
        if not self.codigo_activo or self.estado_alumno is None:
            return
        ruta_final = self._ruta_estado(self.codigo_activo)
        ruta_final.parent.mkdir(parents=True, exist_ok=True)
        ruta_tmp = ruta_final.with_suffix(".tmp")
        try:
            ruta_tmp.write_bytes(self._caja.encrypt(json.dumps(self.estado_alumno, ensure_ascii=False).encode("utf-8")))
            os.replace(ruta_tmp, ruta_final)
        except OSError as e:
            logger.error("Error guardando estado de alumno %s: %s", self.codigo_activo, e)
            return
        try:
            ruta_final.with_name("estado.json").unlink(missing_ok=True)
        except OSError as e:
            logger.error("No se pudo borrar el estado.json anterior de %s: %s", self.codigo_activo, e)

    def _intento(self) -> dict:
        return self.estado_alumno["intentos_en_curso"][self.clave_intento_activa]

    def _hay_intento(self) -> bool:
        return (self.sesion_activa is not None and self.estado_alumno is not None
                and self.clave_intento_activa in self.estado_alumno["intentos_en_curso"])

    def _guardar_tiempo_activo(self) -> None:
        if self._hay_intento() and self.t0_intento:
            self._intento()["segundos_activos"] = round(time.time() - self.t0_intento, 1)

    def _soltar_intento(self, motivo: str = "otro_problema") -> None:
        """Pausa el intento activo: guarda su tiempo y deja de atenderlo."""
        if self._hay_intento():
            self._guardar_tiempo_activo()
            self._registrar("intento_pausado", {**self._datos_intento(self.problema_activo, self._intento()["modo"]),
                                                "motivo": motivo,
                                                "segundos_activos": self._intento()["segundos_activos"]})
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
        self._caja = caja_estado(codigo_norm, self.registro.sal)
        estado = self._cargar_estado(codigo_norm)
        if estado is None:
            estado = {"consentimiento": None, "intentos_en_curso": {}, "terminados": [], "cola_anotaciones": [],
                      "cuestionarios_respondidos": {},
                      "cuestionario_en_curso": None}
        self.codigo_activo = codigo_norm
        self.estado_alumno = estado
        self.t0_sesion = time.monotonic()
        pantalla = datos.get("pantalla")
        self.pantalla = _datos_ui(pantalla) if isinstance(pantalla, dict) else None
        self._guardar_estado()
        self._registrar_sesion_iniciada(self.get_consentimiento()["version"])
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
        ya_aceptado = self.tiene_consentimiento()
        self.estado_alumno["consentimiento"] = acepta
        self._guardar_estado()
        if acepta and not ya_aceptado:
            version = self.get_consentimiento()["version"]
            self._registrar("consentimiento", {"version": version, "acepta": True})
            self._registrar_sesion_iniciada(version)
        return 200, {"ok": True, "consentimiento": acepta}

    def post_salir(self, datos: dict) -> tuple[int, dict]:
        motivo = datos.get("motivo") if datos.get("motivo") in MOTIVOS_SALIDA else "boton"
        self._soltar_intento(motivo)
        if self.t0_sesion is not None:
            self._registrar("sesion_terminada", {"motivo": motivo,
                                                 "ms": round((time.monotonic() - self.t0_sesion) * 1000)})
        self._guardar_estado()
        self.codigo_activo = None
        self.estado_alumno = None
        self._caja = None
        self.t0_sesion = None
        self.pantalla = None
        self._modelo_registrado = None
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

        # un cuestionario pendiente va antes que cualquier problema
        if self._cuestionario_en_curso() or self._abrir_cuestionario(
                "antes_de_tema", self.banco[pid].topic if pid else None):
            self._soltar_intento()
            self._guardar_estado()
            return 409, {"error": "Antes de seguir, contesta el cuestionario.", "cuestionario_pendiente": True}

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
                self._registrar("intento_iniciado", {**self._datos_intento(p, modo), "retomado": True,
                                                     "segundos_activos": intento.get("segundos_activos", 0.0),
                                                     "paso_id": s.step.id, "paso_tipo": s.step.kind})
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
        # el primer mensaje del tutor va aquí: no responde a ningún turno del alumno
        self._registrar("intento_iniciado", {**self._datos_intento(p, modo), "retomado": False,
                                             "segundos_activos": 0.0, "paso_id": s.step.id,
                                             "paso_tipo": s.step.kind, "tutor": self._datos_tutor(red, msg_base)})

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
        turno = {"texto": texto, "ruta": ruta, "intencion": "respuesta", "intencion_modelo": None,
                 "intencion_ms": None, "composicion": _composicion(datos.get("composicion"), texto)}
        if ruta == "ambiguo" and self.estado_modelo() == "listo":
            with self._turno_modelo():
                info = self.conversador.entender(texto, s.pregunta_actual())
            if info:
                turno.update(intencion=info["intencion"], intencion_modelo=info["intencion"],
                             intencion_ms=info.get("ms"))
        # en un paso concept la ficha contiene la respuesta del paso: se atiende como pista
        if turno["intencion"] == "pregunta_teoria" and s.step.kind in PASOS_DE_CONCEPTO:
            turno["intencion"] = "ayuda"

        if turno["intencion"] == "respuesta" and self._pide_confianza(texto):
            self.pendiente = turno
            self._guardar_estado()
            return 200, {"necesita_confianza": True}

        return 200, self._evaluar_turno(**turno)

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

        turno, self.pendiente = self.pendiente, None
        self._intento().setdefault("confianzas", []).append(
            {"paso_id": self.sesion_activa.step.id, "valor": valor, "ms": ms})
        return 200, self._evaluar_turno(**turno, confianza={"valor": valor, "ms": ms})

    def post_pista(self, datos: dict | None = None) -> tuple[int, dict]:
        if not self._hay_intento():
            return 400, {"error": "No hay ningún problema en curso."}
        self.pendiente = None
        return 200, self._evaluar_turno("Dame una pista", ruta="motor", intencion="ayuda", boton=True)

    def post_eventos(self, datos: dict) -> tuple[int, dict]:
        eventos = datos.get("eventos")
        if not isinstance(eventos, list):
            return 400, {"error": "Faltan los eventos."}
        if not self.tiene_consentimiento():
            return 200, {"ok": True, "registrados": 0}
        ahora_ms, ahora_mono = time.time() * 1000, self.registro.mono_ahora()
        envio = datos.get("mono_envio")
        lote = []
        for ev in eventos[:500]:
            if not (isinstance(ev, dict) and ev.get("tipo") in EVENTOS_UI and isinstance(ev.get("datos", {}), dict)):
                continue
            ms, mono = ev.get("ms"), ev.get("mono")
            ms = ms if _numero(ms) is not None and abs(ms - ahora_ms) < 86_400_000 else None
            # la página mide con su propio reloj; se pasa al del arranque con la hora del envío
            ok_mono = _numero(mono) is not None and _numero(envio) is not None and 0 <= envio - mono < 3_600_000
            lote.append({"tipo": ev["tipo"], "datos": _datos_ui(ev.get("datos") or {}),
                         "intento": self.clave_intento_activa, "origen": "ui", "ms": ms,
                         "mono": ahora_mono - (envio - mono) if ok_mono else None})
        if lote:
            self.registro.registrar_varios(self.codigo_activo, lote)
        return 200, {"ok": True, "registrados": len(lote)}

    def post_exportar(self, datos: dict) -> tuple[int, dict]:
        if not self.tiene_consentimiento():
            return 409, {"error": "Sólo quien aceptó participar tiene datos para exportar."}
        if not self.registro.activo:
            return 409, {"error": "Este equipo no tiene la llave del estudio; avisa a tu profesor."}
        codigo = self.codigo_activo
        sm = self.servidor_modelo
        try:
            with self.registro.escritura:
                resultado = exportar(obtener_carpeta_datos(self.carpeta_datos), codigo, self.registro.instalacion,
                                     self.publica, equipo.versiones(getattr(sm, "ruta_modelo", None)),
                                     self.carpeta_exportaciones)
        except SinDatos:
            return 409, {"error": "Todavía no hay datos que exportar."}
        except OSError as e:
            logger.error("No se pudo exportar los datos de %s: %s", codigo, e)
            return 500, {"error": "No se pudo guardar el archivo de exportación. Revisa que haya espacio en el disco."}
        ruta = resultado["ruta"]
        # fuera del candado (no es reentrante): este evento queda en la exportación siguiente
        self._registrar("exportacion", {"archivo": ruta.name, "lotes": resultado["lotes"],
                                        "bytes": resultado["bytes"], "seq_hasta": self.registro.ultimo_seq(codigo)})
        return 200, {"ok": True, "archivo": str(ruta), "carpeta": str(ruta.parent)}

    def post_abrir_carpeta(self, datos: dict) -> tuple[int, dict]:
        if sys.platform != "win32":
            return 501, {"error": "Abrir la carpeta sólo está disponible en Windows."}
        self.carpeta_exportaciones.mkdir(parents=True, exist_ok=True)
        os.startfile(str(self.carpeta_exportaciones))
        return 200, {"ok": True}

    # ------------------------------------------------------------------ cuestionarios
    def _cuestionario_en_curso(self) -> Cuestionario | None:
        """El cuestionario que el alumno tiene a medias; se descarta si ya no existe o cambió."""
        if not self.tiene_consentimiento():
            return None
        en_curso = self.estado_alumno.get("cuestionario_en_curso")
        if not en_curso:
            return None
        c = self.cuestionarios.get(en_curso["id"])
        if c is None or not c.activo or c.version != en_curso["version"]:
            self.estado_alumno["cuestionario_en_curso"] = None
            self._guardar_estado()
            return None
        return c

    def _abrir_cuestionario(self, momento: str, tema: str | None = None) -> Cuestionario | None:
        """Abre el primer cuestionario que toca en este momento, si no hay otro a medias."""
        if not self.tiene_consentimiento() or self.estado_alumno.get("cuestionario_en_curso"):
            return None
        for c in self.cuestionarios.values():
            if toca(c, momento, self.estado_alumno, tema):
                self.estado_alumno["cuestionario_en_curso"] = {"id": c.id, "version": c.version, "respuestas": {}}
                self._guardar_estado()
                return c
        return None

    def _registrar_cuestionario(self, tipo: str, datos: dict) -> None:
        # no pertenece a ningún intento, aunque haya uno abierto
        if self.tiene_consentimiento():
            self.registro.registrar(self.codigo_activo, tipo, datos)

    def get_cuestionario_pendiente(self) -> tuple[int, dict]:
        c = (self._cuestionario_en_curso() or self._abrir_cuestionario("primer_ingreso")
             or self._abrir_cuestionario("despues_de_problemas"))
        if c is None:
            return 200, {"pendiente": False}
        indice, pregunta = c.siguiente(self.estado_alumno["cuestionario_en_curso"]["respuestas"])
        # cada vez que se entrega, la interfaz la muestra (por ejemplo, después de recargar)
        self._registrar_cuestionario("cuestionario_mostrado",
                                     {"cuestionario": c.id, "version": c.version, "item": pregunta.id})
        return 200, {"pendiente": True, "cuestionario": c.id, "version": c.version, "titulo": c.titulo,
                     "instrucciones": c.instrucciones, "indice_item": indice, "total_items": len(c.preguntas),
                     "item": pregunta.para_mostrar()}

    def post_cuestionario_respuesta(self, datos: dict) -> tuple[int, dict]:
        c = self._cuestionario_en_curso()
        if c is None:
            return 409, {"error": "No hay ningún cuestionario pendiente."}
        en_curso = self.estado_alumno["cuestionario_en_curso"]
        _, pregunta = c.siguiente(en_curso["respuestas"])
        # sólo se contesta la pregunta que se mostró, una vez y en orden
        if (datos.get("cuestionario"), datos.get("version"), datos.get("item")) != (c.id, c.version, pregunta.id):
            return 409, {"error": "Esa pregunta no es la que sigue."}
        valida, respuesta = validar_respuesta(pregunta, datos.get("respuesta"))
        if not valida:
            return 400, {"error": "Revisa tu respuesta."}
        ms = datos.get("ms")
        ms = round(ms) if type(ms) in (int, float) and math.isfinite(ms) and ms >= 0 else None
        self._registrar_cuestionario("cuestionario_respuesta", {"cuestionario": c.id, "version": c.version,
                                                                "item": pregunta.id, "respuesta": respuesta,
                                                                "ms": ms})
        en_curso["respuestas"][pregunta.id] = True
        completado = c.siguiente(en_curso["respuestas"]) is None
        if completado:
            self.estado_alumno["cuestionarios_respondidos"][c.id] = c.version
            self.estado_alumno["cuestionario_en_curso"] = None
        self._guardar_estado()
        return 200, {"ok": True, "completado": completado}

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

    def _evaluar_turno(self, texto: str, ruta: str, intencion: str, intencion_modelo: str | None = None,
                       intencion_ms: int | None = None, composicion: dict | None = None,
                       confianza: dict | None = None, boton: bool = False) -> dict:
        s = self.sesion_activa
        intento = self._intento()
        historial = [f"{m['rol']}: {m['texto']}" for m in intento["chat"]]
        # lo que veía el alumno al escribir, para el registro y la anotación
        paso, nivel, intentos_paso = s.step, s.level, s.step.data.get("_intentos", 0)
        pregunta_tutor = intento["chat"][-1]["texto"] if intento["chat"] else s.pregunta_actual()
        justificacion_pendiente = s.justify_pending is not None
        n_transcript = len(s.transcript)

        base = ""
        if intencion == "pregunta_teoria":
            # el motor no avanza
            conv = self._conversador()
            with self._turno_modelo():
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

        if intencion == "pregunta_teoria" or intencion in SIN_MOTOR:
            # ultimo_turno es del turno anterior: aquí el motor no evaluó nada
            motor = {"respondio": False, "paso_id": paso.id, "paso_tipo": paso.kind, "correcto": None,
                     "codigo": None, "nivel_antes": nivel, "nivel_despues": nivel, "pista": False,
                     "intentos_paso": intentos_paso, "terminado": s.finished}
        else:
            u = s.ultimo_turno
            motor = {"respondio": True, **{k: u[k] for k in ("paso_id", "paso_tipo", "correcto", "codigo",
                                                             "nivel_antes", "nivel_despues", "pista",
                                                             "intentos_paso", "terminado")}}
        sobre = self._registrar("turno", {
            "alumno": {"texto": texto, "caracteres": len(texto), "boton": boton},
            "composicion": composicion,
            "ruta": ruta,
            "intencion": {"etiqueta": intencion, "modelo": intencion_modelo, "ms": intencion_ms},
            "confianza": confianza,
            "motor": motor,
            "tutor": self._datos_tutor(red, base),
            "teoria": ({"fichas": [[f, round(p, 3)] for f, p in red.get("fichas", [])],
                        "encontro": red.get("encontro", False)} if intencion == "pregunta_teoria" else None),
        })
        if sobre is not None:
            self._encolar_anotacion(sobre, texto, intencion, paso, pregunta_tutor,
                                    justificacion_pendiente and any(m["rol"] == "justificacion"
                                                                    for m in s.transcript[n_transcript:]))

        indicadores = None
        if s.finished:
            indicadores = s.indicators()
            self._guardar_tiempo_activo()
            tema_terminado = self.problema_activo.topic
            self._registrar("intento_terminado", {**self._datos_intento(self.problema_activo, intento["modo"]),
                                                  "segundos_activos": intento["segundos_activos"],
                                                  "indicadores": indicadores})
            self._terminar_intento()
            if not self._abrir_cuestionario("al_terminar_tema", tema_terminado):
                self._abrir_cuestionario("despues_de_problemas")
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
        with self._turno_modelo():
            return conv.redactar(base, alumno, ctx)

    # ------------------------------------------------------------------ Anotación
    def _encolar_anotacion(self, sobre: dict, texto: str, intencion: str, paso, pregunta: str,
                           justifico: bool) -> None:
        if intencion != "respuesta":
            return
        if justifico:
            tipo = "justificacion"
            errores = [c for c in self.sesion_activa.c["errores"] if self.sesion_activa.bank.get(c)]
            idea = "Explica con sus palabras por qué su respuesta corregida es correcta."
            if errores:
                idea += f" El error que había cometido: {self.sesion_activa.bank.get(errores[-1]).descripcion}"
        elif paso.kind in PASOS_ANOTADOS and not HELP_RE.search(texto) and not DIRECT_RE.search(texto):
            tipo = PASOS_ANOTADOS[paso.kind]
            idea = paso.data.get("hint", "") if tipo == "concepto" else IDEAS_ESPERADAS[tipo]
        else:
            return
        self.estado_alumno.setdefault("cola_anotaciones", []).append({
            "seq_turno": sobre["seq"], "arranque_turno": sobre["arranque"], "intento": sobre["intento"],
            "tipo": tipo, "pregunta": pregunta, "idea_esperada": idea, "respuesta": texto, "fallos": 0,
        })

    def iniciar_anotaciones(self, intervalo: float = 2.0) -> None:
        """Hilo de baja prioridad que anota las respuestas abiertas cuando el modelo está libre."""
        def bucle():
            while True:
                time.sleep(intervalo)
                try:
                    self.anotar_pendiente()
                except Exception:
                    logger.exception("Error al anotar una respuesta")

        threading.Thread(target=bucle, daemon=True, name="anotaciones").start()

    def anotar_pendiente(self, reposo: float = ANOTACION_REPOSO) -> bool:
        """Anota la respuesta más antigua de la cola si el modelo lleva `reposo` s sin atender al alumno.
        No toca la sesión: el resultado sólo va al registro."""
        with self.lock_estado:
            if (not self.tiene_consentimiento() or not self.estado_alumno.get("cola_anotaciones")
                    or self._peticiones_alumno or time.monotonic() - self._ultimo_uso_modelo < reposo
                    or self.estado_modelo() != "listo"):
                return False
            tarea = self.estado_alumno["cola_anotaciones"].pop(0)
            codigo = self.codigo_activo
            self._guardar_estado()

        with self.lock_modelo:
            res = self.conversador.anotar(tarea["tipo"], tarea["pregunta"], tarea["idea_esperada"],
                                          tarea["respuesta"])

        if res is None and tarea["fallos"] < 1:
            with self.lock_estado:
                if self.codigo_activo == codigo:
                    tarea["fallos"] += 1
                    self.estado_alumno["cola_anotaciones"].insert(0, tarea)
                    self._guardar_estado()
                    return False
        sm = self.servidor_modelo
        self.registro.registrar(codigo, "anotacion", {
            "seq_turno": tarea["seq_turno"],
            "arranque_turno": tarea["arranque_turno"],
            "tipo": tarea["tipo"],
            "idea_esperada": tarea["idea_esperada"],
            "rubrica": res["rubrica"] if res else None,
            "ms": res["ms"] if res else None,
            "tokens_prompt": res["tokens_prompt"] if res else None,
            "tokens_salida": res["tokens_salida"] if res else None,
            "modelo": getattr(sm, "ruta_modelo", Path(MODELO_ARCHIVO)).name,
        }, intento=tarea["intento"])
        return res is not None
