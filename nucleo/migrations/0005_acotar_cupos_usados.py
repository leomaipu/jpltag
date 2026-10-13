from django.db import migrations
from django.db.models import F


def acotar_cupos_usados(apps, schema_editor):
    """Antes de exigir usados <= totales, deja al día lo que ya rompa la regla
    (p. ej. un bloque editado a mano en /admin/): si no, la migración que
    agrega la restricción fallaría."""
    import logging

    Bloque = apps.get_model("nucleo", "BloqueHorario")
    ids = list(Bloque.objects.filter(cupos_usados__gt=F("cupos_totales")).values_list("pk", flat=True))
    if ids:
        logging.getLogger("nucleo").warning(
            "Bloques con más cupos ocupados que totales, acotados al total: %s", ids,
        )
    Bloque.objects.filter(cupos_usados__gt=F("cupos_totales")).update(cupos_usados=F("cupos_totales"))


class Migration(migrations.Migration):

    dependencies = [("nucleo", "0004_max_causas_por_pagina_12")]

    operations = [migrations.RunPython(acotar_cupos_usados, migrations.RunPython.noop)]
