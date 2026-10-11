"""Eliminación de información personal identificable en texto libre."""
from __future__ import annotations

import re

PII_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[correo]"),
    (re.compile(r"\b\d{10}\b"), "[número]"),          # boletas IPN y teléfonos (10 dígitos)
    (re.compile(r"\b(?:\+?52\s?)?\d{2,3}[\s-]?\d{3,4}[\s-]?\d{4}\b"), "[teléfono]"),
]


def scrub(text: str) -> str:
    """Quita correos, teléfonos y boletas antes de guardar o enviar texto."""
    for pat, rep in PII_PATTERNS:
        text = pat.sub(rep, text)
    return text
