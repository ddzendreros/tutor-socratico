"""Constantes ajustables de la aplicación de escritorio."""
from __future__ import annotations

# el modelo que se busca junto al ejecutable o en vendor/modelos
MODELO_ARCHIVO = "gemma-4-E2B_q4_0-it.gguf"

INACTIVIDAD_MIN = 30
# tipos de paso en los que se pregunta la confianza; deben estar en CONFIANZA_KINDS del motor
PASOS_CON_CONFIANZA = ("equations", "values", "targets", "table", "reduce", "concept", "submit",
                       "sup_apagar", "sup_parcial", "sup_suma")

# ranuras de llama-server; cada una conserva en caché el prefijo de su prompt
RANURA_VOZ = 0
RANURA_INTENCION = 1
RANURA_ANOTACION = 2

TEMPERATURA_VOZ = 0.4
TEMPERATURA_VOZ_REINTENTO = 0.2
TEMPERATURA_INTENCION = 0.0
MAX_TOKENS_VOZ = 300
MAX_TOKENS_INTENCION = 100
MAX_TOKENS_ANOTACION = 150
# puntaje BM25 mínimo de una ficha para contestar con ella una duda de teoría
UMBRAL_TEORIA = 3.0

# segundos
TIMEOUT_VOZ = 60.0
TIMEOUT_INTENCION = 20.0
TIMEOUT_ANOTACION = 60.0
# la anotación de respuestas abiertas sólo corre si el modelo lleva este tiempo sin atender al alumno
ANOTACION_REPOSO = 20.0
INTERVALO_LATIDO = 15.0
TIMEOUT_LATIDO_SERVIDOR = 300.0

VELOCIDAD_ESCRITURA_CPS = 60
