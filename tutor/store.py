"""Registro de sesiones para la investigación (SQLite local) con seudonimización.

La boleta/ID del alumno NUNCA se guarda ni se envía a ningún LLM: se guarda
HMAC-SHA256(ID, SECRETO). Sin el SECRETO (que sólo conserva el profesor) no se
puede revertir, pero el mismo alumno siempre produce el mismo seudónimo, así
que se pueden seguir sus sesiones a lo largo del semestre.
"""
from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import sqlite3
import threading
import time
import uuid

_LOCK = threading.Lock()

PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[correo]"),
    (re.compile(r"\b\d{10}\b"), "[número]"),          # boletas IPN y teléfonos (10 dígitos)
    (re.compile(r"\b(?:\+?52\s?)?\d{2,3}[\s-]?\d{3,4}[\s-]?\d{4}\b"), "[teléfono]"),
]


def pseudonym(student_id: str, secret: str | None = None) -> str:
    secret = secret or os.getenv("PSEUDONYM_SECRET", "")
    if not secret:
        raise RuntimeError("Define PSEUDONYM_SECRET en el .env antes de registrar alumnos.")
    norm = re.sub(r"\s+", "", student_id).upper()
    return hmac.new(secret.encode(), norm.encode(), hashlib.sha256).hexdigest()[:16]


def scrub(text: str) -> str:
    """Quita correos/teléfonos/boletas antes de guardar o mandar texto a un LLM."""
    for pat, rep in PII_PATTERNS:
        text = pat.sub(rep, text)
    return text


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or os.getenv("DB_PATH", "data/sesiones.db")
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        with _LOCK:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sesiones (
              id TEXT PRIMARY KEY, alumno TEXT, grupo TEXT, metodo TEXT, modo TEXT,
              circuito TEXT, inicio REAL, fin REAL, indicadores TEXT, transcripcion TEXT);
            """)
            self.db.commit()

    def new_id(self) -> str:
        return uuid.uuid4().hex[:12]

    def save(self, sid: str, alumno: str, grupo: str, session) -> None:
        circuit = dict(session.circuit.to_dict(), problema=getattr(session, "problem_id", ""))
        row = (sid, alumno, grupo, session.method, session.mode,
               json.dumps(circuit, ensure_ascii=False),
               session.t0, time.time(),
               json.dumps(session.indicators(), ensure_ascii=False),
               json.dumps(session.transcript, ensure_ascii=False))
        with _LOCK:
            self.db.execute("INSERT OR REPLACE INTO sesiones VALUES (?,?,?,?,?,?,?,?,?,?)", row)
            self.db.commit()

    def export_csv(self) -> str:
        with _LOCK:
            rows = self.db.execute(
                "SELECT id, alumno, grupo, metodo, modo, inicio, fin, indicadores, circuito FROM sesiones "
                "ORDER BY inicio").fetchall()
        out = io.StringIO()
        keys: list[str] = []
        parsed = []
        for r in rows:
            ind = json.loads(r[7])
            for k in ind:
                if k not in keys:
                    keys.append(k)
            parsed.append((r, ind))
        w = csv.writer(out)
        w.writerow(["sesion", "alumno", "grupo", "problema", "metodo", "modo", "inicio", "fin"] + keys)
        for r, ind in parsed:
            problema = json.loads(r[8] or "{}").get("problema", "")
            w.writerow([r[0], r[1], r[2], problema, r[3], r[4],
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(r[5])),
                        time.strftime("%Y-%m-%d %H:%M", time.localtime(r[6]))]
                       + [ind.get(k, "") for k in keys])
        return out.getvalue()

    def export_transcripts(self) -> str:
        with _LOCK:
            rows = self.db.execute("SELECT id, alumno, grupo, transcripcion FROM sesiones").fetchall()
        return json.dumps([{"sesion": r[0], "alumno": r[1], "grupo": r[2],
                            "mensajes": json.loads(r[3])} for r in rows], ensure_ascii=False, indent=1)
