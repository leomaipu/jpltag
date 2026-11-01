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

    def _subir(self, tipo, citaciones=None):
        """`citaciones`: fechas de citación por fila (None = nómina sin esa columna)."""
        filas = [fila_nomina(pat=p, nro_parte=str(4000 + i), fecha="03/09/2026") for i, p in enumerate(("AAAA11", "BBBB22"))]
        if citaciones is not None:
            for f, c in zip(filas, citaciones):
                f["FECHA CITACIÓN"] = c
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
        self.assertContains(resp, "A coordinar con el Juzgado (la nómina no trae FECHA CITACIÓN)")
        self.assertNotContains(resp, "Patentes sin cupo de agenda")

    def test_la_audiencia_sale_con_la_fecha_de_citacion_de_la_nomina(self):
        futura = (date.today() + timedelta(days=20)).strftime("%d/%m/%Y")
        resp = self._subir("CITACION", citaciones=[futura, futura])
        corrida = GeneracionCartas.objects.get()
        texto = self._texto_de_las_cartas(corrida)
        self.assertIn(f"el día {futura}", texto)
        self.assertNotIn("a coordinar", texto)
        self.assertNotRegex(texto, rf"{futura}, a las")
        self.assertContains(resp, "Fecha de citación tomada de la nómina")
        self.assertNotContains(resp, "Revisa la fecha de citación")
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 0)

    def test_citacion_y_sentencia_tambien_usa_la_fecha_de_la_nomina(self):
        futura = (date.today() + timedelta(days=20)).strftime("%d/%m/%Y")
        self._subir("CITACION_SENTENCIA", citaciones=[futura, futura])
        self.assertIn(f"el día {futura}", self._texto_de_las_cartas(GeneracionCartas.objects.get()))

    def test_fecha_en_el_pasado_se_respeta_pero_avisa(self):
        resp = self._subir("CITACION", citaciones=["24/08/2026", "24/08/2026"])
        self.assertIn("el día 24/08/2026", self._texto_de_las_cartas(GeneracionCartas.objects.get()))
        self.assertContains(resp, "fecha de citación anterior a hoy")

    def test_fecha_vacia_sale_a_coordinar_y_avisa(self):
        futura = (date.today() + timedelta(days=20)).strftime("%d/%m/%Y")
        resp = self._subir("CITACION", citaciones=[futura, ""])
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertIn(f"el día {futura}", texto)
        self.assertIn("a coordinar con el Juzgado", texto)
        self.assertContains(resp, "sin fecha de citación legible")

    def test_fecha_con_hora_sin_segundos_y_celda_datetime(self):
        from datetime import datetime
        fut = date.today() + timedelta(days=20)
        resp = self._subir("CITACION", citaciones=[f"{fut:%d/%m/%Y} 09:30", datetime(fut.year, fut.month, fut.day)])
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertEqual(texto.count(f"el día {fut:%d/%m/%Y}"), 2)
        self.assertNotContains(resp, "Revisa la fecha de citación")

    def test_fechas_distintas_en_una_patente_usa_la_mas_temprana_y_avisa(self):
        a, b = date.today() + timedelta(days=20), date.today() + timedelta(days=22)
        filas = [fila_nomina(pat="AAAA11", nro_parte=str(5000 + i), fecha="03/09/2026") for i in range(2)]
        for f, c in zip(filas, (b, a)):
            f["FECHA CITACIÓN"] = c.strftime("%d/%m/%Y")
        resp = self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION",
            "nombre_firmante": "Secretaria de Prueba", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        }, follow=True)
        self.assertIn(f"el día {a:%d/%m/%Y}", self._texto_de_las_cartas(GeneracionCartas.objects.get()))
        self.assertContains(resp, "la carta usa la más temprana")

    def test_ninguna_fecha_legible_no_dice_que_la_tomo_de_la_nomina(self):
        resp = self._subir("CITACION", citaciones=["", ""])
        self.assertContains(resp, "ninguna fecha de citación legible")
        self.assertNotContains(resp, "tomada de la nómina")

    def test_sentencia_no_muestra_avisos_de_citacion(self):
        futura = (date.today() + timedelta(days=20)).strftime("%d/%m/%Y")
        resp = self._subir("SENTENCIA", citaciones=[futura, futura])
        self.assertNotContains(resp, "Revisa la fecha de citación")

    @override_settings(JPLTAG_AGENDA_ACTIVA=True)
    def test_agenda_activa_avisa_que_ignora_la_fecha_de_la_nomina(self):
        futura = (date.today() + timedelta(days=20)).strftime("%d/%m/%Y")
        resp = self._subir("CITACION", citaciones=[futura, futura])
        self.assertContains(resp, "se ignoró la FECHA CITACIÓN de la nómina")

    def test_nomina_sin_columna_avisa(self):
        resp = self._subir("CITACION")
        self.assertContains(resp, "La nómina no trae una columna «FECHA CITACIÓN»")
        self.assertContains(resp, "A coordinar con el Juzgado (la nómina no trae FECHA CITACIÓN)")

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

    def test_el_panel_lo_dice(self):
        self.assertContains(self.client.get("/"), "Las cartas todavía no usan la agenda")

    def test_sin_rol_en_la_nomina_el_resultado_lo_avisa(self):
        # La nómina de prueba trae la columna «Rol», pero vacía.
        resp = self._subir("CITACION")
        self.assertContains(resp, "2 filas de la nómina no traen ROL")

    @override_settings(JPLTAG_AGENDA_ACTIVA=True)
    def test_con_la_agenda_activa_vuelve_a_tomar_cupos(self):
        self.assertContains(self.client.get("/subir/"), "Usa la agenda")
        self._subir("CITACION")
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 2)
