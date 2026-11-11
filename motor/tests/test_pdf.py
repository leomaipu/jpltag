"""Conversión de un paquete de cartas a PDF (motor.pdf)."""

import os
import stat
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from motor import pdf
from motor.plantilla import generar_cartas_certificadas
from motor.tests.test_plantillas_tipos import _config, _info

HAY_LIBREOFFICE = pdf.conversion_disponible()


def _un_paquete(carpeta, cartas=3, causas=2):
    grupos = {f"AAAA{i:02d}": _info(tuple([1] * causas)) for i in range(cartas)}
    slots = {p: ("15/11/2026", "09:30") for p in grupos}
    resultado = generar_cartas_certificadas(grupos, "jpl1", slots, 0, _config(1, "CITACION"), carpeta_salida=carpeta)
    return resultado["archivos"][0]


def _programa_falso(carpeta, cuerpo):
    ruta = Path(carpeta, "soffice-falso")
    ruta.write_text("#!/bin/sh\n" + cuerpo + "\n")
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC)
    return str(ruta)


class SinLibreOfficeTest(unittest.TestCase):
    def test_sin_el_programa_no_esta_disponible_y_convertir_lo_explica(self):
        self.assertFalse(pdf.conversion_disponible("soffice-que-no-existe"))
        with tempfile.TemporaryDirectory() as carpeta:
            docx = _un_paquete(carpeta)
            with self.assertRaisesRegex(pdf.ErrorPdf, "no está instalado"):
                pdf.convertir_a_pdf(docx, binario="soffice-que-no-existe")
            self.assertFalse(Path(pdf.ruta_pdf_de(docx)).exists())

    def test_si_el_programa_falla_no_queda_ningun_pdf_ni_temporal(self):
        with tempfile.TemporaryDirectory() as carpeta:
            docx = _un_paquete(carpeta)
            falso = _programa_falso(carpeta, "echo 'no pude' >&2; exit 3")
            with self.assertRaisesRegex(pdf.ErrorPdf, r"código 3.*no pude"):
                pdf.convertir_a_pdf(docx, binario=falso)
            self.assertEqual([p.name for p in Path(carpeta).glob("*.pdf*")], [])

    def test_si_el_programa_no_termina_se_corta(self):
        with tempfile.TemporaryDirectory() as carpeta:
            docx = _un_paquete(carpeta)
            falso = _programa_falso(carpeta, "sleep 30")
            with self.assertRaisesRegex(pdf.ErrorPdf, "no terminó en 1 s"):
                pdf.convertir_a_pdf(docx, binario=falso, tiempo_maximo=1)

    def test_al_cortar_una_conversion_colgada_no_queda_ningun_proceso_suyo(self):
        """LibreOffice se lanza a través de un proceso intermedio: hay que
        cortar también al que queda detrás."""
        with tempfile.TemporaryDirectory() as carpeta:
            docx = _un_paquete(carpeta)
            ruta_pid = Path(carpeta, "nieto.pid")
            falso = _programa_falso(carpeta, f"sleep 60 &\necho $! > {ruta_pid}\nwait")
            with self.assertRaisesRegex(pdf.ErrorPdf, "no terminó en 1 s"):
                pdf.convertir_a_pdf(docx, binario=falso, tiempo_maximo=1)
            nieto = int(ruta_pid.read_text())
            limite = time.monotonic() + 5
            while Path(f"/proc/{nieto}").exists() and time.monotonic() < limite:
                time.sleep(0.05)
            self.assertFalse(Path(f"/proc/{nieto}").exists(), "quedó vivo el proceso que convertía")
            self.assertFalse(Path(pdf.carpeta_de_trabajo(docx)).exists())

    def test_si_falla_no_queda_un_pdf_de_una_conversion_anterior(self):
        with tempfile.TemporaryDirectory() as carpeta:
            docx = _un_paquete(carpeta)
            Path(pdf.ruta_pdf_de(docx)).write_bytes(b"%PDF-1.7 de antes\n%%EOF")
            with self.assertRaises(pdf.ErrorPdf):
                pdf.convertir_a_pdf(docx, binario=_programa_falso(carpeta, "exit 1"))
            self.assertFalse(Path(pdf.ruta_pdf_de(docx)).exists())

    def test_un_programa_que_no_se_puede_ejecutar_es_un_error_de_pdf(self):
        with tempfile.TemporaryDirectory() as carpeta:
            docx = _un_paquete(carpeta)
            no_ejecutable = Path(carpeta, "soffice-sin-permiso")
            no_ejecutable.write_text("#!/bin/sh\n")
            with self.assertRaisesRegex(pdf.ErrorPdf, "no se pudo escribir el PDF|no está instalado"):
                pdf.convertir_a_pdf(docx, binario=str(no_ejecutable))

    def test_un_pdf_a_medio_escribir_no_cuenta_como_pdf(self):
        with tempfile.TemporaryDirectory() as carpeta:
            truncado = Path(carpeta, "a.pdf")
            truncado.write_bytes(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog")
            self.assertFalse(pdf.es_pdf(truncado))
            self.assertFalse(pdf.es_pdf(Path(carpeta, "no-existe.pdf")))
            otro = Path(carpeta, "b.pdf")
            otro.write_bytes(b"PK\x03\x04 esto es un zip")
            self.assertFalse(pdf.es_pdf(otro))


@unittest.skipUnless(HAY_LIBREOFFICE, "LibreOffice no está instalado")
class ConLibreOfficeTest(unittest.TestCase):
    def test_el_pdf_tiene_una_pagina_por_carta_y_el_mismo_texto(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as carpeta:
            docx = _un_paquete(carpeta, cartas=4)
            ruta = pdf.convertir_a_pdf(docx)
            self.assertEqual(ruta, str(Path(docx).with_suffix(".pdf")))
            self.assertTrue(pdf.es_pdf(ruta))
            lector = PdfReader(ruta)
            self.assertEqual(len(lector.pages), 4)
            for i, pagina in enumerate(lector.pages):
                texto = pagina.extract_text()
                self.assertIn(f"AAAA{i:02d}", texto)
                self.assertIn("MARIA PRUEBA SINTETICA", texto)
            self.assertEqual([p.name for p in Path(carpeta).glob("*.tmp")], [])

    def test_el_pdf_se_abre_e_imprime_sin_clave_y_declara_que_no_admite_cambios(self):
        """Lo que se comprueba son los permisos que el PDF declara (los que
        respetan los lectores); no es una protección contra quien los ignore."""
        from pypdf import PdfReader
        from pypdf.constants import UserAccessPermissions as Permiso

        with tempfile.TemporaryDirectory() as carpeta:
            lector = PdfReader(pdf.convertir_a_pdf(_un_paquete(carpeta)))
            self.assertTrue(lector.is_encrypted)
            lector.pages[0].extract_text()     # se abre sin clave
            permisos = lector.user_access_permissions
            self.assertTrue(permisos & Permiso.PRINT)
            self.assertTrue(permisos & Permiso.PRINT_TO_REPRESENTATION)
            for prohibido in (Permiso.MODIFY, Permiso.ADD_OR_MODIFY, Permiso.FILL_FORM_FIELDS,
                              Permiso.ASSEMBLE_DOC):
                self.assertFalse(permisos & prohibido, prohibido)

    def test_cada_pdf_lleva_una_clave_de_permisos_distinta(self):
        self.assertNotEqual(pdf._filtro_de_exportacion(), pdf._filtro_de_exportacion())

    def test_varias_conversiones_a_la_vez_no_se_estorban(self):
        with tempfile.TemporaryDirectory() as raiz:
            documentos = []
            for i in range(4):
                carpeta = Path(raiz, str(i))
                carpeta.mkdir()
                documentos.append(_un_paquete(str(carpeta), cartas=2))
            with ThreadPoolExecutor(max_workers=4) as hilos:
                rutas = list(hilos.map(pdf.convertir_a_pdf, documentos))
            self.assertTrue(all(pdf.es_pdf(r) for r in rutas))

    def test_no_deja_nada_en_el_directorio_temporal_ni_junto_al_word(self):
        with tempfile.TemporaryDirectory() as carpeta, tempfile.TemporaryDirectory() as temporales:
            docx = _un_paquete(carpeta)
            with mock.patch.dict(os.environ, {"TMPDIR": temporales}):
                tempfile.tempdir = None
                try:
                    pdf.convertir_a_pdf(docx)
                finally:
                    tempfile.tempdir = None
            self.assertEqual(os.listdir(temporales), [])
            self.assertEqual(sorted(p.suffix for p in Path(carpeta).iterdir()), [".docx", ".pdf", ".xlsx"])

    def test_las_cartas_de_los_tres_juzgados_y_tipos_caben_igual_en_pdf(self):
        """El PDF no debe partir una carta en dos hojas: mismas páginas que cartas."""
        from pypdf import PdfReader

        for n in (1, 2, 3):
            for tipo in ("CITACION", "CITACION_SENTENCIA", "SENTENCIA"):
                with self.subTest(juzgado=n, tipo=tipo), tempfile.TemporaryDirectory() as carpeta:
                    grupos = {f"AAAA{i:02d}": _info(tuple([1] * 12)) for i in range(3)}
                    slots = {p: ("15/11/2026", "09:30") for p in grupos}
                    resultado = generar_cartas_certificadas(
                        grupos, f"jpl{n}", slots, 0, _config(n, tipo, max_causas_por_pagina=12),
                        carpeta_salida=carpeta, convertir_a_pdf=pdf.convertir_a_pdf,
                    )
                    self.assertEqual(resultado["lotes_sin_pdf"], [])
                    (ruta,) = resultado["archivos_pdf"]
                    self.assertEqual(len(PdfReader(ruta).pages), 3)


if __name__ == "__main__":
    unittest.main()
