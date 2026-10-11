from django.db import migrations, models


def bajar_de_20_a_12(apps, schema_editor):
    """20 era el valor por defecto del diseño anterior de la carta. Con las
    plantillas oficiales de los Juzgados (logo + bloque postal + firma) solo
    caben ~14 infracciones por hoja: las filas que quedaron en 20 sin que
    nadie lo tocara pasan al nuevo valor. Cualquier otro número, que
    alguien eligió a propósito, no se toca."""
    Juzgado = apps.get_model("nucleo", "Juzgado")
    Juzgado.objects.filter(max_causas_por_pagina=20).update(max_causas_por_pagina=12)


class Migration(migrations.Migration):

    dependencies = [
        ("nucleo", "0003_tres_plantillas_por_tipo_de_carta"),
    ]

    operations = [
        migrations.AlterField(
            model_name="juzgado",
            name="max_causas_por_pagina",
            field=models.PositiveSmallIntegerField(
                default=12,
                help_text="Infracciones por página de carta; si una patente tiene más, la carta sale en varias partes.",
            ),
        ),
        migrations.RunPython(bajar_de_20_a_12, migrations.RunPython.noop),
    ]
