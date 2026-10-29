"""
Asignación de audiencias a cada patente. Porteado de
generar_notificaciones_JPL1.py.

El orden intenta ser el de llegada (N° de parte mínimo primero), pero con
los N° de parte reales de la nómina (`1255846-M78`) no se pueden comparar
como número: todas las patentes empatan y queda el orden del Excel. Es el
mismo orden efectivo de los scripts originales.

Los horarios disponibles vienen de los BloqueHorario de cada Juzgado
(procesos.generacion.pool_de_agenda): una lista de slots
("dd/mm/aaaa", "HH:MM"), repetidos tantas veces como cupos tenga el
bloque. El fallback por .ics de los scripts no se portó.
"""

SLOT_SIN_AGENDA = ("A COORDINAR CON EL JUZGADO", "")


def asignar_agenda(grupos, agenda):
    """Asigna un slot de `agenda` (lista de ("dd/mm/aaaa", "HH:MM")) a cada
    patente de `grupos`, en orden de N° de parte mínimo (ver el docstring
    del módulo). Devuelve (slots, patentes_sin_cupo):
    `slots` es un dict patente -> ("dd/mm/aaaa", "HH:MM"), y
    `patentes_sin_cupo` la lista de patentes que no alcanzaron un slot
    (quedan con SLOT_SIN_AGENDA, "A COORDINAR CON EL JUZGADO")."""
    def nro_parte_min(g):
        try:
            return min(int(c["nroParte"].replace(".", "")) for c in g["causas"])
        except Exception:
            return float("inf")

    orden = sorted(grupos.items(), key=lambda kv: nro_parte_min(kv[1]))
    slots = {}
    patentes_sin_cupo = []
    for i, (pat, _) in enumerate(orden):
        if i < len(agenda):
            slots[pat] = agenda[i]
        else:
            slots[pat] = SLOT_SIN_AGENDA
            patentes_sin_cupo.append(pat)
    return slots, patentes_sin_cupo
