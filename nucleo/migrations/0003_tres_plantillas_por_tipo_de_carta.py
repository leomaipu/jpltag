from django.db import migrations, models


class Migration(migrations.Migration):
    """Una plantilla Word por tipo de carta (citación / citación y sentencia
    / sentencia). La plantilla única que ya existía (plantilla_word) pasa a
    ser la de citación — RenameField conserva el archivo ya subido."""

    dependencies = [
        ("nucleo", "0002_alter_juzgado_plantilla_word_alter_juzgado_utm_valor"),
    ]

    operations = [
        migrations.RenameField(
            model_name="juzgado",
            old_name="plantilla_word",
            new_name="plantilla_citacion",
        ),
        migrations.AlterField(
            model_name="juzgado",
            name="plantilla_citacion",
            field=models.FileField(
                blank=True, null=True, upload_to="juzgados/plantillas/",
                help_text="Carta de CITACIÓN (.docx con marcadores) — vacío usa la plantilla por defecto.",
            ),
        ),
        migrations.AddField(
            model_name="juzgado",
            name="plantilla_citacion_sentencia",
            field=models.FileField(
                blank=True, null=True, upload_to="juzgados/plantillas/",
                help_text="Carta de CITACIÓN Y SENTENCIA (.docx con marcadores) — vacío usa la plantilla por defecto.",
            ),
        ),
        migrations.AddField(
            model_name="juzgado",
            name="plantilla_sentencia",
            field=models.FileField(
                blank=True, null=True, upload_to="juzgados/plantillas/",
                help_text="Carta de SENTENCIA (.docx con marcadores) — vacío usa la plantilla por defecto.",
            ),
        ),
    ]
