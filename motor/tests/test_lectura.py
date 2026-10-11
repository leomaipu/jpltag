"""
Pruebas de motor.lectura.detectar_duplicados — el control de N° de parte
repetido en toda la nómina (Módulo 3.1). El orden fillna-antes-de-comparar
es intencional: dos filas sin N° de parte NUNCA deben marcarse como
duplicado entre sí (se verificó explícitamente en el diagnóstico del
18-ago-2026, ver motor/lectura.py).
"""

import unittest

import pandas as pd

from motor.lectura import detectar_duplicados


class TestDetectarDuplicados(unittest.TestCase):
    def test_sin_duplicados(self):
        df = pd.DataFrame({
            "Número de Parte": ["1001", "1002", "1003"],
            "Patente": ["AAAA11", "BBBB22", "CCCC33"],
        })
        limpio, dup = detectar_duplicados(df)
        self.assertEqual(len(limpio), 3)
        self.assertEqual(len(dup), 0)

    def test_detecta_nro_parte_repetido(self):
        df = pd.DataFrame({
            "Número de Parte": ["1001", "1001", "1002"],
            "Patente": ["AAAA11", "AAAA11", "BBBB22"],
        })
        limpio, dup = detectar_duplicados(df)
        self.assertEqual(len(limpio), 1)
        self.assertEqual(len(dup), 2)
        self.assertEqual(limpio.iloc[0]["Número de Parte"], "1002")

    def test_duplicado_detectado_aunque_sea_en_patentes_distintas(self):
        """El control es sobre TODA la nómina, no solo dentro de una
        patente: dos filas con el mismo N° de parte pero distinta patente
        también se consideran duplicadas (típicamente un error de carga)."""
        df = pd.DataFrame({
            "Número de Parte": ["5555", "5555"],
            "Patente": ["AAAA11", "ZZZZ99"],
        })
        limpio, dup = detectar_duplicados(df)
        self.assertEqual(len(limpio), 0)
        self.assertEqual(len(dup), 2)

    def test_filas_sin_nro_parte_nunca_se_marcan_duplicadas_entre_si(self):
        """Dos filas vacías de N° de parte (tras el fillna("")) no deben
        contarse como 'duplicadas' una de la otra — son, cada una, una
        causa sin ese dato, no un duplicado real."""
        df = pd.DataFrame({
            "Número de Parte": ["", "", "1001"],
            "Patente": ["AAAA11", "BBBB22", "CCCC33"],
        })
        limpio, dup = detectar_duplicados(df)
        self.assertEqual(len(dup), 0)
        self.assertEqual(len(limpio), 3)

    def test_sin_columna_nro_parte_no_falla(self):
        df = pd.DataFrame({"Patente": ["AAAA11", "BBBB22"]})
        limpio, dup = detectar_duplicados(df)
        self.assertEqual(len(limpio), 2)
        self.assertEqual(len(dup), 0)


if __name__ == "__main__":
    unittest.main()
