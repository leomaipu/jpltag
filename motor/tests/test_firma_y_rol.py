"""
El ROL de la nómina (la columna «ROL», p. ej. «13Y95046/26») y el bloque de firma de las cartas: imágenes achicadas hasta caber
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
from motor.columnas import columna_rol
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


def _con_rol(valores, encabezado="ROL"):
    """Como la nómina del 3er JPL: el ROL en la columna «ROL»."""
    filas = [fila(pat=f"AAAA1{i}", nro_parte=f"125584{i}-M78") for i in range(len(valores))]
    df = pd.DataFrame(filas).drop(columns=["Rol"])
    df.insert(0, encabezado, valores)
    return df


class RolDeLaNominaTest(unittest.TestCase):
    def test_se_lee_de_la_columna_rol_y_va_tal_cual(self):
        df = leer_nomina(_nomina_xlsx(_con_rol(["13Y95046/26", "13Y97421/26"])))
        self.assertEqual(columna_rol(df), "ROL")
        self.assertEqual(df.attrs["avisos_lectura"], {})
        grupos, _, _ = construir_grupos(df, [], utm_multa_defecto=1)
        rol = grupos["AAAA10"]["causas"][0]["rol_raw"]
        self.assertEqual(rol, "13Y95046/26")
        self.assertEqual(formatear_rol(rol), "13Y95046/26")

    def test_no_se_le_agrega_nada_ni_se_toca_el_formato(self):
        for valor in ("2026-Y11-259698", "11Y80151/26", "2026 Y419252", "3090675-MUN", "4512"):
            self.assertEqual(formatear_rol(valor), valor)
        self.assertEqual(formatear_rol("  13Y95046/26\r"), "13Y95046/26")

    def test_sin_dato_dice_pendiente(self):
        self.assertEqual(formatear_rol(""), "PENDIENTE")
        self.assertEqual(formatear_rol(None), "PENDIENTE")

    def test_el_encabezado_se_reconoce_sin_importar_mayusculas_ni_espacios(self):
        for encabezado in ("Rol", " rol ", "ROL", "Rol causa", "CAUSA"):
            df = _con_rol(["13Y1/26"], encabezado)
            self.assertEqual(columna_rol(df), encabezado)

    def test_rolmop_no_se_toma_como_rol(self):
        # «rol» está dentro de «rolmop»: un match por subcadena lo confundiría
        df = _con_rol(["13Y1/26"], "ROLMOP")
        self.assertIsNone(columna_rol(df))
        grupos, _, _ = construir_grupos(leer_nomina(_nomina_xlsx(df)), [], utm_multa_defecto=1)
        self.assertEqual(grupos["AAAA10"]["causas"][0]["rol_raw"], "")

    def test_el_n_de_parte_y_la_descripcion_no_se_toman_como_rol(self):
        df = pd.DataFrame([fila(nro_parte="1255846-M78")]).drop(columns=["Rol"])
        df["DESCRIPCIÓN CAUSA"] = ["Infraccion al Art. 114"]
        self.assertIsNone(columna_rol(df))

    def test_un_valor_con_formato_distinto_se_imprime_igual(self):
        grupos, _, _ = construir_grupos(
            leer_nomina(_nomina_xlsx(_con_rol(["13Y95046/26", "S/N"]))), [], utm_multa_defecto=1)
        self.assertEqual(grupos["AAAA11"]["causas"][0]["rol_raw"], "S/N")


class RolAusenteTest(unittest.TestCase):
    def test_sin_columna_rol_avisa_y_la_carta_dice_pendiente(self):
        df = pd.DataFrame([fila(pat=f"AAAA1{i}") for i in range(3)]).drop(columns=["Rol"])
        leida = leer_nomina(_nomina_xlsx(df))
        self.assertEqual(leida.attrs["avisos_lectura"], {"rol_no_encontrado": True})
        grupos, _, _ = construir_grupos(leida, [], utm_multa_defecto=1)
        self.assertEqual(formatear_rol(grupos["AAAA10"]["causas"][0]["rol_raw"]), "PENDIENTE")

    def test_valores_vacios_avisan_cuantos(self):
        leida = leer_nomina(_nomina_xlsx(_con_rol(["13Y1/26", "", "13Y3/26", ""])))
        self.assertEqual(leida.attrs["avisos_lectura"], {"rol_vacios": 2})


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
        "causas": [{"nroParte": "9001", "rol_raw": "13Y95046/26", "fecha": "03/09/2026", "hora": "08:00",
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
