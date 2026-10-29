from datetime import time, timedelta

from django import forms
from django.conf import settings
from django.contrib.auth.forms import AuthenticationForm
from django.core.validators import MaxValueValidator

from motor.plantilla import MARCADOR_DESCONOCIDO, validar_plantilla
from nucleo.models import BloqueHorario, Juzgado, TipoCarta, ahora_local

from .generacion import INCLUIR_REPETIDAS, OMITIR_REPETIDAS


class IngresarForm(AuthenticationForm):
    """Mismos textos que el resto de la interfaz (tú, español de Chile)."""

    error_messages = {
        "invalid_login": "El usuario o la clave no coinciden. Revisa los datos e inténtalo de nuevo.",
        "inactive": "Esta cuenta está desactivada. Pide ayuda a un Administrador.",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["username"].error_messages["required"] = "Escribe tu usuario."
        self.fields["password"].error_messages["required"] = "Escribe tu clave."


MENSAJES_ARCHIVO = {
    "invalid_image": "Ese archivo no es una imagen válida. Sube un PNG o JPG.",
    "invalid": "El archivo no es válido. Elige otro.",
    "missing": "No se recibió el archivo. Vuelve a elegirlo.",
    "empty": "El archivo está vacío.",
    "contradiction": "Elige un archivo nuevo o marca quitar el actual, no ambas cosas.",
}


class ArchivoWidget(forms.ClearableFileInput):
    """Campo de archivo sin el enlace "Actualmente: …" de Django (apuntaría
    a MEDIA_URL, que no se sirve por HTTP: ver DESPLIEGUE.md) y con el
    "Quitar" en palabras de esta app."""

    template_name = "procesos/widgets/archivo.html"
    clear_checkbox_label = "Quitar el archivo cargado"


CARGOS_FIRMA = [
    ("SECRETARIO", "Secretario"),
    ("SECRETARIA", "Secretaria"),
    ("SECRETARIO ABOGADO", "Secretario abogado"),
    ("SECRETARIA ABOGADA", "Secretaria abogada"),
]


class SubirNominaForm(forms.Form):
    tipo_carta = forms.ChoiceField(
        label="Tipo de carta",
        choices=TipoCarta.choices,
        initial=TipoCarta.CITACION,
        widget=forms.RadioSelect,
    )
    fecha_audiencia = forms.DateField(
        label="Fecha de la sentencia", required=False,
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Se escribe en cada carta. Una sola fecha por tanda.",
    )
    nomina = forms.FileField(
        label="Nómina del mes (.xlsx)", required=False,
    )
    # La nómina que quedó guardada cuando se preguntó por las patentes
    # repetidas (procesos.generacion.EnvioRepetido): así no se sube dos veces.
    nomina_guardada = forms.CharField(required=False, widget=forms.HiddenInput)
    tipo_causas = forms.FileField(
        label="Tabla de multas por tipo de causa",
        required=False,
        help_text="Opcional. Sin ella, todas las causas cuentan 1 UTM.",
    )
    # Sin valor por defecto a propósito: quien firma no es necesariamente
    # quien está conectado (un Operador no firma como Secretario).
    nombre_firmante = forms.CharField(
        label="Nombre de quien firma", max_length=150,
        error_messages={
            "required": "Escribe el nombre de quien firma las cartas.",
            "max_length": "El nombre es demasiado largo (máximo 150 letras).",
        },
    )
    cargo_firma = forms.ChoiceField(
        label="Cargo de quien firma",
        choices=[("", "Elige el cargo")] + CARGOS_FIRMA,
        error_messages={"required": "Elige el cargo de quien firma."},
    )
    # Tope y textos en __init__: dependen de settings.JPLTAG_MAX_CARTAS_POR_ARCHIVO.
    n_cartas_paquete = forms.IntegerField(label="Cartas por paquete", min_value=0, required=False)
    # Se muestra solo cuando la generación avisó que hay patentes que ya
    # recibieron carta en el mismo período (procesos.generacion.EnvioRepetido).
    repetidas = forms.ChoiceField(
        label="Las patentes que ya recibieron carta",
        choices=[
            (OMITIR_REPETIDAS, "Generar solo las que faltan"),
            (INCLUIR_REPETIDAS, "Generar todas de nuevo (saldrán dos cartas para las repetidas)"),
        ],
        required=False, widget=forms.RadioSelect,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        tope = settings.JPLTAG_MAX_CARTAS_POR_ARCHIVO
        campo = self.fields["n_cartas_paquete"]
        campo.max_value = tope
        campo.validators.append(MaxValueValidator(tope))
        campo.widget.attrs.update({"min": 0, "max": tope})
        campo.initial = tope
        campo.error_messages = {
            "min_value": "Escribe 0 o un número mayor.",
            "max_value": f"El máximo es {tope} cartas por archivo.",
            "invalid": f"Escribe un número entero, por ejemplo {tope}.",
        }
        campo.help_text = (
            f"Reparte la impresión en archivos de hasta {tope} cartas. "
            f"0 deja todas en un solo archivo (solo si son {tope} o menos)."
        )

    def _validar_excel(self, archivo):
        if archivo and not archivo.name.lower().endswith((".xlsx", ".xls")):
            raise forms.ValidationError("El archivo debe ser un Excel (.xlsx).")
        return archivo

    def clean(self):
        datos = super().clean()
        if not datos.get("nomina") and not datos.get("nomina_guardada"):
            self.add_error("nomina", "Elige el Excel de la nómina del mes.")
        if datos.get("tipo_carta") == TipoCarta.SENTENCIA and not datos.get("fecha_audiencia"):
            self.add_error(
                "fecha_audiencia",
                "Indica la fecha de la sentencia: va escrita en la carta.",
            )
        return datos

    def clean_nomina(self):
        return self._validar_excel(self.cleaned_data["nomina"])

    def clean_tipo_causas(self):
        return self._validar_excel(self.cleaned_data.get("tipo_causas"))


class PlantillaJuzgadoForm(forms.ModelForm):
    """Módulo 1 (plantillas Word, una por tipo de carta) + Módulo 2
    (logo/sello/firma) en una sola pantalla: ambos son parte de la
    identidad/diseño de las cartas de un Juzgado. logo/sello_judicial/firma
    ya se validan como imagen real por ser ImageField (Pillow, ver
    requirements.in); cada plantilla Word necesita su propia validación
    porque un .docx con un marcador mal escrito (ej. falta un {%tr
    endfor %}) o sin los datos del infractor recién se nota al generar
    cartas de verdad — _validar_plantilla() lo prueba antes de guardar."""

    class Meta:
        model = Juzgado
        fields = [
            "logo", "sello_judicial", "firma",
            "plantilla_citacion", "plantilla_citacion_sentencia", "plantilla_sentencia",
        ]
        widgets = {
            campo: ArchivoWidget() for campo in (
                "logo", "sello_judicial", "firma",
                "plantilla_citacion", "plantilla_citacion_sentencia", "plantilla_sentencia",
            )
        }
        help_texts = {
            campo: "" for campo in (
                "logo", "sello_judicial", "firma",
                "plantilla_citacion", "plantilla_citacion_sentencia", "plantilla_sentencia",
            )
        }
        labels = {
            "logo": "Logo institucional",
            "sello_judicial": "Sello judicial",
            "firma": "Firma",
            "plantilla_citacion": "Carta de citación (.docx)",
            "plantilla_citacion_sentencia": "Carta de citación y sentencia (.docx)",
            "plantilla_sentencia": "Carta de sentencia (.docx)",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            campo.error_messages.update(MENSAJES_ARCHIVO)

    def _validar_plantilla(self, campo, tipo):
        archivo = self.cleaned_data.get(campo)
        # FileField.clean() solo entrega un archivo NUEVO acá — si la
        # persona no tocó el campo, cleaned_data trae el valor ya
        # guardado (una ruta, no un archivo subido) y no hay nada que
        # validar de nuevo.
        if archivo and hasattr(archivo, "read"):
            if not archivo.name.lower().endswith(".docx"):
                raise forms.ValidationError("La plantilla debe ser un archivo Word (.docx).")
            es_valida, mensaje = validar_plantilla(archivo, tipo)
            archivo.seek(0)
            if not es_valida and mensaje.startswith(("A la plantilla le falta", MARCADOR_DESCONOCIDO)):
                raise forms.ValidationError(mensaje)
            if not es_valida and ("zip" in mensaje.lower() or "package" in mensaje.lower()):
                raise forms.ValidationError(
                    "El archivo no es un Word (.docx) válido. Ábrelo en Word y guárdalo como "
                    "«Documento de Word (.docx)»."
                )
            if not es_valida:
                raise forms.ValidationError(
                    "No se pudo generar una carta de prueba con esta plantilla — revisa "
                    "los marcadores (¿falta un {%tr endfor %} o {{ }} quedó mal escrito?). "
                    f"Detalle técnico: {mensaje}"
                )
        return archivo

    def clean_plantilla_citacion(self):
        return self._validar_plantilla("plantilla_citacion", TipoCarta.CITACION)

    def clean_plantilla_citacion_sentencia(self):
        return self._validar_plantilla("plantilla_citacion_sentencia", TipoCarta.CITACION_SENTENCIA)

    def clean_plantilla_sentencia(self):
        return self._validar_plantilla("plantilla_sentencia", TipoCarta.SENTENCIA)


CUPOS_MAXIMOS = 500
BLOQUES_MAXIMOS_POR_SERIE = 48


ANIOS_MAXIMOS_ADELANTE = 3


def _limites_de_fecha():
    hoy = ahora_local().date()
    return hoy, hoy + timedelta(days=365 * ANIOS_MAXIMOS_ADELANTE)


def _fecha_no_pasada(fecha):
    desde, hasta = _limites_de_fecha()
    if fecha and fecha < desde:
        raise forms.ValidationError(
            "Esa fecha ya pasó. La agenda solo ofrece horarios de hoy en adelante."
        )
    if fecha and fecha > hasta:
        raise forms.ValidationError(
            f"Esa fecha está a más de {ANIOS_MAXIMOS_ADELANTE} años: revisa el año."
        )
    return fecha


def _poner_limites_de_fecha(campo):
    """min y max en el selector de fecha del navegador (el servidor igual valida)."""
    desde, hasta = _limites_de_fecha()
    campo.widget.attrs.update({"min": desde.isoformat(), "max": hasta.isoformat()})


def _hora_ya_paso(fecha, hora):
    """Hoy, pero a una hora que ya pasó: la agenda no la ofrecería nunca."""
    ahora = ahora_local()
    return bool(fecha and hora and fecha == ahora.date() and hora <= ahora.time().replace(second=0, microsecond=0))


class _CuposMixin:
    def clean_cupos_totales(self):
        cupos = self.cleaned_data["cupos_totales"]
        if cupos < 1:
            raise forms.ValidationError("Debe haber al menos 1 cupo.")
        if cupos > CUPOS_MAXIMOS:
            raise forms.ValidationError(f"El máximo es {CUPOS_MAXIMOS} cupos por bloque.")
        return cupos


class BloqueHorarioForm(_CuposMixin, forms.ModelForm):
    """Agrega UN bloque. Recibe el juzgado para avisar de un horario repetido
    en el formulario y no con un error de base de datos."""

    class Meta:
        model = BloqueHorario
        fields = ["fecha", "hora", "cupos_totales"]
        labels = {"fecha": "Fecha", "hora": "Hora", "cupos_totales": "Cupos"}
        widgets = {
            # type=date/time exigen ISO: sin `format`, Django escribe 14/10/2026.
            "fecha": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "hora": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "cupos_totales": forms.NumberInput(attrs={"min": 1, "max": CUPOS_MAXIMOS, "inputmode": "numeric"}),
        }
        error_messages = {
            "fecha": {"required": "Indica la fecha.", "invalid": "Escribe una fecha válida."},
            "hora": {"required": "Indica la hora.", "invalid": "Escribe una hora válida."},
            "cupos_totales": {
                "required": "Indica cuántos cupos hay.", "invalid": "Escribe un número entero.",
                "min_value": "Debe haber al menos 1 cupo.",
                "max_value": f"El máximo es {CUPOS_MAXIMOS} cupos por bloque.",
            },
        }

    def __init__(self, *args, juzgado=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.juzgado = juzgado
        _poner_limites_de_fecha(self.fields["fecha"])

    def clean_fecha(self):
        return _fecha_no_pasada(self.cleaned_data["fecha"])

    def clean(self):
        datos = super().clean()
        fecha, hora = datos.get("fecha"), datos.get("hora")
        if self.juzgado and fecha and hora:
            repetidos = BloqueHorario.objects.filter(juzgado=self.juzgado, fecha=fecha, hora=hora)
            if self.instance.pk:
                repetidos = repetidos.exclude(pk=self.instance.pk)
            if repetidos.exists():
                self.add_error("hora", "Ya hay un bloque a esa hora ese día. Edítalo en vez de crear otro.")
        if self._revisar_hora_pasada(fecha, hora):
            self.add_error("hora", "Esa hora ya pasó hoy. Elige una hora que aún no llegue.")
        return datos

    def _revisar_hora_pasada(self, fecha, hora):
        return _hora_ya_paso(fecha, hora)


class BloqueEditarForm(BloqueHorarioForm):
    """Edita un bloque existente.

    Reglas, todas para no dejar cartas ya emitidas apuntando a algo falso:
    - los cupos no pueden quedar bajo los ya ocupados;
    - si el bloque ya tiene cartas (cupos ocupados), no se mueve de fecha ni
      de hora: esas cartas ya citan a ese horario y cambiarlo no las corrige;
    - "cupos ocupados" solo se corrige a propósito (p. ej. tras una corrida
      de prueba que gastó cupos).
    """

    cupos_usados = forms.IntegerField(
        label="Cupos ocupados", min_value=0, required=False,
        widget=forms.NumberInput(attrs={"min": 0, "inputmode": "numeric"}),
        error_messages={"min_value": "No puede ser negativo.", "invalid": "Escribe un número entero."},
        help_text="Cuántos cupos ya tomaron las cartas emitidas. Corrígelo solo si sabes que no es real.",
    )

    # Los cupos ocupados que tenía la persona delante al abrir la pantalla.
    # Sin esto, enviar el formulario después de que una generación de cartas
    # tomó cupos devolvería el contador a un valor viejo y dejaría el bloque
    # sobrevendido.
    usados_vistos = forms.IntegerField(required=False, min_value=0, widget=forms.HiddenInput)

    # Huella de fecha, hora y cupos al abrir la pantalla: si otra persona
    # guardó el bloque mientras tanto, este envío no lo pisa.
    version = forms.CharField(required=False, widget=forms.HiddenInput)

    motivo_correccion = forms.CharField(
        label="Motivo de la corrección", required=False, max_length=200,
        widget=forms.TextInput(attrs={"maxlength": 200, "autocomplete": "off"}),
        help_text="Queda anotado junto a tu nombre y la fecha.",
    )

    class Meta(BloqueHorarioForm.Meta):
        fields = ["fecha", "hora", "cupos_totales"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cambio_concurrente = False
        self.fields["cupos_usados"].initial = self.instance.cupos_usados
        self.fields["usados_vistos"].initial = self.instance.cupos_usados
        self.fields["version"].initial = self.instance.version
        self.fields["cupos_totales"].widget.attrs["min"] = max(1, self.instance.cupos_usados)
        self._fecha_original = self.instance.fecha
        self._hora_original = self.instance.hora
        self._usados_originales = self.instance.cupos_usados
        if self._usados_originales:
            # Solo informativo: el servidor igual lo hace cumplir (ver clean()).
            for campo in ("fecha", "hora"):
                self.fields[campo].widget.attrs["readonly"] = True
                self.fields[campo].widget.attrs["aria-describedby"] = "aviso-bloqueado"

    @property
    def bloqueado(self):
        return bool(self._usados_originales)

    @property
    def correccion_abierta(self):
        """Que la sección «Corregir cupos ocupados» se muestre abierta."""
        return bool(self.errors.get("cupos_usados") or self.errors.get("motivo_correccion"))

    def _revisar_hora_pasada(self, fecha, hora):
        # Solo si la persona movió el bloque: el que ya está no se re-juzga.
        if fecha == self._fecha_original and hora == self._hora_original:
            return False
        return _hora_ya_paso(fecha, hora)

    def clean_fecha(self):
        fecha = self.cleaned_data["fecha"]
        if fecha != self._fecha_original:
            _fecha_no_pasada(fecha)
        return fecha

    def clean(self):
        datos = super().clean()
        actuales = self._usados_originales
        if datos.get("version") != self.instance.version:
            # Otra persona guardó este bloque después de que se abrió la
            # pantalla (o la pantalla es de antes de este control).
            self.cambio_concurrente = True
            self.add_error(
                None,
                "Otra persona cambió este bloque mientras lo editabas. Abajo están los valores "
                "de ahora: revísalos y vuelve a hacer tu cambio.",
            )
            return datos
        vistos = datos.get("usados_vistos")
        if vistos is None:
            vistos = actuales
        enviados = datos.get("cupos_usados")
        if enviados is None or enviados == vistos:
            usados = actuales            # no los tocó: vale lo que hay hoy
        elif vistos != actuales:
            self.cambio_concurrente = True   # los corrigió, pero cambiaron mientras tanto
            self.add_error(
                None,
                f"Los cupos ocupados cambiaron mientras editabas (ahora son {actuales}). "
                "Revisa el bloque y corrígelos de nuevo si hace falta.",
            )
            usados = actuales
        else:
            usados = enviados
            if len((datos.get("motivo_correccion") or "").strip()) < 5:
                self.add_error(
                    "motivo_correccion",
                    "Escribe el motivo de la corrección: unas palabras bastan.",
                )
        datos["cupos_usados"] = usados
        totales = datos.get("cupos_totales")
        if totales is not None and usados > totales:
            if usados == self._usados_originales:
                self.add_error(
                    "cupos_totales",
                    f"Ya hay {usados} cupos ocupados por cartas emitidas: no puede haber menos. "
                    "Para cerrar el bloque, usa «Cerrar cupos».",
                )
            else:
                self.add_error("cupos_usados", "Los cupos ocupados no pueden superar el total.")
        cambio_horario = (
            datos.get("fecha") not in (None, self._fecha_original)
            or datos.get("hora") not in (None, self._hora_original)
        )
        if cambio_horario and usados > 0:
            self.add_error(
                None,
                f"Este bloque ya tiene {usados} cartas que citan a su fecha y hora actuales: "
                "cambiarlas no corrige esas cartas. Crea un bloque nuevo para las próximas "
                "y cierra este.",
            )
        return datos


class BloqueSerieForm(_CuposMixin, forms.Form):
    """Crea varios bloques de un mismo día, a intervalos regulares."""

    fecha = forms.DateField(
        label="Fecha", widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        error_messages={"required": "Indica la fecha.", "invalid": "Escribe una fecha válida."},
    )
    desde = forms.TimeField(
        label="Primera audiencia", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        error_messages={"required": "Indica la hora de la primera audiencia.", "invalid": "Escribe una hora válida."},
    )
    hasta = forms.TimeField(
        label="Última audiencia", widget=forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        error_messages={"required": "Indica la hora de la última audiencia.", "invalid": "Escribe una hora válida."},
    )
    cada = forms.IntegerField(
        label="Una cada (minutos)", min_value=5, max_value=480, initial=15,
        widget=forms.NumberInput(attrs={"min": 5, "max": 480, "step": 5, "inputmode": "numeric"}),
        error_messages={
            "required": "Indica cada cuántos minutos.", "invalid": "Escribe un número entero.",
            "min_value": "El mínimo es 5 minutos.", "max_value": "El máximo es 480 minutos.",
        },
    )
    cupos_totales = forms.IntegerField(
        label="Cupos de cada una", initial=BloqueHorario._meta.get_field("cupos_totales").default,
        widget=forms.NumberInput(attrs={"min": 1, "max": CUPOS_MAXIMOS, "inputmode": "numeric"}),
        error_messages={
            "required": "Indica cuántos cupos hay.", "invalid": "Escribe un número entero.",
            "min_value": "Debe haber al menos 1 cupo.", "max_value": f"El máximo es {CUPOS_MAXIMOS} cupos por bloque.",
        },
        min_value=1, max_value=CUPOS_MAXIMOS,
    )

    def __init__(self, *args, **kwargs):
        # Esta pantalla tiene dos formularios con campos del mismo nombre:
        # prefijo en los ids para que ningún id se repita en la página.
        kwargs.setdefault("auto_id", "serie_%s")
        super().__init__(*args, **kwargs)
        _poner_limites_de_fecha(self.fields["fecha"])

    def clean_fecha(self):
        return _fecha_no_pasada(self.cleaned_data["fecha"])

    def horas(self):
        """Las horas de la serie, de `desde` a `hasta` incluida."""
        datos = self.cleaned_data
        inicio = datos["desde"].hour * 60 + datos["desde"].minute
        fin = datos["hasta"].hour * 60 + datos["hasta"].minute
        return [time(m // 60, m % 60) for m in range(inicio, fin + 1, datos["cada"])]

    def clean(self):
        datos = super().clean()
        if all(k in datos for k in ("desde", "hasta", "cada")):
            if _hora_ya_paso(datos.get("fecha"), datos["desde"]):
                self.add_error("desde", "Esa hora ya pasó hoy. Empieza en una hora que aún no llegue.")
            elif datos["hasta"] < datos["desde"]:
                self.add_error("hasta", "La última audiencia no puede ser antes que la primera.")
            elif len(self.horas()) > BLOQUES_MAXIMOS_POR_SERIE:
                self.add_error(
                    "cada",
                    f"Así saldrían {len(self.horas())} bloques; el máximo por vez es "
                    f"{BLOQUES_MAXIMOS_POR_SERIE}. Sube el intervalo o acorta el rango.",
                )
        return datos
