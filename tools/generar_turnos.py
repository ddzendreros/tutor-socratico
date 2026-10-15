"""Genera tools/datos_evaluacion/turnos.jsonl con turnos reales del motor.

Recorre los problemas del banco en los cuatro modos con respuestas correctas,
errores y peticiones de ayuda, y elige turnos variados: errores con pista,
ayudas, aciertos y justificaciones. Cada turno guarda todo lo que escribió el
alumno hasta ese momento, para reconstruir la sesión con `reconstruir()` y
obtener del motor el contexto de la guardia al evaluar.

Uso: python -m tools.generar_turnos
     python -m tools.generar_turnos --agregar thevenin    # añade al final los turnos de esos problemas
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from tests.test_flow import correct_answer  # el recorrido correcto que ya usan las pruebas
from tutor.engine import MODES, Session
from tutor.problems import load_bank

SALIDA = Path(__file__).resolve().parent / "datos_evaluacion" / "turnos.jsonl"
RUIDO = ["no sé", "cinco", "I1 = 3 A", "dame una pista", "R1 y R2 en serie", "creo que es la de arriba",
         "V1 = 0", "I1 + I2", "la misma", "ayuda", "0", "es fantasma"]
CUOTAS = {"error": 14, "ayuda": 10, "acierto": 12, "justificacion": 3, "otro": 3}


def tipo_de_turno(s: Session) -> str:
    u = s.ultimo_turno
    if s.transcript[-2]["rol"] == "justificacion":
        return "justificacion"
    if u["correcto"] is False:
        return "error"
    if u["pista"]:
        return "ayuda"
    if u["correcto"] is True:
        return "acierto"
    return "otro"


# diálogo de clase de tests/test_flow.py: incluye un error, la pregunta "¿por qué?" y la justificación
CLASE_MALLAS = ["Me dan dos fuentes de 100 V y tres resistencias, necesito V, I y P de cada una",
                "dos", "VX, R1, R2 y VY", "VY, R2 y R3", "R2 y VY", "I1 + I2", "2 A", "I1 - I2",
                "porque las corrientes van en sentidos opuestos en esa rama"]


def respuesta_erronea(s: Session) -> str:
    """La respuesta correcta con un error típico, para que el motor diagnostique y dé pista."""
    bien, k = correct_answer(s), s.step.kind
    if k in ("mesh_count", "node_count"):
        return str(int(bien) + 1)
    if k in ("mesh_types", "node_types"):
        return bien.replace("real", "X").replace("fantasma", "real").replace("X", "fantasma")
    if k == "reduce":
        return bien.replace("en serie", "en X").replace("en paralelo", "en serie").replace("en X", "en paralelo")
    if k == "equations":
        return re.sub(r"\((-?)", lambda m: "(" + ("" if m.group(1) else "-"), bien, count=1)
    if k in ("values", "targets", "table", "sim", "submit"):
        return re.sub(r"(?<![\w.])\d+(\.\d+)?", lambda m: f"{2 * float(m.group(0)):g}", bien)
    if k in ("shared_id", "mesh_define") and ", " in bien:
        return bien.rsplit(", ", 1)[0]
    return RUIDO[len(s.entradas) % len(RUIDO)]


def recorrer(problema, modo: str, guion: list[str] | None = None) -> list[dict]:
    s = Session(problema.circuit, problema.method, modo)
    s.start()
    turnos = []
    for k in range(len(guion) if guion else 150):
        if s.finished:
            break
        if guion:
            texto = guion[k]
        elif k % 3 != 1:
            texto = correct_answer(s)
        else:
            texto = respuesta_erronea(s) if (k // 3) % 2 == 0 else RUIDO[(k // 6) % len(RUIDO)]
        base = s.reply(texto)
        turnos.append({"problema": problema.id, "modo": modo, "entradas": list(s.entradas),
                       "alumno": texto, "base": base, "tipo": tipo_de_turno(s)})
    return turnos


def elegir(sesiones: list[list[dict]], cuotas: dict[str, int] = CUOTAS, vistos: set[str] | None = None) -> list[dict]:
    """Toma de cada tipo su cuota, un turno por sesión a la vez y sin repetir mensajes base,
    para repartirlos entre problemas y modos."""
    elegidos, vistos = [], set(vistos or ())
    for tipo, cuota in cuotas.items():
        colas = [[t for t in turnos if t["tipo"] == tipo] for turnos in sesiones]
        tomados = 0
        while tomados < cuota and any(colas):
            for cola in colas:
                while cola and cola[0]["base"] in vistos:
                    cola.pop(0)
                if cola and tomados < cuota:
                    t = cola.pop(0)
                    vistos.add(t["base"])
                    elegidos.append(t)
                    tomados += 1
    return elegidos


def agregar(prefijos: list[str]) -> None:
    """Añade al final del archivo los turnos de los problemas nuevos, sin tocar los que ya hay."""
    with open(SALIDA, encoding="utf-8") as f:
        existentes = [json.loads(linea) for linea in f if linea.strip()]
    banco = [p for p in sorted(load_bank(), key=lambda p: p.id) if any(p.id.startswith(x) for x in prefijos)]
    modos = list(MODES)
    sesiones = [recorrer(p, modos[(i + j) % len(modos)]) for i, p in enumerate(banco) for j in range(2)]
    n = len(banco)
    nuevos = elegir(sesiones, {"error": 2 * n, "ayuda": n, "acierto": 2 * n}, {t["base"] for t in existentes})
    faltan = {p.id for p in banco} - {t["problema"] for t in nuevos}
    if faltan:
        raise SystemExit(f"sin turnos para: {', '.join(sorted(faltan))}")
    with open(SALIDA, "a", encoding="utf-8") as f:
        for t in nuevos:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"{len(nuevos)} turnos añadidos a {SALIDA}")


def main() -> None:
    if len(sys.argv) > 2 and sys.argv[1] == "--agregar":
        return agregar(sys.argv[2:])
    banco = sorted(load_bank(), key=lambda p: p.id)
    modos = list(MODES)
    # cada vuelta recorre los 12 problemas, cada uno en un modo distinto
    orden = sorted(((i + j) % len(modos), i, j) for i in range(len(banco)) for j in range(len(modos)))
    clase = next(p for p in banco if p.id.startswith("mallas-01"))
    sesiones = [recorrer(clase, "aprender", CLASE_MALLAS)]
    sesiones += [recorrer(banco[i], modos[j]) for _, i, j in orden]
    turnos = elegir(sesiones)
    with open(SALIDA, "w", encoding="utf-8") as f:
        for t in turnos:
            f.write(json.dumps(t, ensure_ascii=False) + "\n")
    print(f"{len(turnos)} turnos en {SALIDA}")


if __name__ == "__main__":
    main()
