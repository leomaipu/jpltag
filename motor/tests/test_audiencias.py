"""Fechas de audiencia por bloque de folios."""
import unittest
from datetime import date

from motor.audiencias import (bloques_a_json, bloques_de_json, correlativo_rol, fecha_de_rol,
                              parsear_fechas_audiencia)

HOY = date(2026, 11, 2)


class AudienciasTest(unittest.TestCase):
    def test_folio_de_los_rol_conocidos(self):
        self.assertEqual(correlativo_rol("2026-Y-127795"), 127795)
        self.assertEqual(correlativo_rol("13Y95046/26"), 95046)
        self.assertEqual(correlativo_rol("PENDIENTE"), None)
        self.assertEqual(correlativo_rol(""), None)
        self.assertEqual(correlativo_rol(None), None)

    def test_una_fecha_para_todo(self):
        b = parsear_fechas_audiencia("14/12/2026", hoy=HOY)
        self.assertEqual(b, [(None, None, date(2026, 12, 14))])
        self.assertEqual(fecha_de_rol(b, "2026-Y-1"), date(2026, 12, 14))
        self.assertEqual(fecha_de_rol(b, "PENDIENTE"), date(2026, 12, 14))

    def test_bloques_con_extremos_incluidos(self):
        b = parsear_fechas_audiencia("100-200 14/12/2026\n201 a 300: 15/12/2026\n\n", hoy=HOY)
        self.assertEqual(fecha_de_rol(b, "2026-Y-100"), date(2026, 12, 14))
        self.assertEqual(fecha_de_rol(b, "2026-Y-200"), date(2026, 12, 14))
        self.assertEqual(fecha_de_rol(b, "2026-Y-201"), date(2026, 12, 15))
        self.assertEqual(fecha_de_rol(b, "2026-Y-300"), date(2026, 12, 15))
        self.assertIsNone(fecha_de_rol(b, "2026-Y-301"))
        self.assertIsNone(fecha_de_rol(b, "PENDIENTE"))

    def test_hoy_es_valido_ayer_no(self):
        parsear_fechas_audiencia("02/11/2026", hoy=HOY)
        with self.assertRaises(ValueError):
            parsear_fechas_audiencia("01/11/2026", hoy=HOY)

    def test_errores_claros(self):
        for texto, frag in (
            ("", "al menos una fecha"), ("   \n", "al menos una fecha"),
            ("mañana", "no se entiende"), ("31/02/2027", "no es una fecha válida"),
            ("300-100 14/12/2026", "mayor que el final"),
            ("100-200 14/12/2026\n200-300 15/12/2026", "se solapan"),
            ("14/12/2026\n100-200 15/12/2026", "no ambos"),
            ("14/12/2026\n15/12/2026", "no ambos"),
        ):
            with self.subTest(texto=texto), self.assertRaises(ValueError) as cm:
                parsear_fechas_audiencia(texto, hoy=HOY)
            self.assertIn(frag, str(cm.exception))

    def test_ida_y_vuelta_json(self):
        b = parsear_fechas_audiencia("100-200 14/12/2026", hoy=HOY)
        self.assertEqual(bloques_de_json(bloques_a_json(b)), b)


if __name__ == "__main__":
    unittest.main()
