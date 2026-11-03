"""
Fechas de audiencia que entrega el Juzgado.

El Juzgado fija la fecha de citación por bloque de folios (la hora no
importa). La persona las escribe al generar las cartas, una por línea:

    127795-129000  14/12/2026
    129001-131500  15/12/2026

o una sola fecha para toda la nómina:

    14/12/2026

El «folio» de una causa es el número de su ROL («2026-Y-127795» → 127795;
«13Y95046/26» → 95046). Ambos extremos del bloque se incluyen.
"""

import re
from datetime import date, datetime

_LINEA = re.compile(
    r"^\s*(?:(\d+)\s*(?:-|–|—|a|hasta)\s*(\d+)\s*[:=]?\s*)?(\d{1,2}/\d{1,2}/\d{4})\s*$",
    re.IGNORECASE,
)


def correlativo_rol(rol):
    """El folio de un ROL: «2026-Y-127795» → 127795, «13Y95046/26» → 95046.
    None si el ROL no tiene ninguna de esas formas."""
    texto = str(rol or "").strip()
    m = re.fullmatch(r"\d+\s*Y\s*(\d+)\s*/\s*\d+", texto, re.IGNORECASE)
    if m:
        return int(m.group(1))
    m = re.search(r"-(\d+)$", texto)
    return int(m.group(1)) if m else None


def parsear_fechas_audiencia(texto, hoy=None):
    """Devuelve [(desde|None, hasta|None, date)]. Levanta ValueError con un
    mensaje para la persona si el texto no se entiende o no es coherente."""
    hoy = hoy or date.today()
    bloques = []
    for n, linea in enumerate(str(texto or "").splitlines(), 1):
        if not linea.strip():
            continue
        m = _LINEA.match(linea)
        if not m:
            raise ValueError(
                f"Línea {n}: «{linea.strip()}» no se entiende. Escribe «desde-hasta dd/mm/aaaa» "
                f"(por ejemplo 127795-129000 14/12/2026) o solo la fecha."
            )
        desde, hasta, f = m.groups()
        try:
            fecha = datetime.strptime(f, "%d/%m/%Y").date()
        except ValueError:
            raise ValueError(f"Línea {n}: «{f}» no es una fecha válida (dd/mm/aaaa).") from None
        if fecha < hoy:
            raise ValueError(f"Línea {n}: la fecha {f} ya pasó; la audiencia debe ser desde hoy en adelante.")
        if desde is not None:
            desde, hasta = int(desde), int(hasta)
            if desde > hasta:
                raise ValueError(f"Línea {n}: el folio inicial ({desde}) es mayor que el final ({hasta}).")
        bloques.append((desde, hasta, fecha))
    if not bloques:
        raise ValueError("Escribe al menos una fecha de audiencia.")
    globales = [b for b in bloques if b[0] is None]
    if globales and len(bloques) > 1:
        raise ValueError("Escribe una sola fecha para toda la nómina, o bloques de folios «desde-hasta fecha»; no ambos ni varias fechas sueltas.")
    con_rango = sorted(b for b in bloques if b[0] is not None)
    for (d1, h1, _), (d2, h2, _) in zip(con_rango, con_rango[1:]):
        if d2 <= h1:
            raise ValueError(f"Los bloques {d1}-{h1} y {d2}-{h2} se solapan.")
    return bloques


def fecha_de_rol(bloques, rol):
    """La fecha del bloque que contiene el folio del ROL (None si ninguno)."""
    n = correlativo_rol(rol)
    for desde, hasta, fecha in bloques:
        if desde is None:
            return fecha
        if n is not None and desde <= n <= hasta:
            return fecha
    return None


def bloques_a_json(bloques):
    return [[d, h, f.isoformat()] for d, h, f in bloques]


def bloques_de_json(datos):
    return [(d, h, date.fromisoformat(f)) for d, h, f in datos]
