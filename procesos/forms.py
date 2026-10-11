from django import forms

from nucleo.models import BloqueHorario


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
