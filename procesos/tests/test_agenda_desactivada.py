"""
La agenda desacoplada de la generación de cartas (settings.JPLTAG_AGENDA_ACTIVA
apagada, el valor por defecto desde el 29-oct-2026): no se toman cupos y toda
citación —también la de «Citación y sentencia»— sale «a coordinar con el
Juzgado». La pantalla de la Agenda sigue disponible.
"""

import io
import shutil
import tempfile
from datetime import date, time, timedelta

from django.test import TestCase, override_settings
from docx import Document

from motor.plantilla import texto_de
from nucleo.models import BloqueHorario, Rol
from procesos.models import ArchivoGenerado, GeneracionCartas

from .fixtures import construir_nomina_xlsx, crear_juzgado, crear_usuario, fila_nomina, ingresar

_TMP_SALIDAS = tempfile.mkdtemp(prefix="jpltag-pruebas-salidas-sin-agenda-")


@override_settings(SALIDAS_ROOT=_TMP_SALIDAS)
class AgendaDesactivadaTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.j1 = crear_juzgado(slug="jpl1")
        crear_usuario("sec1", rol=Rol.SECRETARIO, juzgado=self.j1)
        ingresar(self.client, "sec1")
        # Hay bloques con cupos, pero las cartas no deben tocarlos.
        self.bloque = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=date.today() + timedelta(days=5), hora=time(9, 30), cupos_totales=5,
        )

    def _subir(self, tipo):
        filas = [fila_nomina(pat=p, nro_parte=str(4000 + i), fecha="03/09/2026") for i, p in enumerate(("AAAA11", "BBBB22"))]
        return self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx(filas), "tipo_carta": tipo,
            "nombre_firmante": "Secretaria de Prueba", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        }, follow=True)

    def _texto_de_las_cartas(self, corrida):
        archivo = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.CARTAS)
        resp = self.client.get(f"/descargar/{archivo.pk}/")
        return texto_de(Document(io.BytesIO(b"".join(resp.streaming_content))))

    def test_la_citacion_sale_a_coordinar_y_no_toma_cupos(self):
        resp = self._subir("CITACION")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual(corrida.cupos_por_bloque, {})
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 0)
        self.assertIn("en fecha y hora a coordinar con el Juzgado", self._texto_de_las_cartas(corrida))
        self.assertNotIn(self.bloque.fecha.strftime("%d/%m/%Y"), self._texto_de_las_cartas(corrida))
        self.assertContains(resp, "A coordinar con el Juzgado (agenda desactivada)")
        self.assertNotContains(resp, "Patentes sin cupo de agenda")

    def test_la_citacion_y_sentencia_tambien_se_genera_a_coordinar(self):
        self._subir("CITACION_SENTENCIA")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertIn("a coordinar con el Juzgado", self._texto_de_las_cartas(corrida))
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 0)

    def test_el_log_no_anota_a_todas_como_sin_cupo(self):
        self._subir("CITACION")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.total_sin_cupo_agenda, 0)
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.LOG_ERRORES).exists())

    def test_las_pantallas_lo_dicen(self):
        subir = self.client.get("/subir/")
        self.assertContains(subir, "Por ahora las cartas no usan la agenda")
        self.assertContains(subir, '<span class="chip">A coordinar</span>')
        self.assertNotContains(subir, "Usa la agenda")
        agenda = self.client.get("/agenda/?juzgado=jpl1")
        self.assertEqual(agenda.status_code, 200)
        self.assertContains(agenda, "las citaciones salen «a coordinar con el Juzgado»")

    @override_settings(JPLTAG_AGENDA_ACTIVA=True)
    def test_con_la_agenda_activa_vuelve_a_tomar_cupos(self):
        self.assertContains(self.client.get("/subir/"), "Usa la agenda")
        self._subir("CITACION")
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 2)
