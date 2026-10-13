"""
Formato de texto compartido por las cartas (citación, sentencia, o
ambas): fechas, montos en pesos/UTM, el folio «Rol», etc.

Hasta la Fase 3 este archivo también armaba el .docx entero a mano con
python-docx (logo, tabla de causas, firma...) — una copia de lo que
hacían generar_notificaciones_JPL1.py y sus hermanos. Eso ahora vive en
una plantilla Word por Juzgado y por tipo de carta (Módulo 1, ver
motor/plantilla.py y motor/plantillas_defecto/): este archivo solo le
arma a esa plantilla los textos y montos que dependen de los datos de
cada carta.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

# Las cartas llevan la fecha de Chile aunque el servidor ande en UTC.
ZONA_CHILE = ZoneInfo("America/Santiago")


def ahora_chile():
    return datetime.now(ZONA_CHILE)

ENTIDAD_DENUNCIANTE_DEFECTO = (
    "DIRECCIÓN DE ADMINISTRACIÓN Y FINANZAS DE LA ILUSTRE MUNICIPALIDAD DE MAIPÚ"
)
MOTIVO_INFRACCION_DEFECTO = "TRANSITAR SIN DISPOSITIVO ELECTRÓNICO POR AUTOPISTA CONCESIONADA"
BLOQUE_POSTAL_DEFECTO = "SANTIAGO 16"


def fecha_hoy():
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    h = ahora_chile()
    return f"{h.day} de {meses[h.month - 1]} de {h.year}"


def fmt_pesos(n):
    return "$" + f"{int(round(n)):,}".replace(",", ".")


def fmt_utm(utm, utm_valor):
    return f"{utm} UTM ({fmt_pesos(utm * utm_valor)})"


def fmt_utm_simple(utm):
    """'1 UTM', '2 UTM', '1,5 UTM' — sin conversión a pesos (así lo
    muestran las cartas oficiales de los Juzgados en la columna Monto)."""
    numero = f"{utm:g}" if isinstance(utm, float) else str(utm)
    return f"{numero.replace('.', ',')} UTM"


def condena_monto_txt(causas):
    """Texto del monto de la condena para las cartas de sentencia: si TODAS
    las infracciones de la patente tienen la misma multa, '1 UTM' (el
    texto oficial dice 'al pago de 1 UTM por cada infracción'); si no, se
    remite a la columna Monto en vez de afirmar un monto único falso."""
    montos = {c["monto"] for c in causas}
    if len(montos) == 1:
        return fmt_utm_simple(next(iter(montos)))
    return "la multa indicada"


def patente_completa(pat, dig_pat):
    return f"{pat}-{dig_pat}" if dig_pat else pat


def clausula_audiencia(fecha_aud, hora_txt):
    """Evita dejar '...a las , bajo apercibimiento...' cuando no hay hora
    asignada (caso SLOT_SIN_AGENDA)."""
    if hora_txt:
        return f"el día {fecha_aud}, a las {hora_txt}"
    return "en fecha y hora a coordinar con el Juzgado"


def formatear_rol(rol_raw, anio):
    """Formato oficial: «Folio_Proceso»-T-«Año_Proceso». Si no hay folio
    real, muestra "PENDIENTE" para que quede clarísimo en la carta que
    ese dato falta completar."""
    if rol_raw:
        return f"{rol_raw}-T-{anio}"
    return "PENDIENTE"
