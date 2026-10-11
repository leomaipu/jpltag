from django import forms

from motor.plantilla import validar_plantilla
from nucleo.models import BloqueHorario, Juzgado


class SubirNominaForm(forms.Form):
    nomina = forms.FileField(
        label="Nómina del mes (.xlsx)",
        help_text="El Excel con las infracciones del mes, tal como lo entrega la DAF.",
    )
    tipo_causas = forms.FileField(
        label="Tabla de multas por tipo de causa (opcional)",
        required=False,
        help_text="tipo_causas.xlsx — si no se adjunta, se usa 1 UTM fija para todas las causas.",
    )
    nombre_firmante = forms.CharField(
        label="Nombre de quien firma", max_length=150, required=False,
    )
    cargo_firma = forms.ChoiceField(
        label="Cargo de quien firma",
        choices=[("SECRETARIO", "Secretario"), ("SECRETARIA", "Secretaria")],
        required=False,
    )
    n_cartas_paquete = forms.IntegerField(
        label="Cartas por paquete (0 = todas en 1 solo archivo)",
        min_value=0, required=False, initial=0,
        help_text="Para repartir la impresión en tandas, ej. 100 cartas por documento.",
    )

    def _validar_excel(self, archivo):
        if archivo and not archivo.name.lower().endswith((".xlsx", ".xls")):
            raise forms.ValidationError("El archivo debe ser un Excel (.xlsx).")
        return archivo

    def clean_nomina(self):
        return self._validar_excel(self.cleaned_data["nomina"])

    def clean_tipo_causas(self):
        return self._validar_excel(self.cleaned_data.get("tipo_causas"))


class PlantillaJuzgadoForm(forms.ModelForm):
    """Módulo 1 (plantilla Word) + Módulo 2 (logo/sello/firma) en una
    sola pantalla: ambos son parte de la identidad/diseño de la carta de
    un Juzgado. logo/sello_judicial/firma ya se validan como imagen real
    por ser ImageField (Pillow, ver requirements.in); plantilla_word
    necesita su propia validación porque un .docx con un marcador mal
    escrito (ej. falta un {%tr endfor %}) recién se nota al generar
    cartas de verdad — clean_plantilla_word() lo prueba antes."""

    class Meta:
        model = Juzgado
        fields = ["logo", "sello_judicial", "firma", "plantilla_word"]
        labels = {
            "logo": "Logo institucional",
            "sello_judicial": "Sello judicial",
            "firma": "Firma",
            "plantilla_word": "Plantilla de la carta (.docx)",
        }

    def clean_plantilla_word(self):
        archivo = self.cleaned_data.get("plantilla_word")
        # FileField.clean() solo entrega un archivo NUEVO acá — si la
        # persona no tocó el campo, cleaned_data trae el valor ya
        # guardado (una ruta, no un archivo subido) y no hay nada que
        # validar de nuevo.
        if archivo and hasattr(archivo, "read"):
            if not archivo.name.lower().endswith(".docx"):
                raise forms.ValidationError("La plantilla debe ser un archivo Word (.docx).")
            es_valida, mensaje = validar_plantilla(archivo)
            archivo.seek(0)
            if not es_valida:
                raise forms.ValidationError(
                    "No se pudo generar una carta de prueba con esta plantilla — revisa "
                    "los marcadores (¿falta un {%tr endfor %} o {{ }} quedó mal escrito?). "
                    f"Detalle técnico: {mensaje}"
                )
        return archivo


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
