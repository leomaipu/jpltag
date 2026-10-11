from django.contrib import admin

from .models import ArchivoGenerado, GeneracionCartas


class ArchivoGeneradoInline(admin.TabularInline):
    model = ArchivoGenerado
    extra = 0
    readonly_fields = ["tipo", "ruta", "nombre_descarga"]
    can_delete = False


@admin.register(GeneracionCartas)
class GeneracionCartasAdmin(admin.ModelAdmin):
    list_display = ["juzgado", "usuario", "creado", "estado", "total_patentes"]
    list_filter = ["juzgado", "estado"]
    search_fields = ["nombre_archivo_nomina", "usuario__username"]
    readonly_fields = [f.name for f in GeneracionCartas._meta.fields]
    inlines = [ArchivoGeneradoInline]

    def has_add_permission(self, request):
        return False
