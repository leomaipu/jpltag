"""
Módulo 1 (plantilla Word editable) + Módulo 2 (logo/sello/firma propios,
sin pasar por /admin/) — ver procesos.views.PlantillaView/
VerImagenJuzgadoView y nucleo.models.PerfilUsuario.puede_editar_plantilla.
"""

import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from nucleo.models import Rol

from .fixtures import (
    construir_nomina_xlsx,
    construir_plantilla_rota,
    construir_plantilla_valida,
    crear_juzgado,
    crear_usuario,
    fila_nomina,
    ingresar,
)

_TMP_MEDIA = tempfile.mkdtemp(prefix="jpltag-pruebas-media-")
_TMP_SALIDAS = tempfile.mkdtemp(prefix="jpltag-pruebas-salidas-plantilla-")


def _imagen_prueba(nombre="logo.png"):
    """Una imagen PNG real de 2x2 — Django usa Pillow (ver ImageField en
    nucleo/models.py) para validar que el contenido subido sea
    efectivamente una imagen, así que no basta con cualquier byte."""
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), color="red").save(buffer, format="PNG")
    buffer.seek(0)
    return SimpleUploadedFile(nombre, buffer.read(), content_type="image/png")


@override_settings(MEDIA_ROOT=_TMP_MEDIA, SALIDAS_ROOT=_TMP_SALIDAS)
class PermisoPlantillaTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.juzgado = crear_juzgado()

    def test_juez_no_puede_ver_la_pantalla_de_plantilla(self):
        crear_usuario("jueza", rol=Rol.JUEZ, juzgado=self.juzgado)
        ingresar(self.client, "jueza")
        resp = self.client.get("/plantilla/")
        self.assertEqual(resp.status_code, 403)

    def test_operador_si_puede_ver_la_pantalla_de_plantilla(self):
        crear_usuario("operador", rol=Rol.OPERADOR, juzgado=self.juzgado)
        ingresar(self.client, "operador")
        resp = self.client.get("/plantilla/")
        self.assertEqual(resp.status_code, 200)


@override_settings(MEDIA_ROOT=_TMP_MEDIA, SALIDAS_ROOT=_TMP_SALIDAS)
class SubirPlantillaYLogosTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.juzgado = crear_juzgado()
        crear_usuario("secretaria", rol=Rol.SECRETARIO, juzgado=self.juzgado)
        ingresar(self.client, "secretaria")

    def test_sube_logo_sello_firma_y_plantilla_validos(self):
        resp = self.client.post("/plantilla/", {
            "juzgado": self.juzgado.slug,
            "logo": _imagen_prueba("logo.png"),
            "sello_judicial": _imagen_prueba("sello.png"),
            "firma": _imagen_prueba("firma.png"),
            "plantilla_word": construir_plantilla_valida(),
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.juzgado.refresh_from_db()
        self.assertTrue(self.juzgado.logo)
        self.assertTrue(self.juzgado.sello_judicial)
        self.assertTrue(self.juzgado.firma)
        self.assertTrue(self.juzgado.plantilla_word)

    def test_plantilla_con_marcador_mal_cerrado_no_se_guarda(self):
        """El corazón de esta prueba: validar_plantilla() (motor/plantilla.py)
        debe atrapar un {%tr for %} sin su {%tr endfor %} ANTES de
        guardar — si no, recién se habría notado la próxima vez que
        alguien subiera una nómina real, con todas las cartas rotas."""
        resp = self.client.post("/plantilla/", {
            "juzgado": self.juzgado.slug,
            "plantilla_word": construir_plantilla_rota(),
        })
        self.assertEqual(resp.status_code, 200)  # vuelve a mostrar el formulario, no redirige
        self.juzgado.refresh_from_db()
        self.assertFalse(self.juzgado.plantilla_word)
        self.assertContains(resp, "No se pudo generar")

    def test_vista_previa_de_imagen_respeta_el_juzgado(self):
        self.client.post("/plantilla/", {
            "juzgado": self.juzgado.slug,
            "logo": _imagen_prueba("logo.png"),
        })
        self.juzgado.refresh_from_db()
        resp = self.client.get(f"/plantilla/imagen/{self.juzgado.pk}/logo/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Type"], "image/png")

    def test_vista_previa_de_imagen_de_otro_juzgado_da_403(self):
        otro_juzgado = crear_juzgado(slug="jpl2", nombre="2do JPL")
        self.client.post(f"/plantilla/?juzgado={self.juzgado.slug}", {
            "juzgado": self.juzgado.slug,
            "logo": _imagen_prueba("logo.png"),
        })
        self.juzgado.refresh_from_db()
        resp = self.client.get(f"/plantilla/imagen/{otro_juzgado.pk}/logo/")
        self.assertEqual(resp.status_code, 403)


@override_settings(MEDIA_ROOT=_TMP_MEDIA, SALIDAS_ROOT=_TMP_SALIDAS)
class PlantillaPropiaSeUsaAlGenerarTest(TestCase):
    """Prueba de punta a punta: una plantilla PROPIA subida por el
    Juzgado (no la plantilla por defecto) debe ser la que efectivamente
    se use al generar las cartas de una nómina real."""

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.juzgado = crear_juzgado()
        self.usuario = crear_usuario("operador", juzgado=self.juzgado)
        ingresar(self.client, "operador")
        self.client.post("/plantilla/", {
            "juzgado": self.juzgado.slug,
            "plantilla_word": construir_plantilla_valida("MARCA DISTINTIVA DEL JUZGADO XYZ"),
        })
        self.juzgado.refresh_from_db()

    def test_la_carta_generada_trae_el_texto_de_la_plantilla_propia(self):
        from docx import Document

        from procesos.models import ArchivoGenerado, GeneracionCartas

        nomina = construir_nomina_xlsx([fila_nomina(pat="AAAA11", fecha="03/09/2026")])
        resp = self.client.post("/subir/", {
            "nomina": nomina,
            "nombre_firmante": "Secretaria de Prueba",
            "cargo_firma": "SECRETARIA",
            "n_cartas_paquete": 0,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)

        generacion = GeneracionCartas.objects.get(juzgado=self.juzgado)
        self.assertEqual(generacion.estado, GeneracionCartas.Estado.OK)
        archivo_cartas = generacion.archivos.get(tipo=ArchivoGenerado.Tipo.CARTAS)

        descarga = self.client.get(f"/descargar/{archivo_cartas.pk}/")
        import io
        contenido = b"".join(descarga.streaming_content)
        doc = Document(io.BytesIO(contenido))
        texto_completo = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("MARCA DISTINTIVA DEL JUZGADO XYZ", texto_completo)
