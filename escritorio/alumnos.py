"""Identificadores de alumno y validación de códigos de acceso."""
from __future__ import annotations

import secrets

ALFABETO = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
VALORES = {c: i for i, c in enumerate(ALFABETO)}
N = len(ALFABETO)
# prefijo de 2, cuerpo de 4 y verificador: "TS-7K4P-2"
LONGITUD = 7


def normalizar(codigo: str) -> str:
    """Normaliza el código: mayúsculas, sin espacios ni guiones; O->0, I y L->1."""
    s = codigo.upper().replace(" ", "").replace("-", "")
    s = s.replace("O", "0").replace("I", "1").replace("L", "1")
    return s


def calcular_digito_verificador(texto: str) -> str:
    """Calcula el dígito verificador Luhn mod 32 para una cadena base."""
    suma = 0
    factor = 2
    for c in reversed(texto):
        v = VALORES[c]
        p = factor * v
        suma += (p // N) + (p % N)
        factor = 1 if factor == 2 else 2
    return ALFABETO[(N - (suma % N)) % N]


def es_valido(codigo: str) -> bool:
    """Verifica si el código es válido según Luhn mod 32 sobre el alfabeto Crockford."""
    c = normalizar(codigo)
    if len(c) != LONGITUD:
        return False
    for ch in c:
        if ch not in VALORES:
            return False

    suma = 0
    factor = 1
    for ch in reversed(c):
        v = VALORES[ch]
        p = factor * v
        suma += (p // N) + (p % N)
        factor = 2 if factor == 1 else 1

    return (suma % N) == 0


def formatear(codigo: str) -> str:
    """Formatea un código al estilo TS-XXXX-C."""
    c = normalizar(codigo)
    if len(c) == LONGITUD:
        return f"{c[:2]}-{c[2:6]}-{c[6:]}"
    return c


def generar(n: int, prefijo: str = "TS") -> list[str]:
    """Genera n códigos únicos válidos con dígito verificador."""
    prefijo_norm = normalizar(prefijo)
    if len(prefijo_norm) != 2 or any(ch not in VALORES for ch in prefijo_norm):
        raise ValueError("El prefijo debe tener dos caracteres del alfabeto.")
    codigos: set[str] = set()
    while len(codigos) < n:
        cuerpo = "".join(secrets.choice(ALFABETO) for _ in range(4))
        texto = prefijo_norm + cuerpo
        if "0Z" in texto or "Z0" in texto:
            continue
        chk = calcular_digito_verificador(texto)
        completo = texto + chk
        if "0Z" in completo or "Z0" in completo:
            continue
        codigos.add(formatear(completo))
    return sorted(codigos)
