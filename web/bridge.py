"""Puente entre la página web y el motor del tutor.

Se ejecuta dentro del navegador (Pyodide): cada alumno resuelve con su propio
dispositivo, sin servidor. Todas las funciones devuelven JSON.
"""
from __future__ import annotations

import json
import time
import uuid

from tutor import analysis as an
from tutor.circuit import CircuitError
from tutor.engine import MODES, Session
from tutor.problems import load_bank, parse_problem

TOPICS = {"ohm": "Ley de Ohm", "serie_paralelo": "Serie y paralelo", "mallas": "Método de mallas",
          "nodos": "Método de nodos"}

_bank = {p.id: p for p in load_bank("problemas")}
_state: dict = {}


def _describe(p) -> dict:
    c = p.circuit
    return {
        "id": p.id, "title": p.title, "topic": p.topic, "topicName": TOPICS.get(p.topic, p.topic),
        "level": p.level, "method": p.method, "statement": p.statement, "source": p.source,
        "elements": c.describe().splitlines(),
        "meshes": [f"Malla {k+1}: {', '.join(m)}" for k, m in enumerate(c.meshes)],
        "image": p.image,
    }


def problems() -> str:
    order = list(TOPICS)
    items = sorted(_bank.values(), key=lambda p: (order.index(p.topic) if p.topic in order else 9, p.id))
    return json.dumps({"problems": [_describe(p) for p in items], "modes": MODES}, ensure_ascii=False)


def start(pid: str, mode: str, custom: str = "", method: str = "") -> str:
    try:
        if pid:
            p = _bank[pid]
        else:
            head = f"METODO: {method}\n" if method else ""
            p = parse_problem(head + custom, "propio")
            if p.method == "mallas" and not p.circuit.meshes:
                raise CircuitError("Para el método de mallas declara las mallas como están dibujadas, "
                                   "una por renglón: MALLA 1: V1 R1 R2 (en sentido horario).")
        s = Session(p.circuit, p.method, mode)
    except (CircuitError, KeyError, ValueError) as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
    first = s.start()
    _state.clear()
    _state.update(session=s, problem=p, sid=uuid.uuid4().hex[:12], t0=time.time(), mode=mode)
    return json.dumps({"message": first, "problem": _describe(p), "sid": _state["sid"]}, ensure_ascii=False)


def reply(text: str) -> str:
    s: Session = _state["session"]
    msg = s.reply(text)
    out = {"message": msg, "finished": s.finished, "step": s.step.kind, "level": s.level}
    if s.finished:
        out["indicators"] = s.indicators()
    return json.dumps(out, ensure_ascii=False)


def record() -> str:
    """Registro de la sesión para la investigación (sin datos personales)."""
    if not _state:
        return "{}"
    s: Session = _state["session"]
    p = _state["problem"]
    return json.dumps({
        "sesion": _state["sid"], "problema": p.id, "metodo": s.method, "modo": _state["mode"],
        "inicio": _state["t0"], "fin": time.time(), "indicadores": s.indicators(),
        "transcripcion": s.transcript, "circuito": p.circuit.to_dict(),
    }, ensure_ascii=False)


def meshes_hint(custom: str) -> str:
    """Propuesta de mallas para un problema capturado (hay que confirmarla con el dibujo)."""
    try:
        p = parse_problem(custom, "propio")
        return json.dumps({"meshes": an.meshes_to_text(an.guess_meshes(p.circuit))}, ensure_ascii=False)
    except CircuitError as exc:
        return json.dumps({"error": str(exc)}, ensure_ascii=False)
