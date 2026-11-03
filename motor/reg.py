"""
Reg N° del sobre: lo escribe la persona para la primera carta y aumenta de
1 en 1 en cada carta (un número por carta física; las páginas «parte X de N»
de una misma carta comparten el número).
"""

import re

_REG = re.compile(r"(.*?)(\d+)")


def validar_reg(texto):
    """Devuelve el Reg N° inicial limpio o levanta ValueError."""
    reg = str(texto or "").strip()
    if not _REG.fullmatch(reg):
        raise ValueError(
            "El Reg N° debe terminar en un número (por ejemplo 15230 o R-0450): "
            "aumenta de 1 en 1 en cada carta."
        )
    return reg


def reg_para_carta(inicial, indice):
    """El Reg N° de la carta número `indice` (0 = la primera): el número final
    de `inicial` más `indice`, conservando el prefijo y los ceros a la izquierda."""
    prefijo, numero = _REG.fullmatch(validar_reg(inicial)).groups()
    return f"{prefijo}{int(numero) + indice:0{len(numero)}d}"
