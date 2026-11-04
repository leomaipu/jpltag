from django.contrib import admin

from .models import BloqueAjuste, BloqueHorario, ConstanciaUTM, Juzgado, PerfilUsuario

FUENTE_MANUAL = "Ingreso manual"


@admin.register(Juzgado)
class JuzgadoAdmin(admin.ModelAdmin):
    list_display = ("slug", "nombre", "dia_desde", "dia_hasta", "utm_valor", "activo")
    list_filter = ("activo",)
    search_fields = ("slug", "nombre")
    readonly_fields = ("utm_vigente_desde", "utm_fuente", "utm_verificada_en")

    def save_model(self, request, obj, form, change):
        """Un valor a mano pasa por el mismo registro: queda la constancia y
        pierde el mes verificado hasta consultarlo de nuevo."""
        if "utm_valor" not in form.changed_data:
            super().save_model(request, obj, form, change)
            return
        nuevo = obj.utm_valor
        obj.utm_valor = form.initial.get("utm_valor", 0)
        super().save_model(request, obj, form, change)
        obj.registrar_utm(nuevo, None, FUENTE_MANUAL, request.user)


@admin.register(ConstanciaUTM)
class ConstanciaUTMAdmin(admin.ModelAdmin):
    """Solo lectura: es una constancia."""

    list_display = ("creado", "juzgado", "valor_anterior", "valor_nuevo", "vigente_desde", "fuente", "nombre_usuario")
    list_filter = ("juzgado", "fuente")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BloqueHorario)
class BloqueHorarioAdmin(admin.ModelAdmin):
    list_display = ("juzgado", "fecha", "hora", "cupos_totales", "cupos_usados", "cupos_disponibles")
    list_filter = ("juzgado", "fecha")
    ordering = ("fecha", "hora")
    # Los cupos ocupados los cuentan las cartas; corregirlos se hace desde la
    # agenda, que pide el motivo y lo deja anotado.
    readonly_fields = ("cupos_usados",)

    def get_readonly_fields(self, request, obj=None):
        # Con cartas emitidas, la fecha y la hora quedan fijas (igual que en la agenda).
        if obj is not None and obj.cupos_usados:
            return ("cupos_usados", "juzgado", "fecha", "hora")
        return ("cupos_usados",)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.cupos_usados:
            return False
        return super().has_delete_permission(request, obj)

    def delete_queryset(self, request, queryset):
        # La acción «borrar seleccionados» no se salta la regla.
        queryset.filter(cupos_usados=0).delete()


@admin.register(BloqueAjuste)
class BloqueAjusteAdmin(admin.ModelAdmin):
    list_display = ("creado", "juzgado", "fecha", "hora", "desde", "hasta", "nombre_usuario", "motivo")
    list_filter = ("juzgado",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ("usuario", "rol", "juzgado")
    list_filter = ("rol", "juzgado")
    autocomplete_fields = ("usuario",)
