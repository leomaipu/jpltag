"""
Conversión de una fecha a su forma en letras ("siete de septiembre de dos
mil veintiséis"), tal como la necesita el texto legal de la carta de
citación (motor.cartas.clausula_audiencia). En los scripts originales esa
redacción salía de diccionarios fijos (_DIAS_EN_LETRAS, _MESES,
_ANIOS_EN_LETRAS, con solo 4 años cargados a mano) porque la agenda
también era una lista fija escrita a mano. Acá la agenda sale de
BloqueHorario (fecha real en la base de datos), así que esta conversión
se generaliza a cualquier año de este siglo en vez de depender de una
lista cerrada."""

_UNIDADES = ["", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve"]
_DIEZ_A_DIECINUEVE = [
    "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis",
    "diecisiete", "dieciocho", "diecinueve",
]
_VEINTIUNO_A_VEINTINUEVE = {
    21: "veintiuno", 22: "veintidós", 23: "veintitrés", 24: "veinticuatro",
    25: "veinticinco", 26: "veintiséis", 27: "veintisiete", 28: "veintiocho",
    29: "veintinueve",
}
_DECENAS = {
    2: "veinte", 3: "treinta", 4: "cuarenta", 5: "cincuenta",
    6: "sesenta", 7: "setenta", 8: "ochenta", 9: "noventa",
}

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
          "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def numero_en_letras(n):
    """Cardinal en letras para 0-99 (suficiente para días y para los dos
    últimos dígitos de un año "dos mil ...")."""
    if n == 0:
        return "cero"
    if n < 10:
        return _UNIDADES[n]
    if n < 20:
        return _DIEZ_A_DIECINUEVE[n - 10]
    if n == 20:
        return "veinte"
    if 21 <= n <= 29:
        return _VEINTIUNO_A_VEINTINUEVE[n]
    decena, unidad = divmod(n, 10)
    if unidad == 0:
        return _DECENAS[decena]
    return f"{_DECENAS[decena]} y {_UNIDADES[unidad]}"


def dia_en_letras(dia):
    """Día del mes (1-31) en letras. 31 es un caso aparte porque no sigue
    el patrón "decena y unidad" de los demás (no existe "treinteno")."""
    if dia == 31:
        return "treinta y uno"
    return numero_en_letras(dia)


def anio_en_letras(anio):
    """Año en letras, forma "dos mil X" — válida para 2000-2099 (el rango
    realista de uso de este sistema). Fuera de ese rango, se deja el
    número tal cual en vez de arriesgar una conversión incorrecta."""
    if not (2000 <= anio <= 2099):
        return str(anio)
    resto = anio - 2000
    if resto == 0:
        return "dos mil"
    return f"dos mil {numero_en_letras(resto)}"


def fecha_en_letras(fecha):
    """`fecha`: un date/datetime. Devuelve "siete de septiembre de dos mil
    veintiséis", el mismo formato que usaban los AGENDA_AUDIENCIAS
    escritos a mano en los scripts originales."""
    return f"{dia_en_letras(fecha.day)} de {MESES[fecha.month - 1]} de {anio_en_letras(fecha.year)}"
