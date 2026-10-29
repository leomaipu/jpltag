from django.contrib.auth.views import LogoutView
from django.urls import path

from . import views

app_name = "procesos"

urlpatterns = [
    path("ingresar/", views.IngresarView.as_view(), name="ingresar"),
    path("salir/", LogoutView.as_view(), name="salir"),
    path("", views.PanelView.as_view(), name="panel"),
    path("subir/", views.SubirNominaView.as_view(), name="subir_nomina"),
    path("resultado/<int:pk>/", views.ResultadoView.as_view(), name="resultado"),
    path("resultado/<int:pk>/cancelar/", views.CancelarGeneracionView.as_view(), name="cancelar_generacion"),
    path("descargar/<int:pk>/", views.DescargarArchivoView.as_view(), name="descargar"),
    path("descargar/<int:pk>/zip/", views.DescargarZipView.as_view(), name="descargar_zip"),
    path("agenda/", views.AgendaView.as_view(), name="agenda"),
    path("agenda/bloque/<int:pk>/", views.BloqueEditarView.as_view(), name="bloque_editar"),
    path("agenda/bloque/<int:pk>/eliminar/", views.BloqueEliminarView.as_view(), name="bloque_eliminar"),
    path("plantilla/", views.PlantillaView.as_view(), name="plantilla"),
    path("plantilla/imagen/<int:pk>/<str:campo>/", views.VerImagenJuzgadoView.as_view(), name="ver_imagen_juzgado"),
    path("plantilla/descargar/<int:pk>/<str:tipo>/", views.DescargarPlantillaView.as_view(), name="descargar_plantilla"),
    path("plantilla/utm/actualizar/", views.ActualizarUTMView.as_view(), name="actualizar_utm"),
]
