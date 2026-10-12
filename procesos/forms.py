from django import forms
from django.contrib.auth.forms import AuthenticationForm

from motor.plantilla import validar_plantilla
from nucleo.models import BloqueHorario, Juzgado, TipoCarta


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
        label="Nómina del mes (.xlsx)",
        error_messages={"required": "Elige el Excel de la nómina del mes."},
    )
    tipo_causas = forms.FileField(
        label="Tabla de multas por tipo de causa",
        required=False,
        help_text="Opcional. Sin ella, todas las causas cuentan 1 UTM.",
    )
    nombre_firmante = forms.CharField(
        label="Nombre de quien firma", max_length=150, required=False,
        error_messages={"max_length": "El nombre es demasiado largo (máximo 150 letras)."},
    )
    cargo_firma = forms.ChoiceField(
        label="Cargo de quien firma",
        choices=CARGOS_FIRMA,
        required=False,
    )
    n_cartas_paquete = forms.IntegerField(
        label="Cartas por paquete",
        min_value=0, required=False, initial=0,
        error_messages={
            "min_value": "Escribe 0 o un número mayor.",
            "invalid": "Escribe un número entero, por ejemplo 100.",
        },
        help_text="0 deja todas en un solo archivo. Un número mayor reparte la impresión en tandas.",
    )

    def _validar_excel(self, archivo):
        if archivo and not archivo.name.lower().endswith((".xlsx", ".xls")):
            raise forms.ValidationError("El archivo debe ser un Excel (.xlsx).")
        return archivo

    def clean(self):
        datos = super().clean()
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
            if not es_valida and mensaje.startswith("A la plantilla le falta"):
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


class BloqueHorarioForm(forms.ModelForm):
    class Meta:
        model = BloqueHorario
        fields = ["fecha", "hora", "cupos_totales"]
        labels = {
            "fecha": "Fecha",
            "hora": "Hora",
            "cupos_totales": "Cupos",
        }
        widgets = {
            "fecha": forms.DateInput(attrs={"type": "date"}),
            "hora": forms.TimeInput(attrs={"type": "time"}),
        }
