from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("procesos", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="generacioncartas",
            name="tipo_carta",
            field=models.CharField(
                choices=[
                    ("CITACION", "Citación"),
                    ("CITACION_SENTENCIA", "Citación y sentencia"),
                    ("SENTENCIA", "Sentencia"),
                ],
                default="CITACION", max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="generacioncartas",
            name="fecha_sentencia",
            field=models.DateField(
                blank=True, null=True,
                help_text="Solo en cartas de SENTENCIA: la fecha de la sentencia que se escribió en la carta.",
            ),
        ),
    ]
