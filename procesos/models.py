"""
Registro de cada corrida del motor de generación de cartas, disparada
desde la web (procesos.views). Reemplaza al Excel de seguimiento manual +
la memoria de "quién corrió qué script y cuándo" que hoy no queda
guardada en ningún lado — con esto queda trazabilidad de cada envío
mensual por juzgado.
"""

from django.conf import settings
from django.db import models

from nucleo.models import Juzgado, TipoCarta


class GeneracionCartas(models.Model):
    """Una corrida completa del motor para un juzgado: de la nómina subida
    a los archivos de salida (cartas, seguimiento, duplicados, log de
    errores)."""

    class Estado(models.TextChoices):
        OK = "OK", "Completado"
        CON_ERRORES = "CON_ERRORES", "Completado con errores"
        FALLIDO = "FALLIDO", "Falló"
        # La web reservó la agenda y dejó el trabajo listo: espera al worker
        # (manage.py procesar_generaciones, ver procesos/generacion.py).
        PENDIENTE = "PENDIENTE", "En cola"
        # El worker la está generando. Si muere en el medio, al volver la
        # retoma; si no vuelve, desde la agenda se pueden devolver sus cupos.
        EN_CURSO = "EN_CURSO", "En curso"

    juzgado = models.ForeignKey(Juzgado, on_delete=models.PROTECT, related_name="generaciones")
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="generaciones_cartas",
    )
    creado = models.DateTimeField(auto_now_add=True)
    # Latido del worker: se actualiza al terminar cada paquete de cartas.
    actualizado = models.DateTimeField(auto_now=True)
    lotes_total = models.PositiveIntegerField(default=0)
    lotes_hechos = models.PositiveIntegerField(default=0)

    nombre_archivo_nomina = models.CharField(max_length=255)
    tipo_carta = models.CharField(max_length=20, choices=TipoCarta.choices, default=TipoCarta.CITACION)
    fecha_sentencia = models.DateField(
        null=True, blank=True,
        help_text="Solo en cartas de SENTENCIA: la fecha de la sentencia que se escribió en la carta.",
    )
    anio_periodo = models.PositiveIntegerField(null=True, blank=True)
    mes_periodo = models.PositiveSmallIntegerField(null=True, blank=True)

    total_patentes = models.PositiveIntegerField(default=0)
    total_duplicados = models.PositiveIntegerField(default=0)
    total_sin_causas_validas = models.PositiveIntegerField(default=0)
    total_sin_cupo_agenda = models.PositiveIntegerField(default=0)

    carpeta_salida = models.CharField(
        max_length=500,
        help_text="Ruta relativa a settings.SALIDAS_ROOT donde quedaron los archivos de esta corrida.",
    )
    avisos = models.JSONField(
        default=dict, blank=True,
        help_text="Avisos del motor (filas sin patente, disposiciones sin multa, etc.), para mostrar en el resultado.",
    )
    patentes = models.JSONField(
        default=list, blank=True,
        help_text="Patentes que recibieron carta en esta corrida: sirve para avisar si se repite el envío.",
    )
    cupos_por_bloque = models.JSONField(
        default=dict, blank=True,
        help_text="Cupos que tomó esta corrida de cada bloque de la agenda: {id del bloque: cantidad}.",
    )
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.OK)
    mensaje_error = models.TextField(blank=True)

    class Meta:
        verbose_name = "Generación de cartas"
        verbose_name_plural = "Generaciones de cartas"
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.juzgado.slug} · {self.creado:%d-%m-%Y %H:%M} · {self.usuario.get_username()}"


class ArchivoGenerado(models.Model):
    """Un archivo descargable producido por una GeneracionCartas: un lote
    de cartas, el Excel de seguimiento, el reporte de duplicados o el log
    de errores de procesamiento."""

    class Tipo(models.TextChoices):
        CARTAS = "CARTAS", "Cartas (lote)"
        SEGUIMIENTO = "SEGUIMIENTO", "Excel de seguimiento"
        DUPLICADOS = "DUPLICADOS", "Reporte de duplicados"
        LOG_ERRORES = "LOG_ERRORES", "Log de errores de procesamiento"

    generacion = models.ForeignKey(GeneracionCartas, on_delete=models.CASCADE, related_name="archivos")
    tipo = models.CharField(max_length=20, choices=Tipo.choices)
    ruta = models.CharField(max_length=500, help_text="Ruta relativa a settings.SALIDAS_ROOT.")
    nombre_descarga = models.CharField(max_length=255)

    class Meta:
        verbose_name = "Archivo generado"
        verbose_name_plural = "Archivos generados"

    def __str__(self):
        return self.nombre_descarga
