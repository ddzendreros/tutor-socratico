"""Herramienta de conversación en consola para el Tutor Socrático.

Permite interactuar en terminal con el motor pedagógico y la capa de voz
de modelos de lenguaje.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tutor.engine import MODES, Session
from tutor.problems import load_bank
from tutor.llm import LLM
from escritorio.config import PASOS_DE_CONCEPTO
from escritorio.conversacion import Conversador, contexto_guardia, plantilla_para_intencion
from escritorio.modelo import ServidorModelo


def buscar_modelo_predeterminado() -> Path | None:
    """Busca el primer modelo .gguf disponible en vendor/modelos o la raíz."""
    carpetas = [Path("vendor/modelos"), Path("modelo"), Path(".")]
    for carpeta in carpetas:
        if carpeta.exists():
            for archivo in carpeta.glob("*.gguf"):
                return archivo.resolve()
    return None


def buscar_problema(pid: str):
    banco = {p.id: p for p in load_bank()}
    if pid in banco:
        return banco[pid]
    for k, v in banco.items():
        if k.startswith(pid):
            return v
    raise ValueError(f"Problema '{pid}' no encontrado en el banco.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Conversación en terminal con el Tutor Socrático")
    parser.add_argument(
        "--problema",
        default="mallas-01-caso1-malla-real",
        help="ID del problema (ej. mallas-01-caso1-malla-real)",
    )
    parser.add_argument(
        "--modo",
        default="aprender",
        choices=list(MODES),
        help="Modo pedagógico de la sesión",
    )
    parser.add_argument(
        "--sin-modelo",
        action="store_true",
        help="Ejecuta únicamente con las plantillas del motor, sin iniciar llama-server",
    )
    parser.add_argument(
        "--modelo",
        default=None,
        help="Ruta al archivo .gguf del modelo a utilizar",
    )
    args = parser.parse_args()

    try:
        problema = buscar_problema(args.problema)
    except Exception as e:
        print(f"Error cargando el problema '{args.problema}': {e}", file=sys.stderr)
        return 1

    sesion = Session(problema.circuit, problema.method, mode=args.modo)
    servidor = None
    llm = None

    if not args.sin_modelo:
        ruta_mod = Path(args.modelo) if args.modelo else buscar_modelo_predeterminado()
        if ruta_mod and ruta_mod.exists():
            print(f"Iniciando servidor de lenguaje con {ruta_mod.name}...")
            servidor = ServidorModelo(ruta_modelo=ruta_mod)
            if servidor.arrancar():
                print(f"Servidor listo en {servidor.url_base} (carga: {servidor.ms_carga} ms, CPU: {servidor.variante_cpu})")
                llm = LLM(base_url=servidor.url_base)
                llm.configurar(base_url=servidor.url_base, llave=servidor.llave)
            else:
                print("No se pudo iniciar el servidor de lenguaje. Se usarán plantillas.")
        else:
            print("No se encontró archivo de modelo .gguf. Se usarán plantillas.")

    conversador = Conversador(llm=llm)

    try:
        mensaje_inicial = sesion.start()
        print("\n" + "=" * 60)
        print(f"Problema: {problema.title} | Modo: {args.modo.capitalize()}")
        print("Escribe 'salir' para terminar la conversación.")
        print("=" * 60 + "\n")
        print(f"Tutor: {mensaje_inicial}\n")

        historial: list[str] = [f"Tutor: {mensaje_inicial}"]

        while not sesion.finished:
            try:
                entrada = input("Alumno > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nSesión interrumpida.")
                break

            if not entrada:
                continue
            if entrada.lower() in ("salir", "exit", "quit"):
                print("Sesión finalizada por el alumno.")
                break

            ruta = sesion.ruta_rapida(entrada)
            intencion = "respuesta"

            if ruta == "ambiguo" and llm and llm.enabled:
                info_int = conversador.entender(entrada, sesion.pregunta_actual())
                if info_int:
                    intencion = info_int.get("intencion", "respuesta")
                else:
                    intencion = "respuesta"
            # en un paso de concepto la ficha contiene la respuesta: la duda se atiende como pista
            if intencion == "pregunta_teoria" and sesion.step.kind in PASOS_DE_CONCEPTO:
                intencion = "ayuda"

            if intencion == "ayuda":
                base = sesion.reply("pista")
            elif intencion in ("pregunta_paso", "animo", "fuera_de_tema", "charla"):
                base = plantilla_para_intencion(intencion, sesion.pregunta_actual())
            elif intencion == "pregunta_teoria":
                ctx = contexto_guardia(sesion)
                ctx.update(titulo_problema=problema.title, descripcion_paso=sesion.pregunta_actual(),
                           ultimos_intercambios=historial, ruta=ruta, intencion=intencion)
                resp_teoria = conversador.responder_teoria(entrada, sesion.pregunta_actual(), ctx)
                texto_tutor = resp_teoria["texto"]
                fuente = resp_teoria["fuente"]
                historial += [f"Alumno: {entrada}", f"Tutor: {texto_tutor}"]
                print(f"\nTutor [{fuente}]: {texto_tutor}\n")
                continue
            else:
                base = sesion.reply(entrada)

            # Redacción con modelo o plantilla; la guardia usa lo que sabe el motor tras el turno
            ctx = contexto_guardia(sesion)
            ctx.update(titulo_problema=problema.title, descripcion_paso=sesion.pregunta_actual(),
                       ultimos_intercambios=historial, ruta=ruta, intencion=intencion)

            redaccion = conversador.redactar(base, entrada, ctx)
            texto_tutor = redaccion["texto"]
            fuente = redaccion["fuente"]

            historial += [f"Alumno: {entrada}", f"Tutor: {texto_tutor}"]
            print(f"\nTutor [{fuente}]: {texto_tutor}\n")

        if sesion.finished:
            print("¡Has completado el problema!")

    finally:
        if servidor is not None:
            print("Deteniendo servidor de modelo...")
            servidor.detener()

    return 0


if __name__ == "__main__":
    sys.exit(main())
