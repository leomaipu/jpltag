from django.contrib import admin

from .models import BloqueHorario, Juzgado, PerfilUsuario


@admin.register(Juzgado)
class JuzgadoAdmin(admin.ModelAdmin):
    list_display = ("slug", "nombre", "dia_desde", "dia_hasta", "utm_valor", "activo")
    list_filter = ("activo",)
    search_fields = ("slug", "nombre")


@admin.register(BloqueHorario)
class BloqueHorarioAdmin(admin.ModelAdmin):
    list_display = ("juzgado", "fecha", "hora", "cupos_totales", "cupos_usados", "cupos_disponibles")
    list_filter = ("juzgado", "fecha")
    ordering = ("fecha", "hora")


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    list_display = ("usuario", "rol", "juzgado")
    list_filter = ("rol", "juzgado")
    autocomplete_fields = ("usuario",)
