"""Eliminación de información personal identificable en texto libre."""
from __future__ import annotations

import re

# los números pegados a punto, coma u otro dígito, o seguidos de una unidad, son valores del circuito
_ANTES = r"(?<![\d.,])"
_DESPUES = r"(?![\d.,])(?!\s?(?:[kKmMuµ]?(?:Ω|ohms?|V|A|W)\b|Ω))"

PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[correo]"),
    # teléfono: con +52, o con separadores en formato 2-4-4 o 3-3-4
    (re.compile(_ANTES + r"(?:\+52[\s-]?(?:\d{10}|\d{2}[\s-]\d{4}[\s-]\d{4}|\d{3}[\s-]\d{3}[\s-]\d{4})"
                r"|\d{2}[\s-]\d{4}[\s-]\d{4}|\d{3}[\s-]\d{3}[\s-]\d{4})" + _DESPUES), "[teléfono]"),
    # boletas IPN y teléfonos sin separadores (10 dígitos)
    (re.compile(_ANTES + r"\d{10}" + _DESPUES), "[número]"),
]


def scrub(text: str) -> str:
    """Quita correos, teléfonos y boletas antes de guardar o enviar texto."""
    for pat, rep in PII_PATTERNS:
        text = pat.sub(rep, text)
    return text
