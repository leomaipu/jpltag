from django.contrib import admin
from django.urls import include, path


def _solo_superusuarios(request):
    """/admin/ no distingue juzgados: quien entra ve y cambia los 3 (bloques,
    plantillas, firmas, corridas y perfiles, incluido el propio). Por eso
    solo un superusuario; el resto trabaja desde las pantallas de la app,
    que sí aíslan por juzgado."""
    return request.user.is_active and request.user.is_superuser


admin.site.has_permission = _solo_superusuarios

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", include("procesos.urls")),
]
