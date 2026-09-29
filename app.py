"""Interfaz web del Tutor Socrático de Circuitos (Gradio).

Ejecutar:  python app.py          (abre en http://localhost:7860)
"""
from __future__ import annotations

import os
import tempfile

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from tutor import analysis as an  # noqa: E402
from tutor.circuit import CircuitError, parse_mesh_spec, parse_rows  # noqa: E402
from tutor.engine import METHODS, MODES, Session  # noqa: E402
from tutor.errorbank import ErrorBank  # noqa: E402
from tutor.llm import LLM  # noqa: E402
from tutor.problems import load_bank, parse_targets, targets_to_text  # noqa: E402
from tutor.store import Store, pseudonym, scrub  # noqa: E402

BANK = ErrorBank()
PROBLEMS = {p.id: p for p in load_bank()}
LLM_CLIENT = LLM()
STORE = Store()
# Por defecto el tutor usa las plantillas del banco de errores: todos los alumnos
# reciben la misma intervención, que es lo que conviene para la investigación.
REPHRASE = os.getenv("LLM_REPHRASE", "0") == "1"
TEACHER_PASSWORD = os.getenv("TEACHER_PASSWORD", "")
GROUPS = [g.strip() for g in os.getenv("GROUPS", "").split(",") if g.strip()]

HEADERS = ["Elemento", "Nodo 1 (+ / origen)", "Nodo 2", "Valor"]
TOPICS = {"ohm": "Ley de Ohm", "serie_paralelo": "Serie/paralelo", "mallas": "Mallas", "nodos": "Nodos"}
OWN = "__propio__"

INTRO = """## Tutor Socrático · Circuitos Eléctricos
El tutor **no te da la respuesta**: te hace preguntas y te da pistas graduadas para que tú la construyas.

<small>Tus datos: tu boleta se convierte en un código irreversible; no se guarda ni se envía a
ningún servicio externo. Tus respuestas se usan, sin tu nombre, para investigación educativa.</small>"""

CAPTURE_HELP = """**Cómo capturar el circuito:** una fila por elemento. `R` resistencia (Ω), `V` fuente de
voltaje (el **Nodo 1 es la terminal +**), `I` fuente de corriente (la flecha va del Nodo 1 al Nodo 2).
El nodo de referencia se llama `0`. Valores con prefijos: `2.2k`, `500m`.

**Mallas (sólo para el método de mallas):** una por renglón, con los elementos en el orden en que
los recorre la corriente **en sentido horario**, como en tu dibujo. Ejemplo: `MALLA 1: V1 R1 R2`."""


def problem_choices():
    items = sorted(PROBLEMS.values(), key=lambda p: (list(TOPICS).index(p.topic)
                                                      if p.topic in TOPICS else 9, p.id))
    return ([(f"{TOPICS.get(p.topic, p.topic)} · {p.label}", p.id) for p in items]
            + [("Capturar otro problema…", OWN)])


def guess_method(circuit, targets) -> str:
    meshes = len(circuit.elements) - len(circuit.nodes) + 1
    sources = len(circuit.by_kind("V")) + len(circuit.by_kind("I"))
    if not targets:
        return "mallas"
    if len(circuit.by_kind("R")) == 1 and sources == 1:
        return "ohm"
    if sources == 1 and not circuit.by_kind("I"):
        return "serie_paralelo"
    return "mallas" if meshes > 1 else "serie_paralelo"


def rows_from_df(df) -> list[list[str]]:
    if df is None:
        return []
    rows = df.values.tolist() if hasattr(df, "values") else df
    return [[str(x) for x in r] for r in rows if any(str(x).strip() for x in r)]


# ------------------------------------------------------------------ eventos
def on_pick(pid):
    if not pid or pid == OWN:
        return ("", [["", "", "", ""]], "", "", "auto", CAPTURE_HELP, gr.update(open=True))
    p = PROBLEMS[pid]
    c = p.circuit
    note = f"**{p.title}** — {p.source}"   # NOTA es para el profesor: no se muestra al alumno
    return (p.statement, [e.to_row() for e in c.elements], targets_to_text(c.targets),
            an.meshes_to_text(an.default_meshes(c)) if c.meshes else "", p.method, note,
            gr.update(open=False))


def on_extract(statement):
    if not statement.strip():
        return gr.update(), "", "auto", "Escribe o pega el enunciado del problema."
    c, method, notes = LLM_CLIENT.extract_circuit(scrub(statement))
    if c is None:
        return gr.update(), "", "auto", f"⚠️ {notes}"
    rows = [e.to_row() for e in c.elements]
    msg = ("**Revisa la tabla con cuidado:** ¿coincide con tu circuito (valores, nodos y polaridades)? "
           "Corrígela si hace falta antes de empezar.")
    if notes:
        msg += f"\n\n📝 {notes}"
    return rows, targets_to_text(c.targets), method or "auto", msg


def on_start(boleta, grupo, consent, pid, statement, df, targets_txt, meshes_txt, method, mode, state, chat):
    if not consent:
        raise gr.Error("Para usar el tutor necesitas aceptar el aviso de uso de datos.")
    if not boleta.strip():
        raise gr.Error("Escribe tu boleta.")
    try:
        alumno = pseudonym(boleta)
    except RuntimeError as exc:
        raise gr.Error(str(exc))
    try:
        c = parse_rows(rows_from_df(df))
        c.targets = parse_targets(targets_txt)
        c.statement = scrub(statement or "")
        c.meshes = parse_mesh_spec(meshes_txt or "")
        c.validate()
        m = guess_method(c, c.targets) if method == "auto" else method
        if m == "mallas" and not c.meshes:
            # sin el dibujo no se sabe cuáles lazos son las mallas: se proponen y se confirman
            proposal = an.meshes_to_text(an.guess_meshes(c))
            return (state, chat, gr.update(), proposal,
                    "⚠️ **Confirma las mallas.** Propuse unas a partir de la tabla, pero pueden no "
                    "coincidir con tu dibujo. Revísalas (elementos en sentido horario) y vuelve a "
                    "presionar *Empezar*.")
        if c.meshes:
            an.meshes_from_spec(c)
        s = Session(c, m, mode, BANK)
    except CircuitError as exc:
        raise gr.Error(str(exc))
    s.problem_id = "" if pid in (None, OWN) else pid
    sid = STORE.new_id()
    first = s.start()
    head = f"**{PROBLEMS[pid].title}**\n\n" if s.problem_id else ""
    circuit_md = f"{head}{c.statement}\n\n**Circuito:**\n{c.describe()}".strip()
    chat = [{"role": "assistant", "content": circuit_md}, {"role": "assistant", "content": first}]
    STORE.save(sid, alumno, grupo or "", s)
    state = {"session": s, "sid": sid, "alumno": alumno, "grupo": grupo or ""}
    return state, chat, gr.update(interactive=True, value=""), meshes_txt, ""


def on_send(text, state, chat):
    if not state or not text.strip():
        return state, chat, ""
    s: Session = state["session"]
    clean = scrub(text)
    base = s.reply(clean)
    shown = base
    if REPHRASE and LLM_CLIENT.enabled and not s.finished:
        known = {float(e.value) for e in s.circuit.elements}
        shown = LLM_CLIENT.rephrase(base, clean, s.forbidden_numbers(), s.pending_equations(),
                                    allow_equations=s.level >= 4, var_names=s.vars, known=known)
        if shown != base:
            s.transcript[-1]["mostrado"] = shown
            s.transcript[-1]["llm"] = LLM_CLIENT.last_provider
    chat = chat + [{"role": "user", "content": text}, {"role": "assistant", "content": shown}]
    if s.finished:
        ind = s.indicators()
        table = "\n".join(f"| {k} | {v} |" for k, v in ind.items())
        chat.append({"role": "assistant",
                     "content": f"**Registro de la interacción**\n\n| Indicador | Registro |\n|---|---|\n{table}"})
    STORE.save(state["sid"], state["alumno"], state["grupo"], s)
    return state, chat, ""


def on_export(password):
    if not TEACHER_PASSWORD or password != TEACHER_PASSWORD:
        raise gr.Error("Contraseña incorrecta.")
    d = tempfile.mkdtemp()
    p1, p2 = os.path.join(d, "indicadores.csv"), os.path.join(d, "transcripciones.json")
    with open(p1, "w", encoding="utf-8-sig") as fh:
        fh.write(STORE.export_csv())
    with open(p2, "w", encoding="utf-8") as fh:
        fh.write(STORE.export_transcripts())
    return [p1, p2]


# --------------------------------------------------------------------- UI
with gr.Blocks(title="Tutor Socrático · Circuitos") as demo:
    state = gr.State(None)
    gr.Markdown(INTRO)
    with gr.Row():
        boleta = gr.Textbox(label="Boleta", type="password", scale=2)
        grupo = (gr.Dropdown(GROUPS, label="Grupo", scale=1) if GROUPS
                 else gr.Textbox(label="Grupo", scale=1))
        consent = gr.Checkbox(label="Acepto el uso de mis respuestas (sin mi nombre) para investigación",
                              scale=2)
    with gr.Row():
        problem = gr.Dropdown(problem_choices(), label="1 · Elige el problema", scale=3)
        mode = gr.Dropdown([(v, k) for k, v in MODES.items()], value="aprender", label="Modo", scale=1)
    note = gr.Markdown()
    statement = gr.Textbox(label="Enunciado", lines=3, interactive=True)
    with gr.Accordion("Circuito (tabla y mallas)", open=False) as capture:
        btn_extract = gr.Button("Interpretar enunciado con IA", variant="secondary")
        table = gr.Dataframe(value=[["", "", "", ""]], headers=HEADERS, column_count=(4, "fixed"),
                             row_count=(3, "dynamic"), interactive=True, label="Elementos")
        meshes = gr.Textbox(label="Mallas, como están dibujadas (sentido horario)", lines=3,
                            placeholder="MALLA 1: V1 R1 R2\nMALLA 2: R2 R3 V2")
        targets = gr.Textbox(label="¿Qué pide el problema?",
                             placeholder="VIP (tabla de V, I y P) · I R2 · P R3 · Req V1 · vacío = corrientes de malla o voltajes de nodo")
        method = gr.Dropdown([("Automático", "auto")] + [(v, k) for k, v in METHODS.items()],
                             value="auto", label="Método")
    btn_start = gr.Button("Empezar", variant="primary")
    chatbot = gr.Chatbot(label="Tutor", height=520)
    msg = gr.Textbox(label="Tu respuesta", placeholder="Ej.: I1(10 + 20) − I2(20) = 0", interactive=False)
    with gr.Accordion("Panel del profesor", open=False):
        pwd = gr.Textbox(label="Contraseña", type="password")
        btn_exp = gr.Button("Descargar indicadores y transcripciones")
        files = gr.File(label="Archivos", file_count="multiple")

    problem.change(on_pick, [problem], [statement, table, targets, meshes, method, note, capture])
    btn_extract.click(on_extract, [statement], [table, targets, method, note])
    btn_start.click(on_start, [boleta, grupo, consent, problem, statement, table, targets, meshes, method,
                               mode, state, chatbot],
                    [state, chatbot, msg, meshes, note])
    msg.submit(on_send, [msg, state, chatbot], [state, chatbot, msg])
    btn_exp.click(on_export, [pwd], [files])

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=int(os.getenv("CONCURRENCY", "40")))
    demo.launch(server_name=os.getenv("HOST", "0.0.0.0"), server_port=int(os.getenv("PORT", "7860")),
                share=os.getenv("GRADIO_SHARE", "0") == "1")
