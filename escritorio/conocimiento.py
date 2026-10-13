"""Módulo de base de conocimiento y recuperación con BM25 propio.

Carga las fichas pedagógicas desde conocimiento/*.md y realiza búsqueda
léxica normalizada sin dependencias externas.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from escritorio.config import UMBRAL_TEORIA  # noqa: F401  (se importa desde este módulo)


_PALABRAS_VACIAS = {
    "a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos", "ante",
    "antes", "aquel", "aquella", "aquellas", "aquello", "aquellos", "aqui", "arriba",
    "asi", "cada", "como", "con", "contra", "cual", "cuales", "cualquier", "cuando",
    "cuanta", "cuantas", "cuanto", "cuantos", "de", "del", "desde", "donde", "dos",
    "el", "ella", "ellas", "ello", "ellos", "en", "entre", "era", "erais", "eran",
    "eras", "eres", "es", "esa", "esas", "ese", "eso", "esos", "esta", "estaba",
    "estabais", "estaban", "estabas", "estad", "estada", "estadas", "estado", "estados",
    "estais", "estamos", "estan", "estando", "estar", "estara", "estaran", "estaras",
    "estare", "estareis", "estaremos", "estaria", "estariais", "estariamos", "estarian",
    "estarias", "estas", "este", "esteis", "estemos", "esten", "estes", "esto", "estos",
    "estoy", "estuve", "estuviera", "estuvierais", "estuvieran", "estuvieras",
    "estuvieron", "estuviese", "estuvieseis", "estuviesen", "estuvieses", "estuvimos",
    "estuviste", "estuvisteis", "estuvo", "fue", "fuera", "fuerais", "fueran", "fueras",
    "fueron", "fuese", "fueseis", "fuesen", "fueses", "fui", "fuimos", "fuiste",
    "fuisteis", "ha", "habeis", "haber", "habia", "habiais", "habiamas", "habian",
    "habias", "habida", "habidas", "habido", "habidos", "habiendo", "habra", "habran",
    "habras", "habre", "habreis", "habremos", "habria", "habriais", "habriamos",
    "habrian", "habrias", "han", "has", "hasta", "hay", "haya", "hayais", "hayamos",
    "hayan", "hayas", "he", "hemos", "hube", "hubiera", "hubierais", "hubieran",
    "hubieras", "hubieron", "hubiese", "hubieseis", "hubiesen", "hubieses", "hubimos",
    "hubiste", "hubisteis", "hubo", "la", "las", "le", "les", "lo", "los", "mas",
    "me", "mi", "mia", "mias", "mio", "mios", "mis", "mucho", "muchos", "muy",
    "nada", "ni", "no", "nos", "nosotras", "nosotros", "nuestra", "nuestras", "nuestro",
    "nuestros", "o", "os", "otra", "otras", "otro", "otros", "para", "pero", "poco",
    "por", "porque", "que", "quien", "quienes", "se", "sea", "seais", "seamos",
    "sean", "seas", "ser", "sera", "seran", "seras", "sere", "sereis", "seremos",
    "seria", "seriais", "seriamos", "serian", "serias", "si", "sido", "siendo",
    "sin", "sobre", "sois", "somos", "son", "soy", "su", "sus", "suya", "suyas",
    "suyo", "suyos", "tambien", "tanto", "te", "tendra", "tendran", "tendras",
    "tendre", "tendreis", "tendremos", "tendria", "tendriais", "tendriamos",
    "tendrian", "tendrias", "tened", "teneis", "tenemos", "tenga", "tengais",
    "tengamos", "tengan", "tengas", "tengo", "tenia", "teniais", "teniamos",
    "tenian", "tenias", "tenida", "tenidas", "tenido", "tenidos", "teniendo",
    "ti", "tiene", "tienen", "tienes", "toda", "todas", "todo", "todos", "tu",
    "tus", "tuve", "tuviera", "tuvierais", "tuvieran", "tuvieras", "tuvieron",
    "tuviese", "tuvieseis", "tuviesen", "tuvieses", "tuvimos", "tuviste",
    "tuvisteis", "tuvo", "un", "una", "unas", "uno", "unos", "usa", "usar",
    "va", "vais", "vamos", "van", "varias", "varios", "vaya", "vayais", "vayan",
    "vayas", "veces", "ver", "vez", "vosotras", "vosotros", "vuestra", "vuestras",
    "vuestro", "vuestros", "y", "ya", "bien", "mal",
    # verbos y muletillas de las preguntas: no dicen de qué concepto se trata
    "pasa", "funciona", "sirve", "hago", "hace", "saco", "significa", "quiere", "decir", "oye", "dice",
    "explica", "explicas", "puedo", "debo",
}


def normalizar(texto: str) -> list[str]:
    """Normaliza texto: minúsculas, sin acentos, sin plurales simples ni palabras vacías."""
    plano = texto.lower().translate(str.maketrans("áéíóúüÁÉÍÓÚÜ", "aeiouuAEIOUU"))
    tokens = re.findall(r"\b[a-z0-9_]+\b", plano)
    resultado = []
    for t in tokens:
        if t in _PALABRAS_VACIAS:
            continue
        # reducción de plurales simples
        if len(t) > 3 and t.endswith("es") and t[-3] in "dlrnsy":
            t = t[:-2]
        elif len(t) > 3 and t.endswith("s") and not t.endswith("is"):
            t = t[:-1]
        if t and t not in _PALABRAS_VACIAS:
            resultado.append(t)
    return resultado


@dataclass
class Ficha:
    id: str
    titulo: str
    claves: list[str]
    fuente: str
    contenido: str
    tema: str


def cargar_fichas(carpeta: str | Path | None = None) -> list[Ficha]:
    """Carga todas las fichas desde los archivos Markdown en la carpeta indicada."""
    if carpeta is None:
        carpeta = Path(__file__).resolve().parent.parent / "conocimiento"
    else:
        carpeta = Path(carpeta)

    fichas: list[Ficha] = []
    if not carpeta.exists():
        return fichas

    for archivo in sorted(carpeta.glob("*.md")):
        tema = archivo.stem
        texto = archivo.read_text(encoding="utf-8")
        bloques = re.split(r"(?m)^##\s+", texto)
        for bloque in bloques[1:]:
            lineas = [l.strip() for l in bloque.splitlines() if l.strip()]
            if not lineas:
                continue
            titulo = lineas[0]
            ficha_id = ""
            claves: list[str] = []
            fuente = ""
            cuerpo: list[str] = []
            for linea in lineas[1:]:
                if linea.startswith("id:"):
                    ficha_id = linea[3:].strip()
                elif linea.startswith("claves:"):
                    claves = [c.strip() for c in linea[7:].split(",") if c.strip()]
                elif linea.startswith("fuente:"):
                    fuente = linea[7:].strip()
                else:
                    cuerpo.append(linea)

            if not ficha_id:
                slug = titulo.lower().translate(str.maketrans("áéíóúü", "aeiouu"))
                ficha_id = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")

            fichas.append(
                Ficha(
                    id=ficha_id,
                    titulo=titulo,
                    claves=claves,
                    fuente=fuente,
                    contenido=" ".join(cuerpo),
                    tema=tema,
                )
            )

    return fichas


class BuscadorBM25:
    """Implementación de BM25 para recuperación de fichas pedagógicas."""

    def __init__(self, fichas: list[Ficha], k1: float = 1.5, b: float = 0.75):
        self.fichas = fichas
        self.k1 = k1
        self.b = b
        self.N = len(fichas)
        self.doc_lengths: list[int] = []
        self.doc_term_freqs: list[dict[str, int]] = []
        self.doc_freqs: dict[str, int] = defaultdict(int)
        # términos del título y las claves: dicen de qué trata la ficha
        self.terminos_clave: list[set[str]] = []
        self.terminos_titulo: list[set[str]] = []

        for f in fichas:
            # Ponderación de campos: título (x3), palabras clave (x2) y contenido (x1)
            toks_titulo = normalizar(f.titulo) * 3
            toks_claves = normalizar(" ".join(f.claves)) * 2
            toks_contenido = normalizar(f.contenido)
            doc_toks = toks_titulo + toks_claves + toks_contenido
            self.terminos_clave.append(set(toks_titulo) | set(toks_claves))
            self.terminos_titulo.append(set(toks_titulo))
            self.doc_lengths.append(len(doc_toks))
            tf = Counter(doc_toks)
            self.doc_term_freqs.append(tf)
            for t in tf:
                self.doc_freqs[t] += 1

        self.avgdl = (sum(self.doc_lengths) / self.N) if self.N > 0 else 1.0

        # IDF de Robertson-Spärck Jones (estrictamente no negativo)
        self.idf: dict[str, float] = {}
        for t, df in self.doc_freqs.items():
            self.idf[t] = math.log(1.0 + (self.N - df + 0.5) / (df + 0.5))

    def buscar(self, pregunta: str, k: int = 2, umbral: float | None = None) -> list[tuple[Ficha, float]]:
        """Devuelve las k mejores fichas ordenadas por relevancia BM25.

        Con `umbral`, sólo cuentan las fichas que lo alcanzan y que comparten con la pregunta
        algún término de su título o sus claves: coincidir sólo en el texto ("¿cómo funciona un
        transistor?" con una ficha que dice "funciona") no basta para responder con ella.
        La ficha cuyo título es justo lo que se pregunta ("¿qué es una malla?" y "Malla") va primero
        y no necesita el umbral: un término que aparece en muchas fichas puntúa poco.
        """
        q_tokens = normalizar(pregunta)
        if not q_tokens or self.N == 0:
            return []

        scores: list[tuple[Ficha, float, bool]] = []
        for i, ficha in enumerate(self.fichas):
            exacta = set(q_tokens) == self.terminos_titulo[i]
            if umbral is not None and not set(q_tokens) & self.terminos_clave[i]:
                continue
            tf = self.doc_term_freqs[i]
            dl = self.doc_lengths[i]
            score = 0.0
            for qt in q_tokens:
                if qt in tf:
                    f_val = tf[qt]
                    idf_val = self.idf.get(qt, 0.0)
                    denom = f_val + self.k1 * (1.0 - self.b + self.b * (dl / self.avgdl))
                    score += idf_val * (f_val * (self.k1 + 1.0)) / denom
            if score > 0.0 and (umbral is None or score >= umbral or exacta):
                scores.append((ficha, round(score, 3), exacta))

        scores.sort(key=lambda x: (x[2], x[1]), reverse=True)
        return [(ficha, score) for ficha, score, _ in scores[:k]]


_BUSCADOR_GLOBAL: BuscadorBM25 | None = None


def obtener_buscador() -> BuscadorBM25:
    """Instancia global del buscador con las fichas de conocimiento/."""
    global _BUSCADOR_GLOBAL
    if _BUSCADOR_GLOBAL is None:
        fichas = cargar_fichas()
        _BUSCADOR_GLOBAL = BuscadorBM25(fichas)
    return _BUSCADOR_GLOBAL


def reiniciar_buscador() -> None:
    """Fuerza la recarga de fichas en memoria."""
    global _BUSCADOR_GLOBAL
    _BUSCADOR_GLOBAL = None


def buscar(pregunta: str, k: int = 2, umbral: float | None = None) -> list[tuple[Ficha, float]]:
    """Busca las fichas más relevantes para una pregunta, filtrando opcionalmente por umbral."""
    return obtener_buscador().buscar(pregunta, k=k, umbral=umbral)
