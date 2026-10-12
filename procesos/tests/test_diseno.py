"""
Diseño (manual de marca): la CSP de producción (config/settings.py,
SECURE_CSP) solo deja cargar estilos, scripts y fuentes propios. Estas
pruebas protegen lo que eso exige —sin <style>, style="", on*="" ni
recursos externos en ninguna pantalla— y que los estáticos se publiquen
bien con el almacenamiento de producción (manifiesto con hash).
"""

import re
import tempfile
from pathlib import Path

from django.contrib.staticfiles import finders
from django.core.management import call_command
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

from nucleo.models import BloqueHorario, Rol
from procesos.forms import PlantillaJuzgadoForm

from .fixtures import crear_juzgado, crear_usuario, ingresar

PROHIBIDO_INLINE = [
    (re.compile(r"<style\b", re.I), "bloque <style>"),
    (re.compile(r"""\sstyle\s*=""", re.I), "atributo style="),
    (re.compile(r"""\son[a-z]+\s*=""", re.I), "manejador on*="),
    (re.compile(r"javascript:", re.I), "URL javascript:"),
    (re.compile(r"""(?:src|href)\s*=\s*["']https?://""", re.I), "recurso externo"),
    (re.compile(r"<script(?![^>]*\ssrc=)[^>]*>", re.I), "script en línea"),
]


def problemas_de_csp(html):
    return [nombre for patron, nombre in PROHIBIDO_INLINE if patron.search(html)]


class SinCodigoEnLineaTest(TestCase):
    """Cada pantalla, para cada rol, se ve igual con la CSP estricta."""

    def setUp(self):
        self.j1 = crear_juzgado("jpl1", "1er Juzgado de Policía Local de Maipú")
        self.j2 = crear_juzgado("jpl2", "2do Juzgado de Policía Local de Maipú")
        BloqueHorario.objects.create(
            juzgado=self.j1, fecha="2026-10-14", hora="09:00", cupos_totales=12,
        )
        crear_usuario("secretaria", Rol.SECRETARIO, self.j1)
        crear_usuario("jueza", Rol.JUEZ, self.j1)
        crear_usuario("operador", Rol.OPERADOR, self.j1)
        crear_usuario("admin", Rol.ADMINISTRADOR, None)

    def _rutas(self):
        return ["/", "/subir/", "/agenda/", "/plantilla/"]

    def test_pantallas_de_cada_rol_no_usan_nada_que_la_csp_bloquee(self):
        for usuario in ("secretaria", "jueza", "operador", "admin"):
            self.client.logout()
            ingresar(self.client, usuario)
            for ruta in self._rutas():
                resp = self.client.get(ruta)
                if resp.status_code != 200:
                    continue  # un rol sin permiso recibe 403: se prueba abajo
                self.assertEqual(
                    problemas_de_csp(resp.content.decode()), [], f"{usuario} {ruta}",
                )

    def test_ingreso_y_paginas_de_error(self):
        self.client.logout()
        self.assertEqual(problemas_de_csp(self.client.get("/ingresar/").content.decode()), [])
        ingresar(self.client, "secretaria")
        resp = self.client.get("/no-existe/")
        self.assertEqual(resp.status_code, 404)
        html = resp.content.decode()
        self.assertEqual(problemas_de_csp(html), [])
        self.assertIn("Maipú", html)
        for plantilla in ("500.html", "procesos/bloqueado.html"):
            self.assertEqual(problemas_de_csp(render_to_string(plantilla)), [], plantilla)

    def test_403_conserva_el_mensaje_y_la_marca(self):
        ingresar(self.client, "operador")
        resp = self.client.get("/agenda/")
        if resp.status_code == 403:
            self.assertEqual(problemas_de_csp(resp.content.decode()), [])
            self.assertIn("Maipú", resp.content.decode())

    def test_los_estaticos_que_cita_cada_pantalla_existen(self):
        ingresar(self.client, "secretaria")
        for ruta in ["/"] + self._rutas():
            html = self.client.get(ruta).content.decode()
            for ref in re.findall(r"""/static/([^"'#?\s]+)""", html):
                self.assertIsNotNone(finders.find(ref), f"{ruta}: falta {ref}")


class EstaticosEnProduccionTest(TestCase):
    """collectstatic con el almacenamiento real (manifiesto + hash +
    compresión): los CSS que citan la fuente y el logo deben seguir
    resolviendo, o la app llega sin tipografía a producción."""

    def test_collectstatic_publica_css_fuente_y_sprite_con_hash(self):
        with tempfile.TemporaryDirectory() as raiz:
            with override_settings(
                STATIC_ROOT=raiz,
                STORAGES={
                    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                    "staticfiles": {
                        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
                    },
                },
            ):
                call_command("collectstatic", interactive=False, verbosity=0)
                from django.contrib.staticfiles.storage import staticfiles_storage
                css = staticfiles_storage.url("procesos/css/app.css")
                self.assertRegex(css, r"app\.[0-9a-f]{12}\.css$")
                fuentes = list(Path(raiz, "procesos", "fonts").glob("rubik-*.woff2"))
                self.assertTrue(any(re.search(r"\.[0-9a-f]{12}\.woff2$", f.name) for f in fuentes))
                contenido = next(Path(raiz, "procesos", "css").glob("app.*.css")).read_text()
                self.assertRegex(contenido, r"rubik-latin-wght-normal\.[0-9a-f]{12}\.woff2")


class ComponentesTest(TestCase):
    def test_cupos_usa_meter_con_umbrales(self):
        j = crear_juzgado()
        bloque = BloqueHorario.objects.create(
            juzgado=j, fecha="2026-10-14", hora="09:00", cupos_totales=10,
        )
        html = render_to_string("procesos/_cupos.html", {"b": bloque})
        self.assertIn("<meter", html)

    def test_widget_de_archivo_no_enlaza_a_media_y_habla_en_espanol(self):
        j = crear_juzgado()
        j.firma.name = "firmas/ejemplo.png"
        form = PlantillaJuzgadoForm(instance=j)
        html = str(form["firma"])
        self.assertNotIn("media/", html)
        self.assertNotIn("Currently", html)
        self.assertIn("Quitar el archivo cargado", html)


class ComportamientosNuevosTest(TestCase):
    def setUp(self):
        self.j1 = crear_juzgado("jpl1", "1er Juzgado de Policía Local de Maipú")
        self.j2 = crear_juzgado("jpl2", "2do Juzgado de Policía Local de Maipú")
        crear_usuario("secretaria", Rol.SECRETARIO, self.j1)

    def test_pedir_otro_juzgado_da_403_con_pagina_de_marca_y_uno_inexistente_404(self):
        ingresar(self.client, "secretaria")
        resp = self.client.get("/agenda/?juzgado=jpl2")
        self.assertEqual(resp.status_code, 403)
        self.assertIn("Maipú", resp.content.decode())
        self.assertEqual(self.client.get("/agenda/?juzgado=no-existe").status_code, 404)

    def test_formulario_con_errores_muestra_resumen_con_enlaces(self):
        ingresar(self.client, "secretaria")
        resp = self.client.post("/subir/", {"tipo_carta": "SENTENCIA", "juzgado": "jpl1"})
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="errores"', html)
        self.assertIn('role="alert"', html)
        self.assertIn('href="#id_nomina"', html)
        self.assertIn("Elige el Excel de la nómina del mes.", html)
        self.assertIn("Indica la fecha de la sentencia", html)
        # sin archivo no hay nada que "volver a elegir"
        self.assertNotIn("vuelve a elegirlos", html)

    def test_no_quedan_aria_describedby_colgantes_en_subir(self):
        ingresar(self.client, "secretaria")
        html = self.client.get("/subir/").content.decode()
        for ref in re.findall(r'aria-describedby="([^"]+)"', html):
            for id_ in ref.split():
                self.assertIn(f'id="{id_}"', html, f"aria-describedby apunta a {id_}")
        self.assertIn('role="radiogroup"', html)

    def test_pantallas_sin_sesion_llevan_el_logo_y_no_el_riel(self):
        html = self.client.get("/ingresar/").content.decode()
        self.assertNotIn("app--rail", html)
        resp = self.client.get("/no-existe/")
        self.assertNotIn("app--rail", resp.content.decode())
        self.assertIn("cabecera-simple", resp.content.decode())

    def test_login_con_clave_mala_habla_en_espanol_de_chile(self):
        crear_usuario("operador", Rol.OPERADOR, self.j1)
        resp = self.client.post("/ingresar/", {"username": "operador", "password": "mala"})
        self.assertContains(resp, "El usuario o la clave no coinciden")

    def test_un_excel_corrupto_se_explica_sin_jerga_de_pandas(self):
        from django.core.files.uploadedfile import SimpleUploadedFile
        ingresar(self.client, "secretaria")
        # SALIDAS_ROOT apunta a una carpeta temporal: en la imagen de
        # despliegue /app/salidas no es escribible durante las pruebas.
        with tempfile.TemporaryDirectory() as salidas, override_settings(SALIDAS_ROOT=salidas), \
                self.assertLogs("procesos.views", level="ERROR"):
            resp = self.client.post("/subir/", {
                "juzgado": "jpl1", "tipo_carta": "CITACION", "nombre_firmante": "Ana",
                "cargo_firma": "SECRETARIO", "n_cartas_paquete": "0",
                "nomina": SimpleUploadedFile("nomina.xlsx", b"esto no es un excel"),
            }, follow=True)
        html = resp.content.decode()
        self.assertNotIn("Excel file format cannot be determined", html)
        self.assertNotIn("not a zip file", html)
        self.assertIn("no es un Excel (.xlsx) válido", html)
