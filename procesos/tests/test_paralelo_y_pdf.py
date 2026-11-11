"""En la app: paquetes armados en paralelo (JPLTAG_PAQUETES_EN_PARALELO) y
la opción «También en PDF»."""

import json
from pathlib import Path
from unittest import mock

import pandas as pd
from django.test import override_settings

from motor import pdf
from motor.ejecucion_lotes import EjecutorEnProcesos
from nucleo.models import Rol
from procesos import generacion
from procesos.models import ArchivoGenerado, GeneracionCartas

from .fixtures import crear_juzgado, crear_usuario, ingresar
from .test_generacion import _TMP_SALIDAS, _Base

HAY_LIBREOFFICE = pdf.conversion_disponible()
PATENTES = ("AAAA11", "BBBB22", "CCCC33", "DDDD44", "EEEE55")


def _con_pdf_ofrecido(disponible=True):
    """La casilla se ofrece según lo que diga el servidor."""
    return mock.patch("procesos.forms.pdf_disponible", return_value=disponible)


@override_settings(JPLTAG_GENERAR_EN_LA_PETICION=False, JPLTAG_AGENDA_ACTIVA=False)
class PaquetesEnParaleloTest(_Base):
    def test_el_ejecutor_sale_de_la_configuracion(self):
        with override_settings(JPLTAG_PAQUETES_EN_PARALELO=6):
            ejecutor = generacion._ejecutor_de_lotes()
        self.assertIsInstance(ejecutor, EjecutorEnProcesos)
        self.assertEqual(ejecutor.paralelos, 6)
        with override_settings(JPLTAG_PAQUETES_EN_PARALELO=0):     # un valor absurdo no rompe: uno a la vez
            self.assertEqual(generacion._ejecutor_de_lotes().paralelos, 1)

    def test_el_plazo_de_un_paquete_suma_el_de_la_conversion_si_hay_pdf(self):
        from motor.pdf import TIEMPO_MAXIMO_CONVERSION

        with override_settings(JPLTAG_SEGUNDOS_POR_PAQUETE=240):
            self.assertEqual(generacion._ejecutor_de_lotes().tiempo_maximo, 240)
            self.assertGreater(generacion._ejecutor_de_lotes(con_pdf=True).tiempo_maximo, 240 + TIEMPO_MAXIMO_CONVERSION)

    @override_settings(JPLTAG_PAQUETES_EN_PARALELO=3)
    def test_en_paralelo_sale_lo_mismo_que_de_a_uno(self):
        self.subir(PATENTES, n_cartas_paquete=1)
        generacion.procesar_siguiente("w1")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual((corrida.lotes_hechos, corrida.lotes_total), (5, 5))
        cartas = corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).order_by("pk")
        self.assertEqual([a.nombre_descarga for a in cartas],
                         [f"jpl1_Cartas_Certificadas_Lote_{i}.docx" for i in range(1, 6)])
        seguimiento = pd.read_excel(Path(_TMP_SALIDAS) / corrida.archivos.get(tipo="SEGUIMIENTO").ruta)
        self.assertEqual(list(seguimiento["N° de Carta"]), [1, 2, 3, 4, 5])
        self.assertEqual(list(seguimiento["Patente"]), list(PATENTES))
        self.assertEqual(list(seguimiento["Reg N°"]), [f"R-{i}" for i in range(1, 6)])

    @override_settings(JPLTAG_PAQUETES_EN_PARALELO=2)
    def test_al_detenerse_en_paralelo_vuelve_a_la_cola_y_se_retoma_sin_repetir_cartas(self):
        self.subir(PATENTES, n_cartas_paquete=1)

        def detener():
            raise generacion.Detenido()

        generacion.procesar_siguiente("w1", al_latir=detener)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.PENDIENTE)
        carpeta = Path(_TMP_SALIDAS) / corrida.carpeta_salida / "cartas"
        hechos = {p.name: p.stat().st_mtime_ns for p in carpeta.glob("*.docx")}
        self.assertTrue(1 <= len(hechos) < 5)
        self.assertEqual(list(carpeta.glob("*.tmp")), [])

        generacion.procesar_siguiente("w2")
        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual((corrida.lotes_hechos, corrida.lotes_total), (5, 5))
        for nombre, mtime in hechos.items():
            self.assertEqual((carpeta / nombre).stat().st_mtime_ns, mtime)
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).count(), 5)
        seguimiento = pd.read_excel(Path(_TMP_SALIDAS) / corrida.archivos.get(tipo="SEGUIMIENTO").ruta)
        self.assertEqual(sorted(seguimiento["N° de Carta"]), [1, 2, 3, 4, 5])

    @override_settings(JPLTAG_PAQUETES_EN_PARALELO=3)
    def test_si_la_cancelan_mientras_genera_en_paralelo_no_queda_nada(self):
        from datetime import timedelta

        from django.utils import timezone

        self.subir(PATENTES, n_cartas_paquete=1)

        def cancelar():
            GeneracionCartas.objects.update(actualizado=timezone.now() - timedelta(minutes=30))
            generacion.cancelar(GeneracionCartas.objects.get().pk, self.j1, "prueba")

        with self.assertLogs("procesos.generacion", level="WARNING"):
            generacion.procesar_siguiente("w1", al_latir=cancelar)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.FALLIDO)
        self.assertFalse(corrida.archivos.exists())
        self.assertFalse((Path(_TMP_SALIDAS) / corrida.carpeta_salida / "cartas").exists())

    def test_una_corrida_que_estaba_en_cola_antes_del_cambio_se_genera_igual(self):
        """Su trabajo.json no trae «con_pdf»."""
        self.subir(("AAAA11",))
        corrida = GeneracionCartas.objects.get()
        ruta = Path(_TMP_SALIDAS) / corrida.carpeta_salida / generacion.TRABAJO
        trabajo = json.loads(ruta.read_text(encoding="utf-8"))
        del trabajo["con_pdf"]
        ruta.write_text(json.dumps(trabajo), encoding="utf-8")
        generacion.procesar_siguiente("w1")
        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).exists())


@override_settings(JPLTAG_AGENDA_ACTIVA=False)
class OpcionPdfTest(_Base):
    def test_la_casilla_se_ofrece_solo_si_el_servidor_puede_convertir(self):
        with _con_pdf_ofrecido(True):
            self.assertContains(self.client.get("/subir/"), 'name="con_pdf"')
        with _con_pdf_ofrecido(False):
            self.assertNotContains(self.client.get("/subir/"), 'name="con_pdf"')

    def test_esta_apagada_hasta_que_el_servidor_la_activa(self):
        with mock.patch("procesos.generacion.conversion_disponible", return_value=True):
            self.assertFalse(generacion.pdf_disponible())
            with override_settings(JPLTAG_PDF_ACTIVO=True):
                self.assertTrue(generacion.pdf_disponible())
        with override_settings(JPLTAG_PDF_ACTIVO=True, JPLTAG_SOFFICE="soffice-que-no-existe"):
            self.assertFalse(generacion.pdf_disponible())

    def test_sin_marcarla_no_se_generan_pdf(self):
        with _con_pdf_ofrecido(True):
            self.subir(("AAAA11",))
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).exists())
        self.assertNotContains(self.client.get(f"/resultado/{corrida.pk}/"), ".pdf")

    def test_si_el_servidor_no_puede_convertir_se_ignora_aunque_la_envien(self):
        with _con_pdf_ofrecido(False):
            self.subir(("AAAA11",), con_pdf="on")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertNotIn("lotes_sin_pdf", corrida.avisos)
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).exists())

    @override_settings(JPLTAG_SOFFICE="soffice-que-no-existe")
    def test_si_la_conversion_falla_las_cartas_en_word_salen_y_se_avisa(self):
        with _con_pdf_ofrecido(True), mock.patch("procesos.generacion.pdf_disponible", return_value=True), \
                self.assertLogs("motor.plantilla", level="ERROR"):
            resp = self.subir(("AAAA11", "BBBB22"), n_cartas_paquete=1, con_pdf="on")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual(corrida.avisos["lotes_sin_pdf"], [1, 2])
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).count(), 2)
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).exists())
        self.assertContains(resp, "Paquetes sin PDF")
        self.assertContains(resp, "estos paquetes: 1, 2")

    @override_settings(JPLTAG_GENERAR_EN_LA_PETICION=False)
    def test_si_el_pdf_se_apaga_con_la_corrida_en_cola_sale_solo_en_word_y_se_avisa(self):
        with _con_pdf_ofrecido(True):
            self.subir(("AAAA11",), con_pdf="on")
        convertidos = []
        with override_settings(JPLTAG_PDF_ACTIVO=False), \
                mock.patch("motor.pdf.convertir_a_pdf", side_effect=convertidos.append):
            generacion.procesar_siguiente("w1")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual(convertidos, [])
        self.assertTrue(corrida.avisos["pdf_apagado"])
        self.assertNotIn("lotes_sin_pdf", corrida.avisos)
        self.assertContains(self.client.get(f"/resultado/{corrida.pk}/"), "salieron solo en Word")

    def test_la_eleccion_se_conserva_al_preguntar_por_patentes_repetidas(self):
        with _con_pdf_ofrecido(False):
            self.subir(("AAAA11",))
        with _con_pdf_ofrecido(True):
            resp = self.subir(("AAAA11", "BBBB22"), con_pdf="on")
            self.assertContains(resp, 'name="repetidas"')
            self.assertContains(resp, '<input type="hidden" name="con_pdf" value="on">')
            resp = self.subir(("AAAA11", "BBBB22"))
            self.assertNotContains(resp, 'name="con_pdf" value="on"')


@override_settings(JPLTAG_AGENDA_ACTIVA=False, JPLTAG_PDF_ACTIVO=True)
class ConPdfDeVerdadTest(_Base):
    def setUp(self):
        if not HAY_LIBREOFFICE:
            self.skipTest("LibreOffice no está instalado")
        super().setUp()

    def _generar(self, **extra):
        resp = self.subir(("AAAA11", "BBBB22", "CCCC33"), n_cartas_paquete=2, con_pdf="on", **extra)
        return GeneracionCartas.objects.get(), resp

    def test_cada_paquete_queda_en_word_y_en_pdf(self):
        from pypdf import PdfReader

        corrida, _ = self._generar()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertNotIn("lotes_sin_pdf", corrida.avisos)
        pdfs = list(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).order_by("pk"))
        self.assertEqual([a.nombre_descarga for a in pdfs],
                         ["jpl1_Cartas_Certificadas_Lote_1.pdf", "jpl1_Cartas_Certificadas_Lote_2.pdf"])
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).count(), 2)
        self.assertEqual([len(PdfReader(Path(_TMP_SALIDAS) / a.ruta).pages) for a in pdfs], [2, 1])
        texto = PdfReader(Path(_TMP_SALIDAS) / pdfs[1].ruta).pages[0].extract_text()
        self.assertIn("CCCC33", texto)
        self.assertIn("R-3", texto)

    def test_el_resultado_muestra_el_pdf_junto_al_word_y_se_descarga(self):
        corrida, resp = self._generar()
        self.assertContains(resp, "los lectores de PDF no dejan editarlo")
        self.assertNotContains(resp, "no se puede modificar")
        self.assertContains(resp, "jpl1_Cartas_Certificadas_Lote_1.docx")
        self.assertNotContains(resp, "jpl1_Cartas_Certificadas_Lote_1.pdf")   # no hay fila aparte
        self.assertEqual(resp.content.decode().count(">.pdf</a>"), 2)
        un_pdf = corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).first()
        self.assertContains(resp, f'href="/descargar/{un_pdf.pk}/"')
        descarga = self.client.get(f"/descargar/{un_pdf.pk}/")
        self.assertEqual(descarga.status_code, 200)
        self.assertEqual(descarga["Content-Type"], "application/pdf")
        self.assertIn(un_pdf.nombre_descarga, descarga["Content-Disposition"])
        self.assertTrue(b"".join(descarga.streaming_content).startswith(b"%PDF-"))

    def test_el_pdf_de_otro_juzgado_no_se_descarga(self):
        corrida, _ = self._generar()
        un_pdf = corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).first()
        j2 = crear_juzgado(slug="jpl2", nombre="2do JPL", dia_desde=11, dia_hasta=20)
        crear_usuario("sec2", rol=Rol.SECRETARIO, juzgado=j2)
        self.client.logout()
        ingresar(self.client, "sec2")
        self.assertEqual(self.client.get(f"/descargar/{un_pdf.pk}/").status_code, 403)

    @override_settings(JPLTAG_GENERAR_EN_LA_PETICION=False, JPLTAG_PAQUETES_EN_PARALELO=2)
    def test_con_pdf_y_en_paralelo_en_el_worker(self):
        self.subir(PATENTES, n_cartas_paquete=1, con_pdf="on")
        generacion.procesar_siguiente("w1")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS_PDF).count(), 5)
        carpeta = Path(_TMP_SALIDAS) / corrida.carpeta_salida / "cartas"
        self.assertEqual(sorted(p.suffix for p in carpeta.iterdir() if p.suffix != ".xlsx"), [".docx"] * 5 + [".pdf"] * 5)

    def test_una_corrida_fallida_no_deja_pdf(self):
        from datetime import timedelta

        from django.utils import timezone

        with override_settings(JPLTAG_GENERAR_EN_LA_PETICION=False):
            self.subir(("AAAA11", "BBBB22"), n_cartas_paquete=1, con_pdf="on")

            def cancelar():
                GeneracionCartas.objects.update(actualizado=timezone.now() - timedelta(minutes=30))
                generacion.cancelar(GeneracionCartas.objects.get().pk, self.j1, "prueba")

            with self.assertLogs("procesos.generacion", level="WARNING"):
                generacion.procesar_siguiente("w1", al_latir=cancelar)
        corrida = GeneracionCartas.objects.get()
        self.assertFalse((Path(_TMP_SALIDAS) / corrida.carpeta_salida / "cartas").exists())
