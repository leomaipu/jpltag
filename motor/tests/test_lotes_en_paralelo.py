"""generar_cartas_certificadas con paquetes en paralelo y con PDF: el
resultado no depende de cuántos se armen a la vez ni del orden en que
terminen, y una interrupción se puede retomar."""

import functools
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from motor import pdf, plantilla
from motor.ejecucion_lotes import EjecutorEnProcesos, EjecutorEnSerie, Resultado
from motor.plantilla import generar_cartas_certificadas
from motor.plantilla import texto_de
from motor.tests.test_plantillas_tipos import _config, _info

HAY_LIBREOFFICE = pdf.conversion_disponible()


def _grupos(n):
    return {f"AAAA{i:02d}": _info((1, 1)) for i in range(n)}


def _slots(grupos):
    return {p: ("15/11/2026", "09:30") for p in grupos}


def _generar(carpeta, n=7, por_lote=2, **extra):
    grupos = _grupos(n)
    config = _config(1, "CITACION", **extra.pop("config", {}))
    return generar_cartas_certificadas(grupos, "jpl1", _slots(grupos), por_lote, config, carpeta_salida=carpeta, **extra)


def _textos(resultado):
    from docx import Document
    return [texto_de(Document(ruta)) for ruta in resultado["archivos"]]


class EjecutorAlReves:
    """Entrega los resultados en orden inverso al de los lotes."""

    def ejecutar(self, tareas):
        yield from reversed(list(EjecutorEnSerie().ejecutar(tareas)))


class ParaleloIgualQueEnSerieTest(unittest.TestCase):
    def test_mismas_cartas_misma_numeracion_y_mismo_seguimiento(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            serie = _generar(a)
            paralelo = _generar(b, ejecutor=EjecutorEnProcesos(paralelos=3))
            self.assertEqual([Path(r).name for r in serie["archivos"]], [Path(r).name for r in paralelo["archivos"]])
            self.assertEqual(_textos(serie), _textos(paralelo))
            pd.testing.assert_frame_equal(pd.read_excel(serie["seguimiento"]), pd.read_excel(paralelo["seguimiento"]))
            self.assertEqual(paralelo["errores"], [])

    def test_el_resultado_va_en_orden_de_lote_aunque_terminen_en_otro_orden(self):
        with tempfile.TemporaryDirectory() as carpeta:
            avance = []
            resultado = _generar(carpeta, ejecutor=EjecutorAlReves(), al_terminar_lote=avance.append)
            self.assertEqual([Path(r).name for r in resultado["archivos"]],
                             [f"jpl1_Cartas_Certificadas_Lote_{i}.docx" for i in (1, 2, 3, 4)])
            seguimiento = pd.read_excel(resultado["seguimiento"])
            self.assertEqual(list(seguimiento["Lote"]), sorted(seguimiento["Lote"]))
            self.assertEqual(sorted(set(seguimiento["N° de Carta"])), list(range(1, 8)))
            self.assertEqual(avance, [1, 2, 3, 4])    # cuenta de lotes terminados, no su número

    def test_un_lote_que_falla_no_cambia_la_numeracion_de_los_demas(self):
        original = plantilla._armar_lote

        def falla_el_2(lote, num_lote, *resto):
            if num_lote == 2:
                raise RuntimeError("se cayó el lote 2")
            return original(lote, num_lote, *resto)

        with tempfile.TemporaryDirectory() as carpeta, mock.patch.object(plantilla, "_armar_lote", falla_el_2), \
                self.assertLogs("motor.plantilla", level="ERROR"):
            resultado = _generar(carpeta, ejecutor=EjecutorEnProcesos(paralelos=3))
            self.assertEqual(len(resultado["archivos"]), 3)
            self.assertEqual(resultado["patentes_fallidas"], ["AAAA02", "AAAA03"])
            (error,) = resultado["errores"]
            self.assertIn("El lote 2 (cartas 3 a 4)", error)
            seguimiento = pd.read_excel(resultado["seguimiento"])
            self.assertEqual(sorted(set(seguimiento["N° de Carta"])), [1, 2, 5, 6, 7])


class InterrumpirYReanudarTest(unittest.TestCase):
    class _Alto(Exception):
        pass

    def test_si_el_aviso_de_avance_falla_se_detiene_y_se_retoma_solo_lo_que_falta(self):
        def detener(_):
            raise self._Alto()

        with tempfile.TemporaryDirectory() as carpeta:
            with self.assertRaises(self._Alto):
                _generar(carpeta, n=8, por_lote=1, ejecutor=EjecutorEnProcesos(paralelos=2), al_terminar_lote=detener)
            hechos = sorted(Path(carpeta).glob("*.docx"))
            self.assertGreaterEqual(len(hechos), 1)
            self.assertLess(len(hechos), 8)
            self.assertEqual(list(Path(carpeta).glob("*.tmp")), [])
            antes = {p.name: p.stat().st_mtime_ns for p in hechos}

            avance = []
            resultado = _generar(carpeta, n=8, por_lote=1, ejecutor=EjecutorEnProcesos(paralelos=2),
                                 reanudar=True, al_terminar_lote=avance.append)
            self.assertEqual(len(resultado["archivos"]), 8)
            self.assertEqual(resultado["errores"], [])
            for nombre, mtime in antes.items():     # los que ya estaban no se rehicieron
                self.assertEqual(Path(carpeta, nombre).stat().st_mtime_ns, mtime)
            self.assertEqual(avance, list(range(len(antes) + 1, 9)))
            self.assertEqual(sorted(set(pd.read_excel(resultado["seguimiento"])["N° de Carta"])), list(range(1, 9)))

    def test_sin_reanudar_se_arma_todo_de_nuevo(self):
        with tempfile.TemporaryDirectory() as carpeta:
            _generar(carpeta, n=2, por_lote=1)
            llamados = []
            original = plantilla._armar_lote
            with mock.patch.object(plantilla, "_armar_lote", side_effect=lambda *a: llamados.append(a[1]) or original(*a)):
                _generar(carpeta, n=2, por_lote=1)
            self.assertEqual(llamados, [1, 2])


class ConPdfTest(unittest.TestCase):
    def test_sin_pedirlo_no_hay_pdf(self):
        with tempfile.TemporaryDirectory() as carpeta:
            resultado = _generar(carpeta, n=2, por_lote=1)
            self.assertEqual((resultado["archivos_pdf"], resultado["lotes_sin_pdf"]), ([], []))
            self.assertEqual(list(Path(carpeta).glob("*.pdf")), [])

    def test_si_el_pdf_falla_el_word_queda_y_el_lote_se_informa(self):
        with tempfile.TemporaryDirectory() as carpeta, self.assertLogs("motor.plantilla", level="ERROR"):
            resultado = _generar(carpeta, n=3, por_lote=1, convertir_a_pdf=functools.partial(pdf.convertir_a_pdf, binario="soffice-que-no-existe"))
            self.assertEqual(len(resultado["archivos"]), 3)
            self.assertEqual(resultado["archivos_pdf"], [])
            self.assertEqual(resultado["lotes_sin_pdf"], [1, 2, 3])
            self.assertEqual(resultado["errores"], [])            # las cartas salieron
            self.assertEqual(resultado["patentes_fallidas"], [])

    def test_cualquier_falla_del_conversor_deja_el_lote_sin_pdf_pero_con_su_word(self):
        def sin_permiso(_ruta):
            raise PermissionError(13, "Permission denied")

        with tempfile.TemporaryDirectory() as carpeta, self.assertLogs("motor.plantilla", level="ERROR"):
            resultado = _generar(carpeta, n=2, por_lote=1, convertir_a_pdf=sin_permiso,
                                 ejecutor=EjecutorEnProcesos(paralelos=2))
            self.assertEqual((len(resultado["archivos"]), resultado["lotes_sin_pdf"]), (2, [1, 2]))
            self.assertEqual((resultado["errores"], resultado["patentes_fallidas"]), ([], []))

    def test_un_conversor_colgado_no_hace_fallar_el_lote_si_el_plazo_del_lote_lo_cubre(self):
        with tempfile.TemporaryDirectory() as carpeta, self.assertLogs("motor.plantilla", level="ERROR"):
            falso = Path(carpeta, "soffice-colgado")
            falso.write_text("#!/bin/sh\nsleep 60\n")
            falso.chmod(0o755)
            resultado = _generar(
                carpeta, n=2, por_lote=1, ejecutor=EjecutorEnProcesos(paralelos=2, tiempo_maximo=30),
                convertir_a_pdf=functools.partial(pdf.convertir_a_pdf, binario=str(falso), tiempo_maximo=1),
            )
            self.assertEqual((len(resultado["archivos"]), resultado["lotes_sin_pdf"], resultado["errores"]), (2, [1, 2], []))
            self.assertEqual(sorted(p.suffix for p in Path(carpeta).iterdir() if p.name != falso.name),
                             [".docx", ".docx", ".xlsx"])

    def test_un_lote_que_falla_no_deja_archivos_sin_registrar(self):
        def falla_tras_escribir(ruta):
            raise SystemExit("el proceso se cae después de guardar el Word")

        with tempfile.TemporaryDirectory() as carpeta, self.assertLogs("motor.plantilla", level="ERROR"):
            resultado = _generar(carpeta, n=2, por_lote=1, convertir_a_pdf=falla_tras_escribir,
                                 ejecutor=EjecutorEnProcesos(paralelos=2))
            self.assertEqual(len(resultado["errores"]), 2)
            self.assertEqual(list(Path(carpeta).glob("*.docx")) + list(Path(carpeta).glob("*.pdf")), [])

    @unittest.skipUnless(HAY_LIBREOFFICE, "LibreOffice no está instalado")
    def test_al_abandonar_a_mitad_de_una_conversion_no_queda_su_carpeta_de_trabajo(self):
        class Alto(Exception):
            pass

        def detener(_):
            raise Alto()

        with tempfile.TemporaryDirectory() as carpeta:
            with self.assertRaises(Alto):
                _generar(carpeta, n=6, por_lote=1, ejecutor=EjecutorEnProcesos(paralelos=3),
                         convertir_a_pdf=pdf.convertir_a_pdf, al_terminar_lote=detener)
            self.assertEqual([p.name for p in Path(carpeta).iterdir() if p.name.endswith(("-trabajo", ".tmp"))], [])

    @unittest.skipUnless(HAY_LIBREOFFICE, "LibreOffice no está instalado")
    def test_un_pdf_por_lote_en_paralelo(self):
        from pypdf import PdfReader

        with tempfile.TemporaryDirectory() as carpeta:
            resultado = _generar(carpeta, n=5, por_lote=2, convertir_a_pdf=pdf.convertir_a_pdf, ejecutor=EjecutorEnProcesos(paralelos=3))
            self.assertEqual([Path(r).name for r in resultado["archivos_pdf"]],
                             [f"jpl1_Cartas_Certificadas_Lote_{i}.pdf" for i in (1, 2, 3)])
            self.assertEqual([len(PdfReader(r).pages) for r in resultado["archivos_pdf"]], [2, 2, 1])
            self.assertIn("AAAA04", PdfReader(resultado["archivos_pdf"][2]).pages[0].extract_text())
            self.assertEqual(resultado["lotes_sin_pdf"], [])

    @unittest.skipUnless(HAY_LIBREOFFICE, "LibreOffice no está instalado")
    def test_al_reanudar_un_lote_con_word_pero_sin_pdf_solo_se_convierte(self):
        with tempfile.TemporaryDirectory() as carpeta:
            _generar(carpeta, n=2, por_lote=1, convertir_a_pdf=pdf.convertir_a_pdf)
            Path(carpeta, "jpl1_Cartas_Certificadas_Lote_2.pdf").write_bytes(b"%PDF-1.7 truncado")
            word_2 = Path(carpeta, "jpl1_Cartas_Certificadas_Lote_2.docx").stat().st_mtime_ns
            armados = []
            original = plantilla._armar_lote
            with mock.patch.object(plantilla, "_armar_lote", side_effect=lambda *a: armados.append(a[1]) or original(*a)):
                resultado = _generar(carpeta, n=2, por_lote=1, convertir_a_pdf=pdf.convertir_a_pdf, reanudar=True)
            self.assertEqual(armados, [])       # ningún Word se rehízo
            self.assertEqual(Path(carpeta, "jpl1_Cartas_Certificadas_Lote_2.docx").stat().st_mtime_ns, word_2)
            self.assertTrue(all(pdf.es_pdf(r) for r in resultado["archivos_pdf"]))
            self.assertEqual(len(resultado["archivos_pdf"]), 2)


class ContratoDelEjecutorTest(unittest.TestCase):
    def test_un_ejecutor_propio_sirve_si_respeta_el_contrato(self):
        """generar_cartas_certificadas solo necesita `ejecutar(tareas)` que entregue
        Resultado(clave, valor|error): no depende de procesos ni de tuberías."""

        class Registrador:
            def __init__(self):
                self.claves = []

            def ejecutar(self, tareas):
                for t in tareas:
                    self.claves.append(t.clave)
                    yield Resultado(t.clave, valor=t.funcion(*t.args))

        with tempfile.TemporaryDirectory() as carpeta:
            ejecutor = Registrador()
            resultado = _generar(carpeta, n=3, por_lote=1, ejecutor=ejecutor)
            self.assertEqual(ejecutor.claves, [1, 2, 3])
            self.assertEqual(len(resultado["archivos"]), 3)


if __name__ == "__main__":
    unittest.main()
