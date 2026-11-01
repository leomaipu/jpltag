"""La cláusula de audiencia sale de la FECHA CITACIÓN de la nómina (sin hora)."""
import unittest

import pandas as pd

from motor.agrupacion import construir_grupos
from motor.cartas import clausula_audiencia
from motor.columnas import tiene_columna


def _df(citas):
    return pd.DataFrame([
        {"patente": "AAAA11", "NRO PARTE": str(i), "FECHA INFRACCIÓN": "03/09/2026",
         "FECHA CITACIÓN": c, "disposicion": "x", "lugar": "y"}
        for i, c in enumerate(citas)
    ])


class ClausulaAudienciaTest(unittest.TestCase):
    def test_solo_fecha(self):
        self.assertEqual(clausula_audiencia("14/12/2026", ""), "el día 14/12/2026")

    def test_fecha_y_hora_conserva_formato_de_agenda(self):
        self.assertEqual(clausula_audiencia("14/12/2026", "09:30 hrs."), "el día 14/12/2026, a las 09:30 hrs.")

    def test_sin_fecha_o_marcador_es_a_coordinar(self):
        for f in ("", None, "A COORDINAR CON EL JUZGADO"):
            self.assertEqual(clausula_audiencia(f, ""), "en fecha y hora a coordinar con el Juzgado")

    def test_grupo_toma_la_fecha_mas_temprana_y_las_registra(self):
        g, _, _ = construir_grupos(_df(["16/12/2026", "14/12/2026"]), [], 1)
        self.assertEqual(g["AAAA11"]["citacion"], "14/12/2026")
        self.assertEqual(len(g["AAAA11"]["citaciones"]), 2)

    def test_fecha_ilegible_se_marca_y_no_inventa(self):
        g, _, _ = construir_grupos(_df(["pronto"]), [], 1)
        self.assertNotIn("citacion", g["AAAA11"])
        self.assertTrue(g["AAAA11"]["citacion_ilegible"])

    def test_sin_columna(self):
        df = _df(["14/12/2026"]).drop(columns=["FECHA CITACIÓN"])
        self.assertFalse(tiene_columna(df, "FECHA_CITA"))
        g, _, _ = construir_grupos(df, [], 1)
        self.assertNotIn("citacion", g["AAAA11"])
