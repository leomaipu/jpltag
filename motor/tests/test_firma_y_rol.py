"""
El ROL municipal de la nómina (la columna sin encabezado con valores
«…-MUN») y el bloque de firma de las cartas: imágenes achicadas hasta caber
en su caja y el timbre arriba de la firma y del nombre (29-oct-2026).
"""

import io
import os
import tempfile
import unittest

import pandas as pd
from docx.oxml.ns import qn
from PIL import Image

from motor.agrupacion import construir_grupos
from motor.cartas import formatear_rol
from motor.columnas import COLUMNA_ROL_MUN, columna_rol_municipal
from motor.lectura import leer_nomina
from motor.plantilla import (
    CAJA_FIRMA_IN,
    CAJA_FIRMA_SIN_SELLO_IN,
    CAJA_SELLO_IN,
    TIPOS_CARTA,
    construir_contexto,
    renderizar_carta,
    ruta_plantilla_defecto,
    tamano_en_caja,
)
from motor.tests.test_agrupacion import fila


def _nomina_xlsx(df):
    buffer = io.BytesIO()
    df.to_excel(buffer, index=False)
    buffer.seek(0)
    return buffer


def _con_columna_sin_encabezado(valores):
    """Como la nómina de la DAF: el ROL en una columna sin título."""
    filas = [fila(pat=f"AAAA1{i}", nro_parte=f"125584{i}-M78") for i in range(len(valores))]
    df = pd.DataFrame(filas)
    df.insert(9, "", valores)
    return df


class RolMunicipalTest(unittest.TestCase):
    def test_la_columna_sin_encabezado_se_reconoce_por_su_contenido(self):
        df = leer_nomina(_nomina_xlsx(_con_columna_sin_encabezado(["3090675-MUN", "3090676-MUN"])))
        self.assertIn(COLUMNA_ROL_MUN, df.columns)
        grupos, _, _ = construir_grupos(df, [], utm_multa_defecto=1)
        self.assertEqual(grupos["AAAA10"]["causas"][0]["rol_raw"], "3090675-MUN")

    def test_va_tal_cual_en_la_carta(self):
        self.assertEqual(formatear_rol("3090675-MUN", 2026), "3090675-MUN")
        self.assertEqual(formatear_rol("3090675-mun", 2026), "3090675-mun")
        # un folio suelto sigue el formato de siempre, y sin dato, PENDIENTE
        self.assertEqual(formatear_rol("4512", 2026), "4512-T-2026")
        self.assertEqual(formatear_rol("", 2026), "PENDIENTE")

    def test_el_n_de_parte_no_se_confunde_con_el_rol(self):
        df = pd.DataFrame([fila(nro_parte="1255846-M78")])
        self.assertIsNone(columna_rol_municipal(df))

    def test_una_columna_que_no_es_toda_mun_no_cuenta(self):
        df = _con_columna_sin_encabezado(["3090675-MUN", "otra cosa"])
        self.assertIsNone(columna_rol_municipal(df))

    def test_las_celdas_vacias_no_impiden_reconocerla(self):
        df = _con_columna_sin_encabezado(["3090675-MUN", ""])
        self.assertEqual(columna_rol_municipal(df), "")

    def test_gana_al_encabezado_rol(self):
        df = _con_columna_sin_encabezado(["3090675-MUN"])
        df["Rol"] = ["999"]
        grupos, _, _ = construir_grupos(leer_nomina(_nomina_xlsx(df)), [], utm_multa_defecto=1)
        self.assertEqual(grupos["AAAA10"]["causas"][0]["rol_raw"], "3090675-MUN")


def _imagen(carpeta, nombre, tamano):
    ruta = os.path.join(carpeta, nombre)
    Image.new("RGB", tamano, "white").save(ruta)
    return ruta


class CajaDeImagenTest(unittest.TestCase):
    def setUp(self):
        self.carpeta = tempfile.mkdtemp(prefix="jpltag-cajas-")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.carpeta, ignore_errors=True)

    def test_una_imagen_vertical_la_limita_el_alto(self):
        """La imagen única con firma y timbre del 3er JPL (120 × 146 px)."""
        ancho, alto = tamano_en_caja(_imagen(self.carpeta, "v.jpg", (120, 146)), CAJA_FIRMA_SIN_SELLO_IN)
        self.assertEqual(alto, CAJA_FIRMA_SIN_SELLO_IN[1])
        self.assertAlmostEqual(ancho / alto, 120 / 146, places=2)

    def test_una_imagen_horizontal_la_limita_el_ancho_o_el_alto_sin_deformarse(self):
        ancho, alto = tamano_en_caja(_imagen(self.carpeta, "h.png", (1000, 100)), CAJA_FIRMA_IN)
        self.assertEqual(ancho, CAJA_FIRMA_IN[0])
        self.assertLessEqual(alto, CAJA_FIRMA_IN[1])
        self.assertAlmostEqual(ancho / alto, 10, places=1)

    def test_una_imagen_chica_se_ajusta_a_la_caja(self):
        ancho, alto = tamano_en_caja(_imagen(self.carpeta, "c.png", (84, 96)), CAJA_SELLO_IN)
        self.assertEqual(alto, CAJA_SELLO_IN[1])
        self.assertLessEqual(ancho, CAJA_SELLO_IN[0])

    def test_si_cambia_el_archivo_cambia_el_tamano(self):
        ruta = _imagen(self.carpeta, "x.png", (100, 100))
        primero = tamano_en_caja(ruta, CAJA_FIRMA_IN)
        Image.new("RGB", (400, 100), "white").save(ruta)
        os.utime(ruta, ns=(os.stat(ruta).st_atime_ns, os.stat(ruta).st_mtime_ns + 10**9))
        self.assertNotEqual(tamano_en_caja(ruta, CAJA_FIRMA_IN), primero)


def _info():
    return {
        "digPat": "1", "rol_raw": "", "reg_raw": "", "nombre": "MARIA PRUEBA", "rut": "1-9",
        "domicilio": "x", "comuna": "Maipu", "vehiculo": "x",
        "causas": [{"nroParte": "9001", "rol_raw": "3090675-MUN", "fecha": "03/09/2026", "hora": "08:00",
                    "lugar": "x", "disposicion": "x", "monto": 1}],
    }


def _bloque_de_firma(doc):
    """Los párrafos de la celda donde está el nombre del firmante, en orden:
    'IMG' por cada párrafo con imagen y el texto de los demás."""
    for celda in doc.element.body.iter(qn("w:tc")):
        textos = ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in celda.iter(qn("w:p"))]
        if any("ANA FIRMANTE" in t for t in textos):
            return [
                "IMG" if list(p.iter(qn("w:drawing"))) else "".join(t.text or "" for t in p.iter(qn("w:t")))
                for p in celda.iter(qn("w:p"))
            ]
    raise AssertionError("no se encontró el bloque de firma")


class BloqueDeFirmaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.carpeta = tempfile.mkdtemp(prefix="jpltag-firma-")
        cls.firma = _imagen(cls.carpeta, "firma.png", (327, 170))
        cls.sello = _imagen(cls.carpeta, "sello.png", (84, 96))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.carpeta, ignore_errors=True)

    def _bloque(self, n, tipo, **imagenes):
        config = {"tipo_carta": tipo, "slug_juzgado": f"jpl{n}", "nombre_juzgado": "JPL", "utm_valor": 70000,
                  "nombre_firmante": "ANA FIRMANTE", "cargo_firma": "SECRETARIA ABOGADA",
                  "fecha_audiencia": "29/10/2026", **imagenes}
        pagina = {"patente": "AAAA11", "info": _info(), "chunk": _info()["causas"],
                  "parte": 1, "total_partes": 1, "total_causas": 1}
        doc = renderizar_carta(ruta_plantilla_defecto(f"jpl{n}", "", tipo),
                               construir_contexto(pagina, ("A COORDINAR CON EL JUZGADO", ""), 1, config), config)
        return [t for t in _bloque_de_firma(doc) if t.strip()]

    def test_timbre_arriba_de_la_firma_y_del_nombre_en_las_nueve_plantillas(self):
        for n in (1, 2, 3):
            for tipo in TIPOS_CARTA:
                with self.subTest(juzgado=n, tipo=tipo):
                    self.assertEqual(
                        self._bloque(n, tipo, firma_path=self.firma, sello_path=self.sello),
                        ["IMG", "IMG", "ANA FIRMANTE", "SECRETARIA ABOGADA"],
                    )

    def test_sin_timbre_no_queda_un_parrafo_vacio(self):
        self.assertEqual(self._bloque(3, "CITACION", firma_path=self.firma),
                         ["IMG", "ANA FIRMANTE", "SECRETARIA ABOGADA"])

    def test_sin_firma_queda_la_linea_para_firmar_a_mano(self):
        bloque = self._bloque(1, "CITACION")
        self.assertEqual(bloque[1:], ["ANA FIRMANTE", "SECRETARIA ABOGADA"])
        self.assertTrue(bloque[0].startswith("....."))


if __name__ == "__main__":
    unittest.main()
