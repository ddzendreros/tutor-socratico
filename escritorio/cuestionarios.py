"""Cuestionarios: carga y validación de los archivos, respuestas y disparadores."""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from escritorio.rutas import RAIZ

logger = logging.getLogger("tutor_socratico.cuestionarios")

TIPOS = ("likert5", "opcion", "abierta", "numero")
DISPARADORES = ("primer_ingreso", "antes_de_tema", "despues_de_problemas", "al_terminar_tema", "manual")
CON_TEMA = ("antes_de_tema", "al_terminar_tema")
OBLIGATORIOS = ("ID", "TITULO", "VERSION", "ACTIVO", "CUANDO")
MAX_ABIERTA = 2000

_ENCABEZADO = re.compile(r"^(ID|TITULO|VERSION|ACTIVO|CUANDO|INSTRUCCIONES)\s*:\s*(.*)$")
_IDENT = re.compile(r"^[a-z0-9_]+$")


class ErrorCuestionario(Exception):
    def __init__(self, mensaje: str, linea: int | None = None):
        super().__init__(mensaje)
        self.linea = linea


@dataclass
class Pregunta:
    id: str
    tipo: str
    texto: str
    opciones: list[str] = field(default_factory=list)
    etiquetas: list[str] = field(default_factory=list)

    def para_mostrar(self) -> dict:
        d = {"id": self.id, "tipo": self.tipo, "texto": self.texto}
        if self.tipo == "likert5":
            d["etiquetas"] = self.etiquetas
        elif self.tipo == "opcion":
            d["opciones"] = self.opciones
        return d


@dataclass
class Cuestionario:
    id: str
    titulo: str
    version: int
    activo: bool
    cuando: str
    parametro: str | None
    instrucciones: str
    preguntas: list[Pregunta]

    def pregunta(self, item: str) -> Pregunta | None:
        return next((p for p in self.preguntas if p.id == item), None)

    def siguiente(self, respondidas: dict) -> tuple[int, Pregunta] | None:
        """La primera pregunta sin responder y su posición (desde 1)."""
        return next(((i, p) for i, p in enumerate(self.preguntas, 1) if p.id not in respondidas), None)


def _cuando(valor: str, linea: int, temas: set[str] | None) -> tuple[str, str | None]:
    disparador, _, parametro = (s.strip() for s in valor.partition(":"))
    if disparador not in DISPARADORES:
        raise ErrorCuestionario(f"CUANDO desconocido: '{disparador}'", linea)
    if disparador in CON_TEMA:
        if not parametro:
            raise ErrorCuestionario(f"{disparador} necesita el tema: {disparador}:<tema>", linea)
        if temas is not None and parametro not in temas:
            raise ErrorCuestionario(f"el tema '{parametro}' no está en el banco ({', '.join(sorted(temas))})", linea)
        return disparador, parametro
    if disparador == "despues_de_problemas":
        if not parametro.isdigit() or int(parametro) < 1:
            raise ErrorCuestionario("despues_de_problemas necesita un entero >= 1", linea)
        return disparador, parametro
    if parametro:
        raise ErrorCuestionario(f"{disparador} no lleva parámetro", linea)
    return disparador, None


def _pregunta(linea_txt: str, linea: int) -> Pregunta:
    partes = [p.strip() for p in linea_txt.split("|")]
    if len(partes) < 3 or len(partes) > 4:
        raise ErrorCuestionario("una pregunta es <id> | <tipo> | <texto> [| <opciones>]", linea)
    item, tipo, texto = partes[:3]
    extra = [s.strip() for s in partes[3].split(";") if s.strip()] if len(partes) == 4 else []
    if not re.fullmatch(r"[A-Za-z0-9_]+", item):
        raise ErrorCuestionario(f"identificador de pregunta inválido: '{item}'", linea)
    if tipo not in TIPOS:
        raise ErrorCuestionario(f"tipo de pregunta desconocido: '{tipo}'", linea)
    if not texto:
        raise ErrorCuestionario("la pregunta no tiene texto", linea)
    if tipo == "likert5":
        if len(extra) != 2:
            raise ErrorCuestionario("likert5 necesita las etiquetas del 1 y del 5, separadas por ';'", linea)
        return Pregunta(item, tipo, texto, etiquetas=extra)
    if tipo == "opcion":
        if len(extra) < 2 or len(set(extra)) != len(extra):
            raise ErrorCuestionario("opcion necesita dos o más opciones distintas, separadas por ';'", linea)
        return Pregunta(item, tipo, texto, opciones=extra)
    if extra:
        raise ErrorCuestionario(f"{tipo} no lleva opciones", linea)
    return Pregunta(item, tipo, texto)


def cargar_cuestionario(ruta: Path, temas: set[str] | None = None) -> Cuestionario:
    encabezado: dict[str, tuple[str, int]] = {}
    preguntas: list[Pregunta] = []
    for n, crudo in enumerate(Path(ruta).read_text(encoding="utf-8-sig").splitlines(), 1):
        linea = crudo.strip()
        if not linea or linea.startswith("#"):
            continue
        m = _ENCABEZADO.match(linea)
        if m:
            if m.group(1) in encabezado:
                raise ErrorCuestionario(f"{m.group(1)} está repetido", n)
            encabezado[m.group(1)] = (m.group(2).strip(), n)
        elif "|" in linea:
            p = _pregunta(linea, n)
            if any(q.id == p.id for q in preguntas):
                raise ErrorCuestionario(f"pregunta repetida: '{p.id}'", n)
            preguntas.append(p)
        else:
            raise ErrorCuestionario(f"renglón no reconocido: '{linea}'", n)

    faltan = [c for c in OBLIGATORIOS if c not in encabezado]
    if faltan:
        raise ErrorCuestionario(f"faltan campos: {', '.join(faltan)}")
    cid, n = encabezado["ID"]
    if not _IDENT.match(cid):
        raise ErrorCuestionario(f"ID inválido (minúsculas, números y _): '{cid}'", n)
    version, n = encabezado["VERSION"]
    if not version.isdigit() or int(version) < 1:
        raise ErrorCuestionario(f"VERSION debe ser un entero >= 1: '{version}'", n)
    activo, n = encabezado["ACTIVO"]
    if activo.lower() not in ("si", "sí", "no"):
        raise ErrorCuestionario(f"ACTIVO debe ser si o no: '{activo}'", n)
    cuando, parametro = _cuando(*encabezado["CUANDO"], temas)
    if not encabezado["TITULO"][0]:
        raise ErrorCuestionario("TITULO vacío", encabezado["TITULO"][1])
    if not preguntas:
        raise ErrorCuestionario("no hay preguntas")
    return Cuestionario(id=cid, titulo=encabezado["TITULO"][0], version=int(version),
                        activo=activo.lower() != "no", cuando=cuando, parametro=parametro,
                        instrucciones=encabezado.get("INSTRUCCIONES", ("", 0))[0], preguntas=preguntas)


def cargar_cuestionarios(carpeta: Path | None = None, temas: set[str] | None = None) -> dict[str, Cuestionario]:
    """Los cuestionarios válidos de la carpeta, en orden de archivo. Un archivo con errores se informa y se omite."""
    carpeta = Path(carpeta) if carpeta else RAIZ / "cuestionarios"
    cuestionarios: dict[str, Cuestionario] = {}
    if not carpeta.is_dir():
        return cuestionarios
    for ruta in sorted(carpeta.glob("*.txt")):
        try:
            c = cargar_cuestionario(ruta, temas)
            if c.id in cuestionarios:
                raise ErrorCuestionario(f"el ID '{c.id}' ya lo usa otro archivo")
        except (ErrorCuestionario, OSError, UnicodeDecodeError) as e:
            linea = f", línea {e.linea}" if getattr(e, "linea", None) else ""
            logger.error("Cuestionario omitido: %s%s: %s", ruta.name, linea, e)
            continue
        cuestionarios[c.id] = c
    return cuestionarios


def validar_respuesta(pregunta: Pregunta, respuesta: Any) -> tuple[bool, Any]:
    """(válida, valor normalizado)."""
    if isinstance(respuesta, bool):
        return False, None
    if pregunta.tipo == "likert5":
        if isinstance(respuesta, str) and respuesta.strip().isdigit():
            respuesta = int(respuesta.strip())
        if isinstance(respuesta, (int, float)) and respuesta in (1, 2, 3, 4, 5):
            return True, int(respuesta)
        return False, None
    if pregunta.tipo == "opcion":
        if isinstance(respuesta, str) and respuesta.strip() in pregunta.opciones:
            return True, respuesta.strip()
        return False, None
    if pregunta.tipo == "abierta":
        if isinstance(respuesta, str) and respuesta.strip() and len(respuesta) <= MAX_ABIERTA:
            return True, respuesta.strip()
        return False, None
    if pregunta.tipo == "numero":
        if isinstance(respuesta, str):
            try:
                respuesta = float(respuesta.strip().replace(",", "."))
            except ValueError:
                return False, None
        if isinstance(respuesta, (int, float)) and math.isfinite(respuesta):
            return True, int(respuesta) if float(respuesta).is_integer() else float(respuesta)
    return False, None


def toca(c: Cuestionario, momento: str, estado: dict, tema: str | None = None) -> bool:
    """Si el cuestionario se debe aplicar en este momento al alumno con este estado."""
    if not c.activo or c.cuando != momento:
        return False
    if estado.get("cuestionarios_respondidos", {}).get(c.id, 0) >= c.version:
        return False
    if momento in CON_TEMA:
        return tema == c.parametro
    if momento == "despues_de_problemas":
        return len(estado.get("terminados", [])) >= int(c.parametro)
    return momento == "primer_ingreso"
