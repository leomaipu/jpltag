from django.conf import settings
from django.db import models
from django.utils import timezone


class TipoCarta(models.TextChoices):
    """Los tres tipos de carta que emite cada Juzgado (los mismos valores
    que motor.plantilla.TIPOS_CARTA)."""

    CITACION = "CITACION", "Citación"
    CITACION_SENTENCIA = "CITACION_SENTENCIA", "Citación y sentencia"
    SENTENCIA = "SENTENCIA", "Sentencia"


class Juzgado(models.Model):
    """Un juzgado de policía local. Reemplaza las constantes que hoy están
    escritas a mano en cada uno de los 6 scripts (NOMBRE_JUZGADO, UTM_VALOR,
    AGENDA_AUDIENCIAS, etc.) por filas de esta tabla, compartidas por la
    única aplicación web (ver "Decisiones confirmadas" de la propuesta,
    10-oct-2026)."""

    slug = models.SlugField(
        max_length=20, unique=True,
        help_text="Identificador corto, ej. 'jpl1', 'jpl2', 'jpl3'.",
    )
    nombre = models.CharField(
        max_length=120,
        help_text="Ej. 'PRIMER JUZGADO DE POLICÍA LOCAL'.",
    )
    direccion = models.CharField(max_length=200, blank=True)

    # Rango de días del mes que procesa este juzgado (hoy: DIA_DESDE/DIA_HASTA)
    dia_desde = models.PositiveSmallIntegerField(default=1)
    dia_hasta = models.PositiveSmallIntegerField(default=10)

    # UTM vigente configurada (Módulo 4 de la propuesta) — se puede dejar
    # fija acá (/admin/) o consultarla con un clic desde /plantilla/, que
    # la compara contra mindicador.cl y la actualiza si cambió (ver
    # motor/utm.py — no la propia API de la CMF, que exige una API key
    # que no se tramitó; decisión confirmada con Leonardo el 11-oct-2026).
    utm_valor = models.PositiveIntegerField(
        default=0,
        help_text="Valor UTM configurado para este juzgado. Se puede actualizar a mano "
                   "o con el botón \"Verificar valor UTM\" en /plantilla/.",
    )

    # Cuántas infracciones caben en UNA página de carta sin que la firma y el
    # texto final salten a una segunda hoja. Medido con las plantillas por
    # defecto y la nómina real de octubre-2026 (disposiciones/lugares de
    # hasta 2 líneas, firma + sello del 2do JPL = la más alta): 14 caben,
    # 16 no. Se deja 12 por holgura. Si un Juzgado sube una plantilla más
    # compacta (o más alta), se ajusta desde /admin/.
    max_causas_por_pagina = models.PositiveSmallIntegerField(
        default=12,
        help_text="Infracciones por página de carta; si una patente tiene más, la carta sale en varias partes.",
    )

    # Módulo 2 (selector de imágenes): el operador/secretario las sube
    # desde su propia pantalla en la app (ver procesos.views.PlantillaView
    # y PuedeEditarPlantillaMixin) — /admin/ sigue funcionando como
    # alternativa, pero ya no es el único camino.
    logo = models.ImageField(upload_to="juzgados/logos/", blank=True, null=True)
    sello_judicial = models.ImageField(upload_to="juzgados/sellos/", blank=True, null=True)
    firma = models.ImageField(upload_to="juzgados/firmas/", blank=True, null=True)

    # Módulo 1 (plantilla Word editable por el juzgado): el diseño Y el texto
    # legal de cada tipo de carta lo define un .docx con marcadores docxtpl,
    # no el código — ver motor/plantilla.py para la lista de marcadores. Una
    # plantilla por tipo de carta. Si un campo queda vacío, motor.plantilla
    # usa la del repo para ese juzgado (motor/plantillas_defecto/jplN/).
    plantilla_citacion = models.FileField(
        upload_to="juzgados/plantillas/", blank=True, null=True,
        help_text="Carta de CITACIÓN (.docx con marcadores) — vacío usa la plantilla por defecto.",
    )
    plantilla_citacion_sentencia = models.FileField(
        upload_to="juzgados/plantillas/", blank=True, null=True,
        help_text="Carta de CITACIÓN Y SENTENCIA (.docx con marcadores) — vacío usa la plantilla por defecto.",
    )
    plantilla_sentencia = models.FileField(
        upload_to="juzgados/plantillas/", blank=True, null=True,
        help_text="Carta de SENTENCIA (.docx con marcadores) — vacío usa la plantilla por defecto.",
    )

    activo = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Juzgado"
        verbose_name_plural = "Juzgados"
        ordering = ["slug"]

    def __str__(self):
        return self.nombre

    CAMPO_PLANTILLA = {
        TipoCarta.CITACION: "plantilla_citacion",
        TipoCarta.CITACION_SENTENCIA: "plantilla_citacion_sentencia",
        TipoCarta.SENTENCIA: "plantilla_sentencia",
    }

    def plantilla_propia(self, tipo):
        """El archivo .docx que este juzgado subió para ese tipo de carta,
        o None si usa la del repo."""
        archivo = getattr(self, self.CAMPO_PLANTILLA[tipo])
        return archivo if archivo else None


def ahora_local():
    """La fecha y hora de hoy en Chile (settings.TIME_ZONE), no en UTC."""
    return timezone.localtime()


class BloqueQuerySet(models.QuerySet):
    def vigentes(self, ahora=None):
        """Los bloques que todavía no empiezan: de hoy en adelante, y los de
        hoy solo si su hora no pasó."""
        ahora = ahora or ahora_local()
        return self.filter(
            models.Q(fecha__gt=ahora.date())
            | models.Q(fecha=ahora.date(), hora__gt=ahora.time().replace(second=0, microsecond=0))
        )

    def pasados(self, ahora=None):
        ahora = ahora or ahora_local()
        return self.exclude(pk__in=self.vigentes(ahora).values("pk"))


class BloqueHorario(models.Model):
    """Un bloque de audiencia con cupos (Módulo 3 — Agenda con cupos).
    Reemplaza la lista fija AGENDA_AUDIENCIAS de cada script."""

    objects = BloqueQuerySet.as_manager()

    juzgado = models.ForeignKey(Juzgado, on_delete=models.CASCADE, related_name="bloques_horario")
    fecha = models.DateField()
    hora = models.TimeField()
    cupos_totales = models.PositiveSmallIntegerField(default=10)
    cupos_usados = models.PositiveSmallIntegerField(default=0)

    class Meta:
        verbose_name = "Bloque de horario"
        verbose_name_plural = "Bloques de horario"
        ordering = ["fecha", "hora"]
        constraints = [
            models.UniqueConstraint(fields=["juzgado", "fecha", "hora"], name="bloque_unico_por_juzgado"),
            # Un bloque nunca tiene más cartas que cupos: si dos procesos
            # intentan sobrevenderlo, la base lo rechaza en vez de esconderlo.
            models.CheckConstraint(
                condition=models.Q(cupos_usados__lte=models.F("cupos_totales")),
                name="bloque_usados_no_supera_totales",
            ),
        ]

    def __str__(self):
        return f"{self.juzgado.slug} · {self.fecha} {self.hora} ({self.cupos_usados}/{self.cupos_totales})"

    @property
    def cupos_disponibles(self):
        return max(self.cupos_totales - self.cupos_usados, 0)

    @property
    def ya_paso(self):
        """Su hora ya pasó: queda en el historial, sin edición."""
        ahora = ahora_local()
        return (self.fecha, self.hora) <= (ahora.date(), ahora.time().replace(second=0, microsecond=0))

    @property
    def version(self):
        """Huella de lo que se edita (fecha, hora, cupos): el formulario la
        lleva oculta para detectar que otra persona lo cambió mientras tanto."""
        import hashlib
        base = f"{self.fecha:%Y-%m-%d}|{self.hora:%H:%M}|{self.cupos_totales}"
        return hashlib.sha1(base.encode()).hexdigest()[:12]


class BloqueAjuste(models.Model):
    """Corrección manual de los cupos ocupados de un bloque. Es la única forma
    de «devolver» cupos, así que deja quién, cuándo y por qué."""

    juzgado = models.ForeignKey(Juzgado, on_delete=models.CASCADE, related_name="ajustes_de_bloque")
    bloque = models.ForeignKey(BloqueHorario, null=True, on_delete=models.SET_NULL, related_name="ajustes")
    # Copia de la fecha y hora: el registro sobrevive aunque el bloque se elimine.
    fecha = models.DateField()
    hora = models.TimeField()
    usuario = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    nombre_usuario = models.CharField(max_length=150)
    creado = models.DateTimeField(auto_now_add=True)
    desde = models.PositiveSmallIntegerField()
    hasta = models.PositiveSmallIntegerField()
    motivo = models.CharField(max_length=200)

    class Meta:
        verbose_name = "Corrección de cupos"
        verbose_name_plural = "Correcciones de cupos"
        ordering = ["-creado"]

    def __str__(self):
        return f"{self.fecha} {self.hora:%H:%M}: {self.desde}→{self.hasta} por {self.nombre_usuario}"


class Rol(models.TextChoices):
    OPERADOR = "OPERADOR", "Operador"
    SECRETARIO = "SECRETARIO", "Secretario(a)"
    JUEZ = "JUEZ", "Jueza/Juez"
    ADMINISTRADOR = "ADMINISTRADOR", "Administrador"


# Matriz de permisos confirmada con el usuario el 10-oct-2026 (ver
# "Decisiones confirmadas" de la propuesta): qué puede hacer cada rol.
PERMISOS_POR_ROL = {
    Rol.OPERADOR: {"plantilla": "editar", "agenda": "editar", "generar_cartas": True, "gestionar_usuarios": False},
    Rol.SECRETARIO: {"plantilla": "editar", "agenda": "editar", "generar_cartas": True, "gestionar_usuarios": False},
    Rol.JUEZ: {"plantilla": "sin_acceso", "agenda": "editar", "generar_cartas": False, "gestionar_usuarios": False},
    Rol.ADMINISTRADOR: {"plantilla": "editar", "agenda": "editar", "generar_cartas": True, "gestionar_usuarios": True},
}


class PerfilUsuario(models.Model):
    """Extiende el usuario de Django con su rol y su juzgado (Módulo 5 —
    Gestión de usuarios y roles). Un Administrador sin juzgado asignado
    (juzgado=None) tiene acceso a los 3."""

    usuario = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="perfil")
    juzgado = models.ForeignKey(
        Juzgado, on_delete=models.SET_NULL, null=True, blank=True, related_name="usuarios",
        help_text="Vacío solo para Administrador: acceso a todos los juzgados.",
    )
    rol = models.CharField(max_length=20, choices=Rol.choices, default=Rol.OPERADOR)

    class Meta:
        verbose_name = "Perfil de usuario"
        verbose_name_plural = "Perfiles de usuario"

    def __str__(self):
        juzgado = self.juzgado.slug if self.juzgado else "todos"
        return f"{self.usuario.get_username()} · {self.get_rol_display()} · {juzgado}"

    @property
    def permisos(self):
        return PERMISOS_POR_ROL[self.rol]

    def puede_editar_plantilla(self):
        return self.permisos["plantilla"] == "editar"

    def puede_editar_agenda(self):
        return self.permisos["agenda"] == "editar"

    def puede_generar_cartas(self):
        return self.permisos["generar_cartas"]

    def puede_gestionar_usuarios(self):
        return self.permisos["gestionar_usuarios"]

    def tiene_acceso_a(self, juzgado: Juzgado) -> bool:
        """Administrador sin juzgado asignado ve los 3; el resto, solo el suyo."""
        if self.rol == Rol.ADMINISTRADOR and self.juzgado_id is None:
            return True
        return self.juzgado_id == juzgado.id
