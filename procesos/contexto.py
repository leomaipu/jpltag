"""Datos de configuración que varias pantallas muestran."""

from django.conf import settings


def ajustes(request):
    # Con la agenda apagada (settings.JPLTAG_AGENDA_ACTIVA) las pantallas lo
    # dicen: la Agenda sigue editable, pero las cartas no la usan.
    return {"agenda_activa": settings.JPLTAG_AGENDA_ACTIVA}
