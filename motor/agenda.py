"""
Asignación de audiencias a cada patente, en orden de llegada (N° de parte
mínimo). Porteado de generar_notificaciones_JPL1.py.

En los scripts originales la disponibilidad de horarios venía de una
lista fija en el código (AGENDA_AUDIENCIAS) o, si existía, de un archivo
.ics exportado manualmente desde un calendario (obtener_disponibilidad_
calendario). En la aplicación web ese mecanismo queda reemplazado por los
registros de BloqueHorario de cada Juzgado (Fase 4 — Agenda con cupos),
así que ese fallback por .ics no se portó: quien llame a asignar_agenda()
aquí pasa la lista de slots disponibles ya leída desde la base de datos,
en el mismo formato (fecha_en_letras, hora) que usaba el script.
"""

SLOT_SIN_AGENDA = ("A COORDINAR CON EL JUZGADO", "")


def asignar_agenda(grupos, agenda):
    """Asigna un slot de `agenda` (lista de (fecha_en_letras, hora)) a cada
    patente de `grupos`, en orden de N° de parte mínimo (quien llegó
    primero, se atiende primero). Devuelve (slots, patentes_sin_cupo):
    `slots` es un dict patente -> (fecha_en_letras, hora), y
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
