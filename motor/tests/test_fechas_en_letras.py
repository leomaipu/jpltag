"""
Pruebas de motor.fechas_en_letras contra los mismos ejemplos que traían
escritos a mano los AGENDA_AUDIENCIAS de los scripts originales, para
confirmar que la generalización no cambió el texto esperado.
"""

import datetime as dt
import unittest

from motor.fechas_en_letras import anio_en_letras, dia_en_letras, fecha_en_letras


class TestFechaEnLetras(unittest.TestCase):
    def test_ejemplos_del_script_original(self):
        self.assertEqual(fecha_en_letras(dt.date(2026, 9, 7)), "siete de septiembre de dos mil veintiséis")
        self.assertEqual(fecha_en_letras(dt.date(2026, 9, 8)), "ocho de septiembre de dos mil veintiséis")

    def test_dia_31_es_caso_especial(self):
        self.assertEqual(dia_en_letras(31), "treinta y uno")

    def test_dia_16_lleva_tilde(self):
        self.assertEqual(dia_en_letras(16), "dieciséis")

    def test_anio_exacto_dos_mil(self):
        self.assertEqual(anio_en_letras(2000), "dos mil")

    def test_anio_fuera_de_rango_devuelve_numero(self):
        self.assertEqual(anio_en_letras(1999), "1999")
        self.assertEqual(anio_en_letras(2100), "2100")


if __name__ == "__main__":
    unittest.main()
