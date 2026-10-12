"""Banco de pruebas de modelos de lenguaje para el tutor.

Por cada modelo GGUF arranca llama-server con los parámetros de la aplicación, con pocos
hilos para simular un CPU modesto, y alterna las dos ranuras como en el uso real. Mide:
- pico de RAM del proceso (peak_wset) y tiempo de carga;
- latencia de redacción (incluye el reintento) y de intención; tokens/s de generación;
- guardia al primer intento y al final, eco (copia el mensaje base sin cambios) y números
  que el modelo escribió sin que estuvieran en el mensaje base ni en el circuito;
- exactitud de intención y cuántas respuestas reales se reconocen como respuesta;
- tokens reutilizados de la caché por ranura y peticiones fallidas.

La guardia recibe el contexto que da el motor: cada turno de turnos.jsonl se reconstruye
con `reconstruir()` a partir de lo que escribió el alumno.

Salidas en --salida (por omisión salida/evaluacion, fuera de git):
  modelos.csv              métricas por modelo
  detalle-<modelo>.jsonl   cada redacción e intención, para revisarlas
  muestras.md              los mismos turnos con cada modelo, identificados con letras al azar
  clave.txt                qué letra es cada modelo (no abrirlo antes de comparar)

Uso: python -m tools.evaluar_modelos --modelos vendor/modelos/a.gguf vendor/modelos/b.gguf
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

import psutil

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from escritorio.conversacion import EJEMPLOS_VOZ, Conversador, contexto_guardia  # noqa: E402
from escritorio.modelo import ServidorModelo  # noqa: E402
from tutor.engine import reconstruir  # noqa: E402
from tutor.llm import LLM  # noqa: E402
from tutor.mathparse import parse_numbers  # noqa: E402
from tutor.problems import load_bank  # noqa: E402

RUTA_INTENCIONES = _RAIZ / "tools" / "datos_evaluacion" / "intenciones.csv"
RUTA_TURNOS = _RAIZ / "tools" / "datos_evaluacion" / "turnos.jsonl"
N_MUESTRA = 15
HILOS_MEDICION = 2
# criterios obligatorios para equipos de 8 GB; la licencia se revisa a mano
RAM_MAX_MB, GUARDIA_MIN, INTENCION_MIN, MEDIANA_MAX_MS = 3000, 85, 85, 15000

CAMPOS = [
    "modelo", "error", "cumple_obligatorios", "falla_en", "ram_pico_mb", "carga_ms", "variante_cpu",
    "redaccion_ms_mediana", "redaccion_ms_p90", "intencion_ms_mediana", "tokens_s_mediana",
    "guardia_final_pct", "reformulado_pct", "guardia_primer_intento_pct", "eco_pct", "sin_numeros_ajenos_pct",
    "intencion_pct", "respuesta_reconocida_pct", "cache_redaccion_mediana", "cache_intencion_mediana",
    "peticiones_fallidas", "turnos_desfasados", "n_redacciones", "n_intenciones", "motivos_rechazo",
]


def ruta(p: str) -> Path:
    q = Path(p)
    return (q if q.is_absolute() else _RAIZ / q).resolve()


def percentil(datos: list[float], p: float) -> float:
    if not datos:
        return 0.0
    orden = sorted(datos)
    i = (len(orden) - 1) * p / 100
    bajo, alto = int(i), min(int(i) + 1, len(orden) - 1)
    return orden[bajo] + (i - bajo) * (orden[alto] - orden[bajo])


def mediana(datos: list[float]) -> float:
    return statistics.median(datos) if datos else 0


def normalizar(texto: str) -> str:
    return re.sub(r"\W+", " ", texto.casefold()).strip()


def cargar_intenciones() -> list[dict]:
    with open(RUTA_INTENCIONES, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def preparar_turnos() -> tuple[list[dict], int]:
    """Reconstruye cada turno con el motor; descarta los que ya no dan el mismo mensaje base
    y los que usan un mensaje base de los ejemplos del prompt."""
    banco = {p.id: p for p in load_bank()}
    bases_de_ejemplo = {b for _, b, _ in EJEMPLOS_VOZ}
    listos, desfasados = [], 0
    with open(RUTA_TURNOS, encoding="utf-8") as f:
        turnos = [json.loads(linea) for linea in f if linea.strip()]
    for t in turnos:
        if t["base"] in bases_de_ejemplo:
            continue
        p = banco[t["problema"]]
        s = reconstruir(copy.deepcopy(p.circuit), p.method, t["modo"], t["entradas"])
        if s.ultimo_turno["mensaje_base"] != t["base"]:
            desfasados += 1
            continue
        previos = [m for m in s.transcript[:-2] if m["rol"] in ("alumno", "tutor", "justificacion")][-6:]
        ctx = contexto_guardia(s)
        ctx.update(titulo_problema=p.title, descripcion_paso=s.pregunta_actual(), ruta="motor",
                   intencion="respuesta",
                   ultimos_intercambios=[("Tutor: " if m["rol"] == "tutor" else "Alumno: ") + m["texto"]
                                         for m in previos])
        listos.append({**t, "ctx": ctx})
    return listos, desfasados


def numeros_ajenos(texto: str, base: str, ctx: dict) -> list[float]:
    permitidos = {round(n.value, 4) for n in parse_numbers(base)} | {0, 1, 2, 3}
    permitidos |= {round(k, 4) for k in ctx.get("known", set())}
    return [n.value for n in parse_numbers(texto) if round(n.value, 4) not in permitidos]


def evaluar_modelo(ruta_modelo: Path, ruta_servidor: Path, hilos: int, salida: Path,
                   turnos: list[dict], intenciones: list[dict]) -> tuple[dict, list[dict]]:
    servidor = ServidorModelo(ruta_modelo, ruta_servidor, ruta_log=salida / f"registro-{ruta_modelo.stem}.log",
                              ruta_pid=salida / f"registro-{ruta_modelo.stem}.pid", hilos=hilos)
    fila = {"modelo": ruta_modelo.name, "error": ""}
    print(f"\n{ruta_modelo.name}: cargando…", flush=True)
    if not servidor.arrancar():
        fila.update(error="no arrancó; ver la bitácora", cumple_obligatorios=False, falla_en="arranque")
        return fila, []
    print(f"  listo en {servidor.ms_carga} ms (CPU {servidor.variante_cpu})", flush=True)

    llm = LLM(timeout=60.0)
    llm.configurar(servidor.url_base, servidor.llave)
    peticiones = []
    chat_original = llm.chat

    def chat(*args, **kwargs):
        out, m = chat_original(*args, **kwargs)
        peticiones.append({"ranura": kwargs.get("id_slot", 0), "ok": out is not None, **m})
        return out, m

    llm.chat = chat
    conv = Conversador(llm)
    redacciones, aciertos_int, detalle = [], [], []

    for i in range(max(len(turnos), len(intenciones))):
        if i < len(turnos):
            t = turnos[i]
            r = conv.redactar(t["base"], t["alumno"], t["ctx"])
            primero = r["intentos"][0]["texto"] if r["intentos"] else ""
            redacciones.append({"r": r, "base": t["base"], "eco": normalizar(primero) == normalizar(t["base"]),
                                "ajenos": numeros_ajenos(primero, t["base"], t["ctx"])})
            detalle.append({"tipo": "redaccion", "problema": t["problema"], "turno": t["tipo"],
                            "alumno": t["alumno"], "base": t["base"], "mostrado": r["texto"],
                            "fuente": r["fuente"], "intentos": r["intentos"], "ms": r["ms"]})
        if i < len(intenciones):
            it = intenciones[i]
            res = conv.entender(it["texto"], it.get("pregunta", ""))
            obtenida = res["intencion"] if res else None
            aciertos_int.append((it["intencion"], obtenida, res["ms"] if res else 0))
            detalle.append({"tipo": "intencion", "texto": it["texto"], "esperada": it["intencion"],
                            "obtenida": obtenida})
        print(f"  {i + 1}/{max(len(turnos), len(intenciones))}", end="\r", flush=True)

    fila["ram_pico_mb"] = round(getattr(psutil.Process(servidor.pid).memory_info(), "peak_wset",
                                        psutil.Process(servidor.pid).memory_info().rss) / 2**20, 1)
    servidor.detener()

    n_red, n_int = len(redacciones), len(aciertos_int)
    pct = lambda n, d: round(100 * n / d, 1) if d else 0.0  # noqa: E731
    respuestas = [(e, o) for e, o, _ in aciertos_int if e == "respuesta"]
    motivos = Counter(x["motivo"] for d in redacciones for x in d["r"]["intentos"] if not x["ok"])
    fila.update({
        "carga_ms": servidor.ms_carga,
        "variante_cpu": servidor.variante_cpu,
        "redaccion_ms_mediana": round(mediana([d["r"]["ms"] for d in redacciones])),
        "redaccion_ms_p90": round(percentil([d["r"]["ms"] for d in redacciones], 90)),
        "intencion_ms_mediana": round(mediana([ms for _, _, ms in aciertos_int if ms])),
        "tokens_s_mediana": round(mediana([p["predicted_per_second"] for p in peticiones
                                           if p.get("predicted_per_second")]), 1),
        "guardia_final_pct": pct(sum(d["r"]["fuente"] == "modelo" for d in redacciones), n_red),
        # lo que de verdad aporta el modelo: se mostró su texto y no es el mensaje base
        "reformulado_pct": pct(sum(d["r"]["fuente"] == "modelo" and normalizar(d["r"]["texto"]) != normalizar(d["base"])
                                   for d in redacciones), n_red),
        "guardia_primer_intento_pct": pct(sum(bool(d["r"]["intentos"]) and d["r"]["intentos"][0]["ok"]
                                              for d in redacciones), n_red),
        "eco_pct": pct(sum(d["eco"] for d in redacciones), n_red),
        "sin_numeros_ajenos_pct": pct(sum(not d["ajenos"] for d in redacciones), n_red),
        "intencion_pct": pct(sum(e == o for e, o, _ in aciertos_int), n_int),
        "respuesta_reconocida_pct": pct(sum(e == o for e, o in respuestas), len(respuestas)),
        "cache_redaccion_mediana": round(mediana([p.get("tokens_cache", 0) for p in peticiones
                                                  if p["ranura"] == 0 and p["ok"]])),
        "cache_intencion_mediana": round(mediana([p.get("tokens_cache", 0) for p in peticiones
                                                  if p["ranura"] == 1 and p["ok"]])),
        "peticiones_fallidas": sum(not p["ok"] for p in peticiones),
        "n_redacciones": n_red,
        "n_intenciones": n_int,
        "motivos_rechazo": "; ".join(f"{m} ({n})" for m, n in motivos.most_common(4)),
    })
    fallas = [nombre for nombre, falla in (
        ("RAM", fila["ram_pico_mb"] > RAM_MAX_MB), ("guardia", fila["guardia_final_pct"] < GUARDIA_MIN),
        ("intención", fila["intencion_pct"] < INTENCION_MIN),
        ("latencia", fila["redaccion_ms_mediana"] > MEDIANA_MAX_MS)) if falla]
    fila.update(cumple_obligatorios=not fallas, falla_en=", ".join(fallas))
    confusion = Counter((e, o) for e, o, _ in aciertos_int if e != o)
    print(f"  RAM {fila['ram_pico_mb']} MB | guardia {fila['guardia_final_pct']} % | "
          f"intención {fila['intencion_pct']} % | mediana {fila['redaccion_ms_mediana']} ms", flush=True)
    print("  confusiones:", ", ".join(f"{e}→{o} ({n})" for (e, o), n in confusion.most_common(6)) or "ninguna")
    return fila, detalle


def escribir_muestras(turnos: list[dict], por_modelo: dict[str, list[dict]], salida: Path) -> None:
    nombres = list(por_modelo)
    random.SystemRandom().shuffle(nombres)
    letras = {n: chr(ord("A") + i) for i, n in enumerate(nombres)}
    md = ["# Muestras de redacción (comparación a ciegas)", "",
          "Cada turno muestra lo que escribió el alumno, el mensaje base del motor y lo que mostró cada "
          "modelo. Cuando la guardia rechazó al modelo se muestra el mensaje base y se indica.", ""]
    for i, t in enumerate(turnos[:N_MUESTRA], 1):
        md += [f"## Turno {i}", f"- **Alumno:** {t['alumno']}", f"- **Base:** {t['base']}", ""]
        for n in sorted(nombres, key=letras.get):
            redaccion = next((d for d in por_modelo[n] if d["tipo"] == "redaccion" and d["base"] == t["base"]), None)
            if redaccion is None:
                continue
            nota = " *(se mostró el mensaje base)*" if redaccion["fuente"] == "plantilla" else ""
            md.append(f"- **Modelo {letras[n]}:** {redaccion['mostrado']}{nota}")
        md.append("")
    (salida / "muestras.md").write_text("\n".join(md), encoding="utf-8")
    (salida / "clave.txt").write_text("\n".join(f"Modelo {letras[n]} = {n}" for n in nombres), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Banco de pruebas de modelos de lenguaje")
    parser.add_argument("--modelos", nargs="+", required=True, help="archivos .gguf a evaluar")
    parser.add_argument("--servidor", default="vendor/llama/llama-server.exe")
    parser.add_argument("--hilos", type=int, default=HILOS_MEDICION)
    parser.add_argument("--salida", default="salida/evaluacion")
    args = parser.parse_args(argv)

    ruta_servidor, salida = ruta(args.servidor), ruta(args.salida)
    if not ruta_servidor.exists():
        print(f"No se encontró llama-server en {ruta_servidor}", file=sys.stderr)
        return 1
    salida.mkdir(parents=True, exist_ok=True)
    turnos, desfasados = preparar_turnos()
    intenciones = cargar_intenciones()
    print(f"{len(turnos)} turnos ({desfasados} desfasados con el motor) y {len(intenciones)} intenciones")

    filas, por_modelo = [], {}
    for m in args.modelos:
        rm = ruta(m)
        if not rm.exists():
            filas.append({"modelo": rm.name, "error": "archivo no encontrado", "cumple_obligatorios": False})
            continue
        fila, detalle = evaluar_modelo(rm, ruta_servidor, args.hilos, salida, turnos, intenciones)
        fila["turnos_desfasados"] = desfasados
        filas.append(fila)
        if detalle:
            por_modelo[rm.name] = detalle
            with open(salida / f"detalle-{rm.stem}.jsonl", "w", encoding="utf-8") as f:
                for d in detalle:
                    f.write(json.dumps(d, ensure_ascii=False) + "\n")

    with open(salida / "modelos.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS)
        w.writeheader()
        w.writerows({c: fila.get(c, "") for c in CAMPOS} for fila in filas)
    if por_modelo:
        escribir_muestras(turnos, por_modelo, salida)
    print(f"\nResultados en {salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
