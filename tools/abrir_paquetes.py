"""Lector de los paquetes que exportan los alumnos.

Uso:
  python -m tools.abrir_paquetes --llave llave_privada.tsk --entrada <carpeta con .zip> --salida <carpeta>

Descifra cada .zip con la llave privada, revisa su integridad, junta los eventos de todos los paquetes
(sin duplicados) y escribe en la carpeta de salida las tablas en CSV (UTF-8 con BOM, separadas por comas),
los eventos en JSONL, el diccionario de datos y un LEEME.md. Sólo lee: nunca modifica los paquetes.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import json
import re
import statistics
import sys
import unicodedata
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

# permite ejecutarlo directamente como script
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from nacl.public import PrivateKey  # noqa: E402

from escritorio.alumnos import normalizar  # noqa: E402
from escritorio.cifrado import abrir_sellado, cargar_llave_privada, huella_publica, tsd_lotes  # noqa: E402

FORMATO_PAQUETE = "tutor-paquete-1"
ARCHIVOS_PAQUETE = ("paquete.json", "manifiesto.tsd", "eventos.tsd")

# los 16 indicadores del motor, tal como los entrega Session.indicators()
INDICADORES = [
    ("Intentos del estudiante", "entero", "", "Respuestas del estudiante que el motor evaluó."),
    ("Preguntas socráticas realizadas", "entero", "", "Preguntas socráticas que hizo el tutor."),
    ("Pistas nivel 1", "entero", "", "Pistas de nivel 1 dadas."),
    ("Pistas nivel 2", "entero", "", "Pistas de nivel 2 dadas."),
    ("Pistas nivel 3 o superiores", "entero", "", "Pistas de nivel 3, 4 o 5 dadas."),
    ("Errores conceptuales", "entero", "", "Errores clasificados como conceptuales."),
    ("Errores procedimentales", "entero", "", "Errores clasificados como procedimentales."),
    ("Corrección autónoma", "texto", "", "Sí si corrigió un error sin que el tutor se lo señalara."),
    ("Solicitó respuesta directa", "texto", "", "Sí si pidió que el tutor le diera la respuesta."),
    ("Verificó con simulador", "texto", "", "Sí si verificó su resultado con un simulador."),
    ("Verificó con balance de potencias", "texto", "", "Sí si verificó con el balance de potencias (PE = PC)."),
    ("Justificaciones dadas", "entero", "", "Justificaciones que dio después de corregir un error."),
    ("Nivel final de ayuda", "entero", "", "Nivel de ayuda (0 a 5) al terminar el intento."),
    ("Códigos de error", "texto", "", "Códigos de error del banco que cometió, separados por comas."),
    ("Completado", "texto", "", "Sí si terminó el problema."),
    ("Duración (min)", "decimal", "min", "Minutos desde que empezó el problema hasta que lo terminó."),
]


def _snake(nombre: str) -> str:
    sin_acentos = unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", sin_acentos.lower()).strip("_")


COLUMNAS_INDICADORES = [f"ind_{_snake(n)}" for n, *_ in INDICADORES]

# ------------------------------------------------------------------ tablas (una sola definición)
# cada columna: (nombre, tipo, unidad, descripción). Con esto se escriben las tablas y el diccionario.

_SOBRE = [
    ("codigo", "texto", "", "Código del alumno (TS-XXXX-C)."),
    ("instalacion", "texto", "", "Identificador de la instalación del programa (el equipo)."),
    ("arranque", "texto", "", "Identificador del arranque del programa."),
    ("seq", "entero", "", "Número del evento, consecutivo por alumno y arranque; empieza en 1."),
    ("t", "fecha_hora", "", "Fecha y hora local del evento (ISO 8601 con zona)."),
    ("ms", "entero", "ms", "Hora del evento en milisegundos desde 1970."),
    ("intento", "texto", "", "Intento abierto (<problema>:<modo>); vacío si no había ninguno."),
]


def _sobre(sin: tuple[str, ...] = ()) -> list[tuple]:
    return [c for c in _SOBRE if c[0] not in sin]


def _comp(nombre: str, tipo: str, unidad: str, desc: str) -> tuple:
    return (f"comp_{nombre}", tipo, unidad, desc)


TABLAS: dict[str, list[tuple]] = {
    "eventos.csv": [
        ("v", "entero", "", "Versión del esquema de eventos."),
        ("seq", "entero", "", "Número del evento, consecutivo por alumno y arranque; empieza en 1."),
        ("t", "fecha_hora", "", "Fecha y hora local del evento (ISO 8601 con zona)."),
        ("ms", "entero", "ms", "Hora del evento en milisegundos desde 1970."),
        ("mono", "decimal", "ms", "Milisegundos desde el arranque del programa (reloj monotónico)."),
        ("codigo", "texto", "", "Código del alumno (TS-XXXX-C)."),
        ("instalacion", "texto", "", "Identificador de la instalación del programa (el equipo)."),
        ("arranque", "texto", "", "Identificador del arranque del programa."),
        ("intento", "texto", "", "Intento abierto (<problema>:<modo>); vacío si no había ninguno."),
        ("origen", "texto", "", "app (lo escribe el programa) o ui (viene de la interfaz)."),
        ("tipo", "texto", "", "Tipo de evento."),
        ("datos", "json", "", "Contenido del evento (esquema por tipo en docs/DATOS.md)."),
    ],
    "sesiones_uso.csv": [
        ("codigo", "texto", "", "Código del alumno."),
        ("instalacion", "texto", "", "Identificador de la instalación."),
        ("arranque", "texto", "", "Identificador del arranque del programa."),
        ("inicio", "fecha_hora", "", "Hora de sesion_iniciada."),
        ("fin", "fecha_hora", "", "Hora de la sesion_terminada que le corresponde; vacío si no hay."),
        ("ms", "entero", "ms", "Duración de la sesión, medida por el programa."),
        ("motivo", "texto", "", "Cómo terminó: boton, inactividad, cierre o cambio."),
        ("modo", "texto", "", "ventana (ventana propia) o navegador."),
        ("version_consentimiento", "texto", "", "Versión del aviso de consentimiento vigente."),
        ("intentos_iniciados", "entero", "", "Eventos intento_iniciado dentro de la sesión (nuevos y retomados)."),
        ("turnos", "entero", "", "Turnos (mensajes del alumno o clics en Pista) dentro de la sesión."),
    ],
    "intentos.csv": [
        ("codigo", "texto", "", "Código del alumno."),
        ("instalacion", "texto", "", "Identificador de la instalación."),
        ("intento", "texto", "", "Clave del intento (<problema>:<modo>)."),
        ("problema", "texto", "", "Identificador del problema; propio si el alumno lo capturó."),
        ("tema", "texto", "", "Tema del problema."),
        ("metodo", "texto", "", "Método de solución."),
        ("modo", "texto", "", "Modo de trabajo: aprender, practicar, verificar o simulador."),
        ("inicio", "fecha_hora", "", "Hora del primer evento del intento."),
        ("fin", "fecha_hora", "", "Hora del último evento del intento."),
        ("terminado", "booleano", "", "Si el alumno terminó el problema (hay intento_terminado)."),
        ("sesiones", "entero", "", "Veces que se abrió el intento (nuevo y retomados)."),
        ("segundos_activos", "decimal", "s", "Tiempo con el problema abierto, sumando sesiones (último valor)."),
        ("turnos", "entero", "", "Turnos del intento (incluye clics en Pista)."),
        ("turnos_modelo", "entero", "", "Turnos cuyo mensaje redactó el modelo de lenguaje."),
        ("pct_plantilla", "decimal", "%", "Porcentaje de turnos cuyo mensaje salió de una plantilla."),
        ("pistas_boton", "entero", "", "Pistas pedidas con el botón."),
        ("ayudas", "entero", "", "Turnos atendidos con la intención ayuda (botón o texto)."),
        ("preguntas_teoria", "entero", "", "Turnos con la intención pregunta_teoria."),
        ("salidas_ventana", "entero", "", "Veces que la ventana perdió el foco (ventana desenfocada)."),
        ("ms_fuera", "entero", "ms", "Tiempo con la ventana desenfocada u oculta, hasta volver a enfocarla."),
        ("pegados", "entero", "", "Pegados en los mensajes del alumno."),
        ("copias", "entero", "", "Eventos de copiar."),
        ("confianza_n", "entero", "", "Turnos con confianza contestada (con valor)."),
        ("confianza_media", "decimal", "1-5", "Media de la confianza contestada."),
        ("calibracion", "decimal", "0-1", "Media de (valor-1)/4 menos la media de aciertos; positiva = exceso de confianza."),
        ("brier", "decimal", "0-1", "Media de ((valor-1)/4 - acierto)^2: error de la confianza como pronóstico."),
        *[(col, tipo, unidad, f"Indicador «{nombre}» al terminar; vacío si no terminó. {desc}")
          for col, (nombre, tipo, unidad, desc) in zip(COLUMNAS_INDICADORES, INDICADORES)],
    ],
    "turnos.csv": [
        *_sobre(),
        ("alumno_texto", "texto", "", "Mensaje del alumno, ya sin datos personales."),
        ("alumno_caracteres", "entero", "", "Caracteres del mensaje."),
        ("alumno_boton", "booleano", "", "Si vino del botón Pista y no de un mensaje escrito."),
        _comp("ms_desde_tutor", "entero", "ms", "Desde que apareció el último mensaje del tutor hasta el envío."),
        _comp("ms_primera_tecla", "entero", "ms", "Desde que apareció el mensaje del tutor hasta la primera tecla."),
        _comp("ms_escribiendo", "entero", "ms", "Desde la primera edición hasta el envío."),
        _comp("teclas", "entero", "", "Teclas (sin contar el Enter que envía)."),
        _comp("borrados", "entero", "", "Retrocesos y Supr."),
        _comp("pegados", "entero", "", "Pegados en el mensaje."),
        _comp("caracteres_pegados", "entero", "", "Caracteres pegados."),
        _comp("pausas_2s", "entero", "", "Pausas de 2 s o más entre dos ediciones."),
        _comp("foco_perdido", "entero", "", "Veces que salió de la ventana con el mensaje a medias."),
        _comp("teclas_sin_texto", "booleano", "", "Si las teclas se guardaron sin su texto por haber un dato personal."),
        ("ruta", "texto", "", "Quién decidió cómo atender el mensaje: motor o ambiguo (se preguntó al modelo)."),
        ("intencion_etiqueta", "texto", "", "Intención con la que se atendió el mensaje."),
        ("intencion_modelo", "texto", "", "Intención que dijo el modelo; vacío si no se le preguntó."),
        ("intencion_ms", "entero", "ms", "Lo que tardó el modelo en clasificar la intención."),
        ("confianza_preguntada", "booleano", "", "Si en este turno se preguntó la confianza."),
        ("confianza_valor", "entero", "1-5", "Confianza contestada; vacío si no se preguntó o prefirió no decir."),
        ("confianza_ms", "entero", "ms", "Lo que tardó en contestar la confianza."),
        ("motor_respondio", "booleano", "", "Si el motor evaluó el mensaje (falso: plantilla o respuesta de teoría)."),
        ("motor_paso_id", "texto", "", "Paso en el que estaba el alumno."),
        ("motor_paso_tipo", "texto", "", "Tipo del paso."),
        ("motor_correcto", "booleano", "", "Si la respuesta fue correcta; vacío si el motor no evaluó."),
        ("motor_codigo", "texto", "", "OK o código de error del banco; vacío si no evaluó."),
        ("motor_nivel_antes", "entero", "0-5", "Nivel de ayuda antes del turno."),
        ("motor_nivel_despues", "entero", "0-5", "Nivel de ayuda después del turno."),
        ("motor_pista", "booleano", "", "Si el turno fue una pista."),
        ("motor_intentos_paso", "entero", "", "Intentos del alumno en el paso."),
        ("motor_terminado", "booleano", "", "Si el problema terminó con este turno."),
        ("tutor_texto_base", "texto", "", "Mensaje del motor o de la plantilla, antes de redactarse."),
        ("tutor_texto_mostrado", "texto", "", "Mensaje que vio el alumno."),
        ("tutor_fuente", "texto", "", "modelo, plantilla o ficha."),
        ("tutor_guardia_ok", "booleano", "", "Si la guardia aceptó el mensaje final."),
        ("tutor_guardia_motivo", "texto", "", "Motivo con el que la guardia rechazó o aceptó."),
        ("tutor_reintentos", "entero", "", "Reintentos de redacción."),
        ("tutor_intentos", "json", "", "Cada texto del modelo con el veredicto de la guardia."),
        ("tutor_ms", "entero", "ms", "Tiempo de redacción."),
        ("tutor_tokens_prompt", "entero", "tokens", "Tokens de la instrucción."),
        ("tutor_tokens_salida", "entero", "tokens", "Tokens generados."),
        ("tutor_tokens_cache", "entero", "tokens", "Tokens que salieron de la caché."),
        ("teoria_fichas", "json", "", "Fichas de teoría recuperadas, como [ficha, puntaje]."),
        ("teoria_encontro", "booleano", "", "Si había una ficha sobre el umbral."),
    ],
    "teclas.csv": [
        ("codigo", "texto", "", "Código del alumno."),
        ("instalacion", "texto", "", "Identificador de la instalación."),
        ("arranque", "texto", "", "Identificador del arranque del programa."),
        ("seq_turno", "entero", "", "seq del turno al que pertenece la tecla."),
        ("orden", "entero", "", "Orden de la edición dentro del mensaje (desde 1)."),
        ("dt_ms", "entero", "ms", "Tiempo desde la primera edición del mensaje."),
        ("tipo", "texto", "", "inputType de la edición (insertText, deleteContentBackward, paste…)."),
        ("texto", "texto", "", "Texto insertado; vacío en borrados y en mensajes con datos personales."),
        ("posicion", "entero", "", "Posición del cursor después de la edición."),
    ],
    "anotaciones.csv": [
        *_sobre(sin=("ms",)),
        ("seq_turno", "entero", "", "seq del turno que se anotó."),
        ("arranque_turno", "texto", "", "Arranque del turno que se anotó (puede ser otro)."),
        ("tipo", "texto", "", "justificacion, concepto, verificacion o metacognicion."),
        ("idea_esperada", "texto", "", "Referencia que recibió el modelo."),
        ("respuesta", "texto", "", "Texto del alumno en el turno anotado; vacío si ese turno no está en los paquetes."),
        ("rubrica_expresa_idea", "texto", "", "si, parcial o no; vacío si el modelo no respondió."),
        ("rubrica_claridad", "entero", "1-3", "Claridad de la respuesta."),
        ("rubrica_usa_terminos_tecnicos", "booleano", "", "Si usa términos técnicos."),
        ("rubrica_idea_erronea", "texto", "", "Idea errónea que detectó el modelo (hasta 100 caracteres)."),
        ("rubrica_tipo", "texto", "", "explicacion, repeticion, vago o fuera_de_tema."),
        ("ms", "entero", "ms", "Lo que tardó el modelo en anotar."),
        ("tokens_prompt", "entero", "tokens", "Tokens de la instrucción."),
        ("tokens_salida", "entero", "tokens", "Tokens generados."),
        ("modelo", "texto", "", "Archivo del modelo."),
    ],
    "equipos.csv": [
        ("instalacion", "texto", "", "Identificador de la instalación (el equipo)."),
        ("codigos", "texto", "", "Códigos de alumnos que usaron el equipo, separados por «;»."),
        ("sesiones", "entero", "", "Sesiones iniciadas."),
        ("primera", "fecha_hora", "", "Hora del primer evento."),
        ("ultima", "fecha_hora", "", "Hora del último evento."),
        ("so", "texto", "", "Sistema operativo (de la última sesión)."),
        ("version_so", "texto", "", "Versión del sistema operativo."),
        ("build", "entero", "", "Compilación de Windows."),
        ("plataforma", "texto", "", "Descripción completa de la plataforma."),
        ("cpu", "texto", "", "Procesador."),
        ("nucleos_fisicos", "entero", "", "Núcleos físicos."),
        ("nucleos_logicos", "entero", "", "Núcleos lógicos."),
        ("ram_total_gb", "decimal", "GB", "Memoria total."),
        ("ram_disponible_gb", "decimal", "GB", "Memoria disponible al entrar."),
        ("disco_libre_gb", "decimal", "GB", "Espacio libre en la unidad de los datos."),
        ("pantalla_ancho", "entero", "px", "Ancho de la pantalla (píxeles CSS)."),
        ("pantalla_alto", "entero", "px", "Alto de la pantalla (píxeles CSS)."),
        ("pantalla_escala", "decimal", "", "Escala de la pantalla."),
        ("webview2", "texto", "", "Versión de WebView2; vacío si no está."),
        ("modo", "texto", "", "ventana o navegador."),
        ("variante_cpu", "texto", "", "Biblioteca de llama.cpp que eligió el procesador."),
        ("app", "texto", "", "Versión del programa (la última registrada)."),
        ("modelo", "texto", "", "Archivo del modelo (el último registrado)."),
        ("modelo_sha256", "texto", "", "SHA-256 corto del modelo (el último registrado)."),
        ("hilos", "entero", "", "Hilos de llama-server."),
        ("ctx", "entero", "tokens", "Contexto de llama-server."),
        ("ms_carga_mediana", "decimal", "ms", "Mediana de lo que tardó en cargar el modelo, por arranque."),
        ("ms_precalentar_mediana", "decimal", "ms", "Mediana de lo que tardó en precalentar el modelo, por arranque."),
        ("voz_ms_mediana", "decimal", "ms", "Mediana del tiempo de redacción, en turnos donde se llamó al modelo."),
        ("voz_ms_p90", "decimal", "ms", "Percentil 90 (rango más cercano) del tiempo de redacción."),
        ("voz_tokens_s_mediana", "decimal", "tokens/s", "Mediana de tokens generados por segundo al redactar."),
        ("intencion_ms_mediana", "decimal", "ms", "Mediana del tiempo de clasificar la intención."),
        ("anotacion_ms_mediana", "decimal", "ms", "Mediana del tiempo de anotar una respuesta."),
        ("pct_plantilla", "decimal", "%", "Porcentaje de turnos cuyo mensaje salió de una plantilla."),
    ],
    "cuestionarios.csv": [
        ("codigo", "texto", "", "Código del alumno."),
        ("instalacion", "texto", "", "Identificador de la instalación."),
        ("arranque", "texto", "", "Identificador del arranque del programa."),
        ("seq", "entero", "", "Número del evento."),
        ("t", "fecha_hora", "", "Hora de la respuesta."),
        ("cuestionario", "texto", "", "Identificador del cuestionario."),
        ("version", "texto", "", "Versión del cuestionario."),
        ("item", "texto", "", "Pregunta."),
        ("respuesta", "texto", "", "Respuesta del alumno."),
        ("ms", "entero", "ms", "Lo que tardó en contestar."),
    ],
    "integridad.csv": [
        ("zip", "texto", "", "Paquete revisado; vacío si el problema surge al juntar varios."),
        ("tipo", "texto", "", "paquete, formato, llave, manifiesto, sha256, lotes, lote_ilegible, "
                              "bytes_incompletos, codigo, conflicto, hueco o faltan_al_final."),
        ("codigo", "texto", "", "Código del alumno afectado."),
        ("instalacion", "texto", "", "Instalación afectada."),
        ("arranque", "texto", "", "Arranque afectado."),
        ("desde", "entero", "", "Primer seq afectado."),
        ("hasta", "entero", "", "Último seq afectado."),
        ("detalle", "texto", "", "Explicación (en las filas paquete, el número de eventos)."),
    ],
    "diccionario.csv": [
        ("archivo", "texto", "", "Archivo de salida."),
        ("columna", "texto", "", "Nombre de la columna."),
        ("tipo", "texto", "", "texto, entero, decimal, booleano (true/false), fecha_hora o json."),
        ("unidad", "texto", "", "Unidad o escala; vacío si no aplica."),
        ("descripcion", "texto", "", "Qué contiene."),
    ],
}

DESCRIPCION_ARCHIVOS = {
    "eventos.jsonl": "Todos los eventos sin duplicados, uno por línea, tal como se registraron.",
    "eventos.csv": "Los mismos eventos: el sobre en columnas y el contenido como JSON.",
    "sesiones_uso.csv": "Una fila por sesión de uso.",
    "intentos.csv": "Una fila por intento de un problema, con sus indicadores.",
    "turnos.csv": "Una fila por turno, con todo menos las teclas.",
    "teclas.csv": "Cada edición del cuadro de texto, en formato largo.",
    "anotaciones.csv": "Rúbrica del modelo sobre respuestas abiertas.",
    "equipos.csv": "Una fila por equipo, con sus características y tiempos del modelo.",
    "cuestionarios.csv": "Respuestas a cuestionarios (sólo encabezado si no hay).",
    "integridad.csv": "Un renglón por paquete y uno por cada problema de integridad.",
    "diccionario.csv": "Qué contiene cada columna de cada archivo.",
    "LEEME.md": "Este resumen.",
}


# ------------------------------------------------------------------ un paquete

def _problema(zip_: str, tipo: str, detalle: str, **campos) -> dict:
    fila = {c: "" for c, *_ in TABLAS["integridad.csv"]}
    fila.update(zip=zip_, tipo=tipo, detalle=detalle, **campos)
    return fila


def abrir_paquete(ruta_zip: Path, privada: PrivateKey) -> tuple[list[dict], list[dict], dict | None]:
    """Abre un .zip y devuelve (eventos, problemas, manifiesto). El manifiesto es None si no se pudo abrir.

    Cada problema es una fila de integridad.csv. No lanza error por un paquete dañado: lo reporta.
    """
    ruta_zip = Path(ruta_zip)
    nombre = ruta_zip.name
    problemas: list[dict] = []
    eventos: list[dict] = []

    def reportar(tipo: str, detalle: str, **campos) -> None:
        problemas.append(_problema(nombre, tipo, detalle, **campos))

    try:
        zf = zipfile.ZipFile(ruta_zip)
    except (zipfile.BadZipFile, OSError) as e:
        reportar("formato", f"No se pudo abrir el .zip: {e}")
        return eventos, problemas, None

    with zf:
        presentes = set(zf.namelist())

        def leer(archivo: str) -> bytes | None:
            try:
                return zf.read(archivo)
            except (KeyError, zipfile.BadZipFile, OSError) as e:
                reportar("formato", f"No se pudo leer {archivo}: {e}")
                return None

        for archivo in ARCHIVOS_PAQUETE:
            if archivo not in presentes:
                reportar("formato", f"Falta {archivo}.")

        crudo = leer("paquete.json") if "paquete.json" in presentes else None
        if crudo is not None:
            try:
                paquete = json.loads(crudo.decode("utf-8"))
                if not isinstance(paquete, dict) or paquete.get("formato") != FORMATO_PAQUETE:
                    raise ValueError(f"formato {paquete.get('formato') if isinstance(paquete, dict) else paquete!r}")
            except ValueError as e:
                reportar("formato", f"paquete.json no es un {FORMATO_PAQUETE}: {e}")
                return eventos, problemas, None
            esperada = huella_publica(privada.public_key)
            if paquete.get("huella_llave") != esperada:
                reportar("llave", f"El paquete se cifró con la llave {paquete.get('huella_llave')!r}; "
                                  f"esta llave es {esperada!r}.")
                return eventos, problemas, None

        manifiesto = None
        crudo = leer("manifiesto.tsd") if "manifiesto.tsd" in presentes else None
        if crudo is not None:
            try:
                manifiesto = json.loads(abrir_sellado(crudo, privada).decode("utf-8"))
                if not isinstance(manifiesto, dict):
                    raise ValueError("no es un objeto")
            except Exception as e:  # sellado dañado, zlib o JSON: todo es "no se pudo abrir"
                manifiesto = None
                reportar("manifiesto", f"No se pudo abrir el manifiesto: {e}")
        ident = {"codigo": manifiesto.get("codigo", ""), "instalacion": manifiesto.get("instalacion", "")} \
            if manifiesto else {}

        datos = leer("eventos.tsd") if "eventos.tsd" in presentes else None
        if datos is None:
            return eventos, problemas, manifiesto
        try:
            lotes, sobrante = tsd_lotes(datos)
        except ValueError as e:
            reportar("formato", str(e), **ident)
            return eventos, problemas, manifiesto

        if manifiesto:
            if hashlib.sha256(datos).hexdigest() != manifiesto.get("sha256_eventos"):
                reportar("sha256", "El SHA-256 de eventos.tsd no coincide con el del manifiesto.", **ident)
            if len(lotes) != manifiesto.get("lotes"):
                reportar("lotes", f"El manifiesto dice {manifiesto.get('lotes')} lotes y el archivo trae "
                                  f"{len(lotes)}.", **ident)
        if sobrante:
            reportar("bytes_incompletos", f"eventos.tsd trae {sobrante} bytes sobrantes al final.", **ident)

        for i, lote in enumerate(lotes, 1):
            try:
                texto = abrir_sellado(lote, privada).decode("utf-8")
                de_lote = [json.loads(linea) for linea in texto.splitlines() if linea.strip()]
            except Exception as e:
                reportar("lote_ilegible", f"Lote {i} de {len(lotes)}: {type(e).__name__}: {e}", **ident)
                continue
            eventos += de_lote

        if manifiesto:
            esperado = normalizar(str(manifiesto.get("codigo", "")))
            raros: dict[str, list[int]] = defaultdict(list)
            for e in eventos:
                if normalizar(str(e.get("codigo", ""))) != esperado:
                    raros[str(e.get("codigo"))].append(e.get("seq", 0))
            for codigo, seqs in raros.items():
                reportar("codigo", f"{len(seqs)} eventos son de {codigo} y el manifiesto dice "
                                   f"{manifiesto.get('codigo')}.", codigo=codigo,
                         instalacion=manifiesto.get("instalacion", ""), desde=min(seqs), hasta=max(seqs))
    return eventos, problemas, manifiesto


# ------------------------------------------------------------------ juntar paquetes

def _canonico(evento: dict) -> str:
    return json.dumps(evento, sort_keys=True, ensure_ascii=False)


def _ordenar(eventos: list[dict]) -> list[dict]:
    """Por equipo, alumno y arranque (en el orden en que empezaron) y seq. El id del arranque es aleatorio."""
    primero: dict[tuple, int] = {}
    for e in eventos:
        k = (e["instalacion"], e["codigo"], e["arranque"])
        primero[k] = min(primero.get(k, e["ms"]), e["ms"])
    return sorted(eventos, key=lambda e: (e["instalacion"], e["codigo"],
                                          primero[(e["instalacion"], e["codigo"], e["arranque"])],
                                          e["arranque"], e["seq"]))


def _huecos(eventos: list[dict]) -> list[dict]:
    seqs: dict[tuple, set] = defaultdict(set)
    for e in eventos:
        seqs[(e["instalacion"], e["codigo"], e["arranque"])].add(e["seq"])
    filas = []
    for (instalacion, codigo, arranque), vistos in sorted(seqs.items()):
        desde = None
        for n in range(1, max(vistos) + 2):
            if n in vistos or n == max(vistos) + 1:
                if desde is not None:
                    filas.append(_problema("", "hueco", f"Faltan los eventos {desde} a {n - 1}.", codigo=codigo,
                                           instalacion=instalacion, arranque=arranque, desde=desde, hasta=n - 1))
                    desde = None
            elif desde is None:
                desde = n
    return filas


def _faltan_al_final(eventos: list[dict], manifiestos: list[tuple[str, dict]]) -> list[dict]:
    maximos: dict[tuple, int] = defaultdict(int)
    for e in eventos:
        k = (e["instalacion"], e["codigo"], e["arranque"])
        maximos[k] = max(maximos[k], e["seq"])
    filas = []
    for nombre, m in manifiestos:
        for rango in m.get("rangos", []):
            k = (m.get("instalacion", ""), m.get("codigo", ""), rango.get("arranque", ""))
            if maximos[k] < rango.get("seq_hasta", 0):
                filas.append(_problema(
                    nombre, "faltan_al_final",
                    f"El manifiesto declara eventos hasta {rango['seq_hasta']} y sólo hay hasta {maximos[k]}.",
                    codigo=k[1], instalacion=k[0], arranque=k[2], desde=maximos[k] + 1, hasta=rango["seq_hasta"]))
    return filas


# ------------------------------------------------------------------ filas de cada tabla

def _en(d, *camino, defecto=None):
    for clave in camino:
        if not isinstance(d, dict):
            return defecto
        d = d.get(clave)
    return defecto if d is None else d


def _sobre_fila(e: dict) -> dict:
    return {"codigo": e["codigo"], "instalacion": e["instalacion"], "arranque": e["arranque"], "seq": e["seq"],
            "t": e["t"], "ms": e["ms"], "intento": e.get("intento")}


def _fila_turno(e: dict) -> dict:
    d = e["datos"]
    fila = _sobre_fila(e)
    fila.update({
        "alumno_texto": _en(d, "alumno", "texto"), "alumno_caracteres": _en(d, "alumno", "caracteres"),
        "alumno_boton": _en(d, "alumno", "boton"),
        "ruta": d.get("ruta"),
        "intencion_etiqueta": _en(d, "intencion", "etiqueta"), "intencion_modelo": _en(d, "intencion", "modelo"),
        "intencion_ms": _en(d, "intencion", "ms"),
        "confianza_preguntada": d.get("confianza") is not None,
        "confianza_valor": _en(d, "confianza", "valor"), "confianza_ms": _en(d, "confianza", "ms"),
        "tutor_guardia_ok": _en(d, "tutor", "guardia", "ok"),
        "tutor_guardia_motivo": _en(d, "tutor", "guardia", "motivo"),
        "teoria_fichas": _en(d, "teoria", "fichas"), "teoria_encontro": _en(d, "teoria", "encontro"),
    })
    for campo in ("ms_desde_tutor", "ms_primera_tecla", "ms_escribiendo", "teclas", "borrados", "pegados",
                  "caracteres_pegados", "pausas_2s", "foco_perdido", "teclas_sin_texto"):
        fila[f"comp_{campo}"] = _en(d, "composicion", campo)
    for campo in ("respondio", "paso_id", "paso_tipo", "correcto", "codigo", "nivel_antes", "nivel_despues",
                  "pista", "intentos_paso", "terminado"):
        fila[f"motor_{campo}"] = _en(d, "motor", campo)
    for campo in ("texto_base", "texto_mostrado", "fuente", "reintentos", "intentos", "ms", "tokens_prompt",
                  "tokens_salida", "tokens_cache"):
        fila[f"tutor_{campo}"] = _en(d, "tutor", campo)
    return fila


def _filas_teclas(turnos: list[dict]) -> list[dict]:
    filas = []
    for e in turnos:
        for orden, ev in enumerate(_en(e["datos"], "composicion", "eventos", defecto=[]), 1):
            filas.append({"codigo": e["codigo"], "instalacion": e["instalacion"], "arranque": e["arranque"],
                          "seq_turno": e["seq"], "orden": orden, "dt_ms": ev[0], "tipo": ev[1], "texto": ev[2],
                          "posicion": ev[3]})
    return filas


def _filas_sesiones(eventos: list[dict]) -> list[dict]:
    por_arranque = defaultdict(list)
    for e in eventos:
        por_arranque[(e["instalacion"], e["codigo"], e["arranque"])].append(e)
    filas = []
    for (instalacion, codigo, arranque), evs in por_arranque.items():
        abierta = None
        for e in evs + [None]:
            if abierta and (e is None or e["tipo"] in ("sesion_iniciada", "sesion_terminada")):
                if e is not None and e["tipo"] == "sesion_terminada":
                    abierta.update(fin=e["t"], ms=e["datos"].get("ms"), motivo=e["datos"].get("motivo"))
                filas.append(abierta)
                abierta = None
            if e is not None and e["tipo"] == "sesion_iniciada":
                abierta = {"codigo": codigo, "instalacion": instalacion, "arranque": arranque, "inicio": e["t"],
                           "modo": _en(e["datos"], "equipo", "modo"),
                           "version_consentimiento": e["datos"].get("version_consentimiento"),
                           "intentos_iniciados": 0, "turnos": 0}
            elif abierta and e is not None:
                if e["tipo"] == "intento_iniciado":
                    abierta["intentos_iniciados"] += 1
                elif e["tipo"] == "turno":
                    abierta["turnos"] += 1
    return filas


def _media(valores: list[float], cifras: int = 3) -> float | None:
    return round(sum(valores) / len(valores), cifras) if valores else None


def _fila_intento(instalacion: str, codigo: str, clave: str, evs: list[dict]) -> dict:
    turnos = [e["datos"] for e in evs if e["tipo"] == "turno"]
    terminado = next((e for e in reversed(evs) if e["tipo"] == "intento_terminado"), None)
    descriptivo = next((e["datos"] for e in evs if "problema" in e["datos"]), {})
    segundos = [e["datos"]["segundos_activos"] for e in evs if "segundos_activos" in e["datos"]]

    fuera, desde = 0, None
    for e in evs:
        if e["tipo"] != "ventana":
            continue
        estado = e["datos"].get("estado")
        if estado in ("desenfocada", "oculta") and desde is None:
            desde = e["ms"]
        elif estado in ("enfocada", "visible") and desde is not None:
            fuera += max(0, e["ms"] - desde)
            desde = None

    # la confianza como pronóstico del acierto
    pares = [((t["confianza"]["valor"] - 1) / 4, t["motor"]["correcto"]) for t in turnos
             if _en(t, "confianza", "valor") is not None and _en(t, "motor", "correcto") is not None]
    confianzas = [t["confianza"]["valor"] for t in turnos if _en(t, "confianza", "valor") is not None]
    plantilla = sum(1 for t in turnos if _en(t, "tutor", "fuente") == "plantilla")

    fila = {
        "codigo": codigo, "instalacion": instalacion, "intento": clave,
        "problema": descriptivo.get("problema"), "tema": descriptivo.get("tema"),
        "metodo": descriptivo.get("metodo"), "modo": descriptivo.get("modo"),
        "inicio": evs[0]["t"], "fin": evs[-1]["t"], "terminado": terminado is not None,
        "sesiones": sum(1 for e in evs if e["tipo"] == "intento_iniciado"),
        "segundos_activos": segundos[-1] if segundos else None,
        "turnos": len(turnos),
        "turnos_modelo": sum(1 for t in turnos if _en(t, "tutor", "fuente") == "modelo"),
        "pct_plantilla": round(100 * plantilla / len(turnos), 1) if turnos else None,
        "pistas_boton": sum(1 for t in turnos if _en(t, "alumno", "boton")),
        "ayudas": sum(1 for t in turnos if _en(t, "intencion", "etiqueta") == "ayuda"),
        "preguntas_teoria": sum(1 for t in turnos if _en(t, "intencion", "etiqueta") == "pregunta_teoria"),
        "salidas_ventana": sum(1 for e in evs if e["tipo"] == "ventana" and e["datos"].get("estado") == "desenfocada"),
        "ms_fuera": fuera,
        "pegados": sum(_en(t, "composicion", "pegados", defecto=0) for t in turnos),
        "copias": sum(1 for e in evs if e["tipo"] == "copiar"),
        "confianza_n": len(confianzas), "confianza_media": _media(confianzas, 2),
        "calibracion": round(sum(p - c for p, c in pares) / len(pares), 3) if pares else None,
        "brier": _media([(p - c) ** 2 for p, c in pares]),
    }
    indicadores = _en(terminado["datos"], "indicadores", defecto={}) if terminado else {}
    for columna, (nombre, *_) in zip(COLUMNAS_INDICADORES, INDICADORES):
        fila[columna] = indicadores.get(nombre) if terminado else None
    return fila


def _filas_intentos(eventos: list[dict]) -> list[dict]:
    """Un intento empieza en un intento_iniciado nuevo (no retomado) y termina en su intento_terminado
    o donde empieza el siguiente nuevo. Si faltan los eventos del principio, empieza en el primero que haya."""
    por_clave = defaultdict(list)
    for e in eventos:
        if e.get("intento"):
            por_clave[(e["instalacion"], e["codigo"], e["intento"])].append(e)
    filas = []
    for (instalacion, codigo, clave), evs in por_clave.items():
        actual: list[dict] = []

        def cerrar():
            nonlocal actual
            if actual:
                filas.append(_fila_intento(instalacion, codigo, clave, actual))
            actual = []

        for e in evs:
            nuevo = e["tipo"] == "intento_iniciado" and not e["datos"].get("retomado")
            if nuevo:
                cerrar()
            if not actual and not nuevo and e["tipo"] not in ("intento_iniciado", "turno", "intento_pausado",
                                                               "intento_terminado"):
                continue  # un evento suelto de la interfaz, ya fuera del intento
            actual.append(e)
            if e["tipo"] == "intento_terminado":
                cerrar()
        cerrar()
    return filas


def _filas_anotaciones(eventos: list[dict]) -> list[dict]:
    respuestas = {(e["instalacion"], e["codigo"], e["arranque"], e["seq"]): _en(e["datos"], "alumno", "texto")
                  for e in eventos if e["tipo"] == "turno"}
    filas = []
    for e in eventos:
        if e["tipo"] != "anotacion":
            continue
        d = e["datos"]
        fila = _sobre_fila(e)
        del fila["ms"]
        fila.update({k: d.get(k) for k in ("seq_turno", "arranque_turno", "tipo", "idea_esperada", "ms",
                                           "tokens_prompt", "tokens_salida", "modelo")})
        fila["respuesta"] = respuestas.get((e["instalacion"], e["codigo"], d.get("arranque_turno"),
                                            d.get("seq_turno")))
        for campo in ("expresa_idea", "claridad", "usa_terminos_tecnicos", "idea_erronea", "tipo"):
            fila[f"rubrica_{campo}"] = _en(d, "rubrica", campo)
        filas.append(fila)
    return filas


def _mediana(valores: list[float]) -> float | None:
    return round(float(statistics.median(valores)), 1) if valores else None


def _p90(valores: list[float]) -> float | None:
    if not valores:
        return None
    ordenados = sorted(valores)
    return round(float(ordenados[-(-9 * len(ordenados) // 10) - 1]), 1)


def _filas_equipos(eventos: list[dict]) -> list[dict]:
    por_equipo = defaultdict(list)
    for e in eventos:
        por_equipo[e["instalacion"]].append(e)
    filas = []
    for instalacion, evs in sorted(por_equipo.items()):
        por_tiempo = sorted(evs, key=lambda e: e["ms"])
        sesiones = [e for e in por_tiempo if e["tipo"] == "sesion_iniciada"]
        turnos = [e["datos"] for e in evs if e["tipo"] == "turno"]

        def ultimo(*camino):
            for e in reversed(sesiones):
                v = _en(e["datos"], *camino)
                if v is not None:
                    return v
            return None

        # lo que informó el modelo al cargar, una vez por arranque (sesion_iniciada y modelo_estado repiten)
        por_arranque = defaultdict(dict)
        for e in por_tiempo:
            if e["tipo"] == "modelo_estado":
                modelo = e["datos"]
            elif e["tipo"] == "sesion_iniciada":
                modelo = e["datos"].get("modelo")
            else:
                continue
            for campo, v in (modelo or {}).items():
                # una sesión que empezó con el modelo aún cargando informa 0 ms; no es un tiempo medido
                if v is None or (campo in ("ms_carga", "ms_precalentar") and not v > 0):
                    continue
                por_arranque[e["arranque"]][campo] = v
        del_modelo = list(por_arranque.values())

        def ultimo_modelo(campo):
            return next((m[campo] for m in reversed(del_modelo) if campo in m), None)

        # turnos en los que se llamó al modelo para redactar
        voz = [t["tutor"] for t in turnos if _en(t, "tutor", "intentos") and _en(t, "tutor", "ms") is not None]
        equipo = next((e["datos"].get("equipo") for e in reversed(sesiones) if e["datos"].get("equipo")), None) or {}
        pantalla = equipo.get("pantalla") or {}
        fila = {
            "instalacion": instalacion,
            "codigos": ";".join(sorted({e["codigo"] for e in evs})),
            "sesiones": len(sesiones), "primera": por_tiempo[0]["t"], "ultima": por_tiempo[-1]["t"],
            **{k: equipo.get(k) for k in ("so", "version_so", "build", "plataforma", "cpu", "nucleos_fisicos",
                                          "nucleos_logicos", "ram_total_gb", "ram_disponible_gb",
                                          "disco_libre_gb", "webview2", "modo", "variante_cpu")},
            "pantalla_ancho": pantalla.get("ancho"), "pantalla_alto": pantalla.get("alto"),
            "pantalla_escala": pantalla.get("escala"),
            "app": ultimo("versiones", "app"), "modelo": ultimo("versiones", "modelo"),
            "modelo_sha256": ultimo("versiones", "modelo_sha256"),
            "hilos": ultimo_modelo("hilos"), "ctx": ultimo_modelo("ctx"),
            "ms_carga_mediana": _mediana([m["ms_carga"] for m in del_modelo if "ms_carga" in m]),
            "ms_precalentar_mediana": _mediana([m["ms_precalentar"] for m in del_modelo if "ms_precalentar" in m]),
            "voz_ms_mediana": _mediana([v["ms"] for v in voz]),
            "voz_ms_p90": _p90([v["ms"] for v in voz]),
            "voz_tokens_s_mediana": _mediana([v["tokens_salida"] * 1000 / v["ms"] for v in voz
                                              if v.get("tokens_salida") and v["ms"] > 0]),
            "intencion_ms_mediana": _mediana([t["intencion"]["ms"] for t in turnos
                                              if _en(t, "intencion", "ms") is not None]),
            "anotacion_ms_mediana": _mediana([e["datos"]["ms"] for e in evs
                                              if e["tipo"] == "anotacion" and e["datos"].get("ms") is not None]),
            "pct_plantilla": round(100 * sum(1 for t in turnos if _en(t, "tutor", "fuente") == "plantilla")
                                   / len(turnos), 1) if turnos else None,
        }
        filas.append(fila)
    return filas


def _filas_cuestionarios(eventos: list[dict]) -> list[dict]:
    filas = []
    for e in eventos:
        if e["tipo"] == "cuestionario_respuesta":
            d = e["datos"]
            filas.append({**{k: e[k] for k in ("codigo", "instalacion", "arranque", "seq", "t")},
                          "cuestionario": d.get("cuestionario", d.get("id")), "version": d.get("version"),
                          "item": d.get("item"), "respuesta": d.get("respuesta"), "ms": d.get("ms")})
    return filas


# ------------------------------------------------------------------ escritura

# Excel y LibreOffice ejecutan como fórmula una celda que empieza con estos caracteres.
_INICIO_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _celda(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    texto = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
    # Lo que escribió el alumno no debe abrirse como fórmula: se antepone un apóstrofo.
    # Un número solo ("-2.5") no es peligroso y se deja igual. eventos.jsonl conserva el original.
    if texto.startswith(_INICIO_FORMULA) and not _es_numero(texto):
        return "'" + texto
    return texto


def _es_numero(texto: str) -> bool:
    try:
        float(texto)
    except ValueError:
        return False
    return texto.strip() == texto


def escribir_csv(ruta: Path, columnas: list[str], filas: list[dict]) -> None:
    """UTF-8 con BOM, separado por comas. Una fila con una clave que no es columna es un error de programa."""
    with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columnas, extrasaction="raise")
        w.writeheader()
        for fila in filas:
            w.writerow({k: _celda(v) for k, v in fila.items()})


def _filas_diccionario() -> list[dict]:
    filas = [{"archivo": archivo, "columna": c, "tipo": tipo, "unidad": unidad, "descripcion": desc}
             for archivo, columnas in TABLAS.items() for c, tipo, unidad, desc in columnas]
    # eventos.jsonl lleva los mismos campos, con `datos` como objeto
    filas += [{"archivo": "eventos.jsonl", "columna": c, "tipo": "objeto" if c == "datos" else tipo,
               "unidad": unidad, "descripcion": desc} for c, tipo, unidad, desc in TABLAS["eventos.csv"]]
    return filas


def _leeme(resumen: dict, hoy: str) -> str:
    archivos = "\n".join(f"- `{a}`: {d}" for a, d in DESCRIPCION_ARCHIVOS.items())
    return (f"# Datos del Tutor socrático\n\n"
            f"Generado el {hoy} con `tools.abrir_paquetes`.\n\n"
            f"- Paquetes: {resumen['paquetes']}\n"
            f"- Alumnos: {resumen['alumnos']}\n"
            f"- Eventos (sin duplicados): {resumen['eventos']}\n"
            f"- Problemas de integridad: {resumen['problemas']} (ver `integridad.csv`)\n\n"
            f"## Archivos\n\n{archivos}\n\n"
            f"Las tablas están en UTF-8 con BOM, separadas por comas. `diccionario.csv` describe cada columna.\n"
            f"Un texto que empieza con = + - @ (y no es un número) lleva un apóstrofo al inicio para que "
            f"Excel no lo tome como fórmula; `eventos.jsonl` guarda el texto original.\n")


# ------------------------------------------------------------------ todo junto

def procesar(rutas_zip: list[Path], privada: PrivateKey, salida: Path) -> dict:
    """Abre los paquetes, junta los eventos y escribe todas las tablas en `salida`. Devuelve un resumen."""
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    vistos: dict[tuple, tuple[str, str]] = {}
    eventos: list[dict] = []
    problemas: list[dict] = []
    manifiestos: list[tuple[str, dict]] = []
    paquetes = []

    for ruta in map(Path, rutas_zip):
        del_zip, de_zip, manifiesto = abrir_paquete(ruta, privada)
        problemas += de_zip
        if manifiesto:
            manifiestos.append((ruta.name, manifiesto))
        nuevos = 0
        for e in del_zip:
            clave = (e["instalacion"], e["codigo"], e["arranque"], e["seq"])
            if clave in vistos:
                if _canonico(e) != vistos[clave][1]:
                    problemas.append(_problema(ruta.name, "conflicto",
                                               f"El evento {e['seq']} ya venía en {vistos[clave][0]} con otro contenido.",
                                               codigo=e["codigo"], instalacion=e["instalacion"],
                                               arranque=e["arranque"], desde=e["seq"], hasta=e["seq"]))
                continue
            vistos[clave] = (ruta.name, _canonico(e))
            eventos.append(e)
            nuevos += 1
        paquetes.append(_problema(ruta.name, "paquete", f"{len(del_zip)} eventos ({nuevos} nuevos)",
                                  **({"codigo": manifiesto.get("codigo", ""),
                                      "instalacion": manifiesto.get("instalacion", "")} if manifiesto else {})))

    eventos = _ordenar(eventos)
    problemas += _huecos(eventos) + _faltan_al_final(eventos, manifiestos)
    turnos = [e for e in eventos if e["tipo"] == "turno"]

    tablas = {
        "eventos.csv": [{**{k: e.get(k) for k in ("v", "seq", "t", "ms", "mono", "codigo", "instalacion",
                                                  "arranque", "intento", "origen", "tipo")}, "datos": e["datos"]}
                        for e in eventos],
        "sesiones_uso.csv": _filas_sesiones(eventos),
        "intentos.csv": _filas_intentos(eventos),
        "turnos.csv": [_fila_turno(e) for e in turnos],
        "teclas.csv": _filas_teclas(turnos),
        "anotaciones.csv": _filas_anotaciones(eventos),
        "equipos.csv": _filas_equipos(eventos),
        "cuestionarios.csv": _filas_cuestionarios(eventos),
        "integridad.csv": paquetes + problemas,
        "diccionario.csv": _filas_diccionario(),
    }
    for archivo, filas in tablas.items():
        escribir_csv(salida / archivo, [c for c, *_ in TABLAS[archivo]], filas)
    with open(salida / "eventos.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for e in eventos:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    resumen = {"paquetes": len(paquetes), "alumnos": len({(e["codigo"]) for e in eventos}),
               "eventos": len(eventos), "problemas": len(problemas),
               "tablas": {a: len(f) for a, f in tablas.items()}}
    (salida / "LEEME.md").write_text(_leeme(resumen, date.today().isoformat()), encoding="utf-8")
    return resumen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.abrir_paquetes", description=__doc__.splitlines()[0])
    parser.add_argument("--llave", type=Path, required=True, help="llave_privada.tsk")
    parser.add_argument("--entrada", type=Path, required=True, help="carpeta con los .zip exportados")
    parser.add_argument("--salida", type=Path, required=True, help="carpeta donde se escriben las tablas")
    args = parser.parse_args(argv)

    rutas = sorted(args.entrada.rglob("*.zip"), key=lambda r: (r.stat().st_mtime, r.name)) \
        if args.entrada.is_dir() else []
    if not rutas:
        print(f"Error: no hay archivos .zip en {args.entrada}.", file=sys.stderr)
        return 1
    try:
        privada = cargar_llave_privada(args.llave, getpass.getpass("Contraseña de la llave privada: "))
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    resumen = procesar(rutas, privada, args.salida)
    print(f"{resumen['paquetes']} paquetes, {resumen['alumnos']} alumnos, {resumen['eventos']} eventos.")
    if resumen["problemas"]:
        print(f"Hay {resumen['problemas']} problemas de integridad: revisa integridad.csv.")
    print(f"Tablas en {args.salida}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
