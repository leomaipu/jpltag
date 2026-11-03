"""
La agenda desacoplada de la generación de cartas (settings.JPLTAG_AGENDA_ACTIVA
apagada, el valor por defecto desde el 29-oct-2026): no se toman cupos y toda
citación —también la de «Citación y sentencia»— sale «a coordinar con el
Juzgado». La pantalla de la Agenda sigue disponible.
"""

import io
import shutil
import tempfile
from datetime import date, datetime, time, timedelta

from django.test import TestCase, override_settings
from docx import Document

from motor.plantilla import texto_de
from nucleo.models import BloqueHorario, Rol
from procesos.models import ArchivoGenerado, GeneracionCartas

from .fixtures import construir_nomina_xlsx, crear_juzgado, crear_usuario, fila_nomina, ingresar, CAMPOS_REG_Y_AUDIENCIA

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

    def _subir(self, tipo, citaciones=None, fechas=None, reg="R-77", roles=("2026-Y-1001", "2026-Y-1002")):
        """`citaciones`: FECHA CITACIÓN por fila (None = la nómina no trae esa columna).
        `fechas`: lo que se escribe en «Fechas de audiencia» (por defecto una fecha futura
        para toda la nómina; "" = se deja vacío)."""
        filas = [fila_nomina(pat=p, nro_parte=str(4000 + i), fecha="03/09/2026", rol=roles[i])
                 for i, p in enumerate(("AAAA11", "BBBB22"))]
        if citaciones is not None:
            for f, c in zip(filas, citaciones):
                f["FECHA CITACIÓN"] = c
        datos = {
            "nomina": construir_nomina_xlsx(filas), "tipo_carta": tipo,
            "nombre_firmante": "Secretaria de Prueba", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
            **CAMPOS_REG_Y_AUDIENCIA, "reg": reg,
        }
        if fechas is not None:
            datos["fechas_audiencia"] = fechas
        return self.client.post("/subir/", datos, follow=True)

    def _texto_de_las_cartas(self, corrida):
        archivo = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.CARTAS)
        resp = self.client.get(f"/descargar/{archivo.pk}/")
        return texto_de(Document(io.BytesIO(b"".join(resp.streaming_content))))

    @staticmethod
    def _futura(dias=60):
        return (date.today() + timedelta(days=dias)).strftime("%d/%m/%Y")

    # ── fechas de audiencia y Reg N° del formulario ──────────────────────

    def test_la_audiencia_sale_con_la_fecha_del_formulario_sin_hora_y_no_toma_cupos(self):
        fecha = self._futura()
        resp = self._subir("CITACION", fechas=fecha)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual(corrida.cupos_por_bloque, {})
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 0)
        texto = self._texto_de_las_cartas(corrida)
        self.assertEqual(texto.count(f"prestar declaración indagatoria el día {fecha},"), 2)
        self.assertNotIn("a coordinar", texto)
        self.assertNotIn(self.bloque.fecha.strftime("%d/%m/%Y"), texto)
        self.assertContains(resp, "Fechas de audiencia indicadas al generar (sin hora)")
        self.assertNotContains(resp, "Patentes sin cupo de agenda")
        self.assertNotContains(resp, "Revisa la fecha de citación")

    def test_el_reg_del_formulario_sale_en_todas_las_cartas(self):
        self._subir("CITACION", reg="R-4455")
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertEqual(texto.count("Reg N° R-4455"), 2)
        self.assertNotIn("«folio carta trámite»", texto)

    def test_citacion_y_sentencia_usa_la_misma_fecha(self):
        fecha = self._futura()
        self._subir("CITACION_SENTENCIA", fechas=fecha)
        self.assertIn(f"el día {fecha}", self._texto_de_las_cartas(GeneracionCartas.objects.get()))
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 0)

    def test_bloques_de_folios_dan_fechas_distintas_por_carta(self):
        a, b = self._futura(60), self._futura(61)
        resp = self._subir("CITACION", fechas=f"1000-1001 {a}\n1002-1003 {b}")
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertEqual(texto.count(f"el día {a},"), 1)
        self.assertEqual(texto.count(f"el día {b},"), 1)
        self.assertNotContains(resp, "Revisa la fecha de citación")

    def test_causa_fuera_de_todo_bloque_usa_la_fecha_de_la_nomina_y_lo_avisa(self):
        a, nomina = self._futura(60), self._futura(70)
        resp = self._subir("CITACION", fechas=f"1000-1001 {a}", citaciones=[nomina, nomina])
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertIn(f"el día {a},", texto)
        self.assertIn(f"el día {nomina},", texto)
        self.assertContains(resp, "sin ninguna causa dentro de los bloques de folios indicados")

    def test_causa_fuera_de_todo_bloque_y_sin_fecha_en_la_nomina_sale_a_coordinar(self):
        resp = self._subir("CITACION", fechas=f"1000-1001 {self._futura()}")
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertIn("en fecha y hora a coordinar con el Juzgado", texto)
        self.assertContains(resp, "sin ninguna causa dentro de los bloques de folios indicados")

    def test_fechas_distintas_en_una_patente_usa_la_mas_temprana_y_nombra_ejemplos(self):
        a, b = self._futura(60), self._futura(61)
        filas = [fila_nomina(pat="AAAA11", nro_parte=str(4000 + i), rol=f"2026-Y-{1000 + i}") for i in range(2)]
        datos = {"nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION", "nombre_firmante": "X",
                 "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA,
                 "fechas_audiencia": f"1000-1000 {b}\n1001-1001 {a}"}
        resp = self.client.post("/subir/", datos, follow=True)
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertIn(f"el día {a},", texto)
        self.assertNotIn(f"el día {b},", texto)
        self.assertContains(resp, "la carta usa la más temprana (por ejemplo AAAA11)")

    def test_cobertura_parcial_avisa_las_causas_fuera_de_bloque(self):
        filas = [fila_nomina(pat="AAAA11", nro_parte="1", rol="2026-Y-1000"),
                 fila_nomina(pat="AAAA11", nro_parte="2", rol="2026-Y-1001"),
                 fila_nomina(pat="BBBB22", nro_parte="3", rol="2026-Y-9000")]
        datos = {"nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION", "nombre_firmante": "X",
                 "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA,
                 "fechas_audiencia": f"1000-1000 {self._futura()}"}
        resp = self.client.post("/subir/", datos, follow=True)
        self.assertContains(resp, "2 causas fuera de los bloques de folios indicados")
        self.assertContains(resp, "1 patente sin ninguna causa dentro de los bloques")

    def test_el_ampersand_no_corta_la_carta_ni_el_reg(self):
        filas = [fila_nomina(pat="AAAA11", nombre="TRANSPORTES J&J", ap1="<SPA>", ap2="")]
        datos = {"nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION", "nombre_firmante": "X",
                 "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA, "reg": "A&B <1>"}
        self.client.post("/subir/", datos, follow=True)
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertIn("TRANSPORTES J&J <SPA>", texto)
        self.assertIn("Reg N° A&B <1>", texto)

    def test_patente_con_dos_rut_son_dos_cartas_y_el_seguimiento_las_distingue(self):
        filas = [fila_nomina(pat="AAAA11", nro_parte="1", rut="11111111", dv="1", nombre="Ana"),
                 fila_nomina(pat="AAAA11", nro_parte="2", rut="22222222", dv="2", nombre="Beto"),
                 fila_nomina(pat="AAAA11", nro_parte="3", rut="11111111", dv="1", nombre="Ana")]
        datos = {"nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION", "nombre_firmante": "X",
                 "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA}
        self.client.post("/subir/", datos, follow=True)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.total_patentes, 2)
        texto = self._texto_de_las_cartas(corrida)
        self.assertEqual(texto.count("placa patente AAAA11-1"), 2)
        self.assertEqual(texto.count("NOMBRE: Ana"), 1)
        self.assertEqual(texto.count("NOMBRE: Beto"), 1)
        archivo = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.SEGUIMIENTO)
        resp = self.client.get(f"/descargar/{archivo.pk}/")
        import pandas as pd
        seg = pd.read_excel(io.BytesIO(b"".join(resp.streaming_content)), dtype=str)
        self.assertEqual(set(seg["Patente"]), {"AAAA11"})
        self.assertEqual(seg.groupby("N° de Carta")["RUT"].nunique().max(), 1)
        self.assertEqual(seg["N° de Carta"].nunique(), 2)

    def test_el_formulario_exige_reg_y_fechas(self):
        resp = self._subir("CITACION", fechas="", reg="")
        self.assertContains(resp, "Escribe el Reg N° que se imprime en las cartas.")
        self.assertContains(resp, "Escribe las fechas de audiencia que fijó el Juzgado")
        self.assertFalse(GeneracionCartas.objects.exists())

    def test_el_formulario_rechaza_fechas_malas(self):
        for texto, mensaje in (
            ("14-12-2026", "no se entiende"),
            ("01/01/2020", "ya pasó"),
            ("31/02/2027", "no es una fecha válida"),
            (f"2000-1000 {self._futura()}", "mayor que el final"),
            (f"1000-2000 {self._futura()}\n1500-2500 {self._futura()}", "se solapan"),
            (f"{self._futura()}\n1000-2000 {self._futura()}", "no ambos"),
        ):
            with self.subTest(texto=texto):
                resp = self._subir("CITACION", fechas=texto)
                self.assertContains(resp, mensaje)
        self.assertFalse(GeneracionCartas.objects.exists())

    def test_la_sentencia_no_pide_fechas_de_audiencia_pero_si_reg(self):
        datos = {
            "nomina": construir_nomina_xlsx([fila_nomina()]), "tipo_carta": "SENTENCIA",
            "fecha_audiencia": self._futura(), "nombre_firmante": "X", "cargo_firma": "SECRETARIA",
            "n_cartas_paquete": 50, "reg": "R-9",
        }
        resp = self.client.post("/subir/", datos, follow=True)
        self.assertEqual(GeneracionCartas.objects.get().estado, GeneracionCartas.Estado.OK)
        self.assertNotContains(resp, "Revisa la fecha de citación")

    # ── FECHA CITACIÓN de la nómina: respaldo ────────────────────────────

    def test_fecha_en_el_pasado_de_la_nomina_no_se_imprime_y_avisa(self):
        resp = self._subir("CITACION", fechas=f"1-2 {self._futura()}", citaciones=["24/08/2026", "24/08/2026"])
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertNotIn("24/08/2026", texto)
        self.assertIn("en fecha y hora a coordinar con el Juzgado", texto)
        self.assertContains(resp, "fecha de citación anterior a hoy")

    def test_fecha_con_hora_sin_segundos_y_celda_datetime_de_la_nomina(self):
        fut = date.today() + timedelta(days=20)
        resp = self._subir("CITACION", fechas=f"1-2 {self._futura()}",
                           citaciones=[f"{fut:%d/%m/%Y} 09:30", datetime(fut.year, fut.month, fut.day)])
        self.assertEqual(self._texto_de_las_cartas(GeneracionCartas.objects.get()).count(f"el día {fut:%d/%m/%Y}"), 2)
        self.assertNotContains(resp, "fecha de citación anterior a hoy")

    def test_nomina_con_la_columna_vacia_no_dice_que_la_tomo_de_la_nomina(self):
        resp = self._subir("CITACION", fechas=f"1-2 {self._futura()}", citaciones=["", ""])
        self.assertContains(resp, "sin fecha de citación legible")

    # ── el resto de la pantalla ──────────────────────────────────────────

    def test_la_sentencia_no_muestra_avisos_de_citacion(self):
        datos = {
            "nomina": construir_nomina_xlsx([fila_nomina()]), "tipo_carta": "SENTENCIA", "reg": "R-1",
            "fecha_audiencia": self._futura(), "nombre_firmante": "X", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        }
        self.assertNotContains(self.client.post("/subir/", datos, follow=True), "Revisa la fecha de citación")

    @override_settings(JPLTAG_AGENDA_ACTIVA=True)
    def test_agenda_activa_avisa_que_ignora_las_fechas_de_audiencia(self):
        resp = self._subir("CITACION", fechas=self._futura())
        self.assertContains(resp, "se ignoraron las fechas de audiencia")

    def test_el_log_no_anota_a_todas_como_sin_cupo(self):
        self._subir("CITACION")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.total_sin_cupo_agenda, 0)
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.LOG_ERRORES).exists())

    def test_las_pantallas_lo_dicen(self):
        subir = self.client.get("/subir/")
        self.assertContains(subir, "Por ahora las cartas no usan la agenda")
        self.assertContains(subir, '<span class="chip">Fecha del Juzgado</span>')
        self.assertContains(subir, "Fechas de audiencia")
        self.assertContains(subir, "Reg N°")
        self.assertNotContains(subir, "Usa la agenda")
        agenda = self.client.get("/agenda/?juzgado=jpl1")
        self.assertEqual(agenda.status_code, 200)
        self.assertContains(agenda, "las citaciones salen con la fecha que fija el Juzgado")

    def test_el_panel_lo_dice(self):
        self.assertContains(self.client.get("/"), "Las cartas todavía no usan la agenda")

    def test_sin_rol_en_la_nomina_el_resultado_lo_avisa(self):
        resp = self._subir("CITACION", roles=("", ""))
        self.assertContains(resp, "2 filas de la nómina no traen ROL")

    @override_settings(JPLTAG_AGENDA_ACTIVA=True)
    def test_con_la_agenda_activa_vuelve_a_tomar_cupos(self):
        self.assertContains(self.client.get("/subir/"), "Usa la agenda")
        self._subir("CITACION")
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 2)
