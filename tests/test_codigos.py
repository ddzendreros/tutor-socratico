"""Pruebas de validación de códigos de acceso de alumnos."""
from __future__ import annotations

from escritorio.alumnos import ALFABETO, es_valido, formatear, generar, normalizar


def test_normalizacion():
    assert normalizar("ts-7k4p-2") == "TS7K4P2"
    assert normalizar(" TS - 7O4P - 2 ") == "TS704P2"
    assert normalizar("ts-1l4p-2") == "TS114P2"


def test_formatear():
    assert formatear("TS7K4P2") == "TS-7K4P-2"
    assert formatear("ts-7k4p-2") == "TS-7K4P-2"


def test_rechazo_cambio_caracter_y_transposicion_en_10000_codigos():
    codigos = generar(10000)
    assert len(codigos) == 10000

    for codigo in codigos:
        assert es_valido(codigo)
        raw = normalizar(codigo)

        # Cualquier cambio de un carácter debe ser rechazado
        for i, char_actual in enumerate(raw):
            for alt in ALFABETO:
                if alt != char_actual:
                    mutado = raw[:i] + alt + raw[i + 1:]
                    assert not es_valido(mutado), f"Mutación no rechazada: {mutado} de {raw}"

        # Cualquier transposición adyacente debe ser rechazada
        for i in range(len(raw) - 1):
            if raw[i] != raw[i + 1]:
                mutado = raw[:i] + raw[i + 1] + raw[i] + raw[i + 2:]
                assert not es_valido(mutado), f"Transposición no rechazada: {mutado} de {raw}"


def test_solo_el_formato_completo():
    codigo = normalizar(generar(1)[0])
    assert es_valido(codigo) and es_valido(formatear(codigo).lower())
    # códigos cortos o largos que cumplen Luhn no entran
    assert not es_valido("000") and not es_valido("0" * 6) and not es_valido("0" * 8)
    assert not es_valido(codigo + "0")
    assert not es_valido("")
