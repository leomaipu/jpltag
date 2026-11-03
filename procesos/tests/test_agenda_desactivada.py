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

    def test_el_reg_sube_de_1_en_1_en_cada_carta_y_coincide_con_el_seguimiento(self):
        resp = self._subir("CITACION", reg="R-4455")
        corrida = GeneracionCartas.objects.get()
        texto = self._texto_de_las_cartas(corrida)
        self.assertEqual(texto.count("Reg N° R-4455"), 1)
        self.assertEqual(texto.count("Reg N° R-4456"), 1)
        self.assertNotIn("«folio carta trámite»", texto)
        self.assertContains(resp, "R-4455 a R-4456")
        archivo = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.SEGUIMIENTO)
        import pandas as pd
        seg = pd.read_excel(io.BytesIO(b"".join(self.client.get(f"/descargar/{archivo.pk}/").streaming_content)), dtype=str)
        self.assertEqual(dict(zip(seg["N° de Carta"], seg["Reg N°"])), {"1": "R-4455", "2": "R-4456"})

    def test_las_paginas_de_una_carta_comparten_el_reg(self):
        filas = [fila_nomina(pat="AAAA11", nro_parte=str(100 + i), rol=f"2026-Y-{100 + i}") for i in range(14)]
        filas.append(fila_nomina(pat="BBBB22", nro_parte="999", rol="2026-Y-999"))
        datos = {"nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION", "nombre_firmante": "X",
                 "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA, "reg": "500"}
        self.client.post("/subir/", datos, follow=True)
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertEqual(texto.count("Reg N° 500"), 2)     # AAAA11: 2 páginas (14 causas, 12 por página)
        self.assertEqual(texto.count("Reg N° 501"), 1)
        self.assertNotIn("Reg N° 502", texto)

    def test_el_reg_debe_terminar_en_numero(self):
        resp = self._subir("CITACION", reg="ABC")
        self.assertContains(resp, "El Reg N° debe terminar en un número")
        self.assertFalse(GeneracionCartas.objects.exists())

    # ── reparto por tandas (el Juzgado divide en 200 o 300 personas) ─────

    def _subir_n(self, n, **extra):
        filas = [fila_nomina(pat=f"PAT{i:03d}", nro_parte=str(7000 + i), rol=f"2026-Y-{7000 + i}") for i in range(n)]
        datos = {"nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION", "nombre_firmante": "X",
                 "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA, **extra}
        return self.client.post("/subir/", datos, follow=True)

    def test_reparto_por_tandas(self):
        a, b, c = self._futura(60), self._futura(61), self._futura(62)
        resp = self._subir_n(5, fechas_audiencia=f"{a}\n{b}\n{c}", cartas_por_fecha=2)
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertEqual((texto.count(f"el día {a},"), texto.count(f"el día {b},"), texto.count(f"el día {c},")), (2, 2, 1))
        self.assertContains(resp, f"{a} (2 cartas); {b} (2 cartas); {c} (1 carta)")

    def test_la_tanda_sigue_el_orden_de_las_cartas(self):
        a, b = self._futura(60), self._futura(61)
        self._subir_n(4, fechas_audiencia=f"{a}\n{b}", cartas_por_fecha=2)
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        # Las dos primeras patentes (PAT000, PAT001) van a la primera fecha.
        for pat, fecha in (("PAT000", a), ("PAT001", a), ("PAT002", b), ("PAT003", b)):
            bloque = texto[texto.index(f"placa patente {pat}"):][:400]
            self.assertIn(f"el día {fecha},", bloque, pat)

    def test_varias_fechas_sin_cartas_por_fecha_sugiere_completarlo(self):
        resp = self._subir_n(2, fechas_audiencia=f"{self._futura(60)}\n{self._futura(61)}")
        self.assertContains(resp, "completa «Cartas por fecha»")

    def test_las_fechas_insuficientes_se_rechazan_antes_de_preguntar_por_repetidas(self):
        self._subir_n(2, fechas_audiencia=self._futura(60))
        resp = self._subir_n(5, fechas_audiencia=f"{self._futura(60)}\n{self._futura(61)}", cartas_por_fecha=2)
        self.assertContains(resp, "se necesitan 3 fechas de audiencia")
        self.assertNotContains(resp, "Continuar")
        self.assertEqual(GeneracionCartas.objects.count(), 1)

    def test_si_las_fechas_no_alcanzan_no_se_genera_y_se_explica(self):
        resp = self._subir_n(5, fechas_audiencia=f"{self._futura(60)}\n{self._futura(61)}", cartas_por_fecha=2)
        self.assertContains(resp, "Son 5 cartas y con 2 cartas por fecha se necesitan 3 fechas de audiencia; escribiste 2")
        self.assertFalse(GeneracionCartas.objects.exists())
        # Los campos escritos se conservan para corregir (solo hay que volver a elegir el Excel).
        self.assertContains(resp, self._futura(60))

    def test_cartas_por_fecha_exige_solo_fechas_sueltas(self):
        resp = self._subir_n(2, fechas_audiencia=f"1-5 {self._futura(60)}", cartas_por_fecha=2)
        self.assertContains(resp, "no es una fecha")
        self.assertFalse(GeneracionCartas.objects.exists())

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
                 "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA, "reg": "A&B <1"}
        self.client.post("/subir/", datos, follow=True)
        texto = self._texto_de_las_cartas(GeneracionCartas.objects.get())
        self.assertIn("TRANSPORTES J&J <SPA>", texto)
        self.assertIn("Reg N° A&B <1", texto)

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
