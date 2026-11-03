"""Reg N° correlativo y reparto de fechas por tandas."""
import unittest
from datetime import date

from motor.audiencias import fecha_de_carta, fechas_necesarias, parsear_fechas_por_tanda
from motor.reg import reg_para_carta, validar_reg

HOY = date(2026, 11, 2)


class RegTest(unittest.TestCase):
    def test_correlativo_de_1_en_1(self):
        self.assertEqual([reg_para_carta("15230", i) for i in range(3)], ["15230", "15231", "15232"])

    def test_conserva_prefijo_y_ceros(self):
        self.assertEqual(reg_para_carta("R-0450", 0), "R-0450")
        self.assertEqual(reg_para_carta("R-0450", 1), "R-0451")
        self.assertEqual(reg_para_carta("R-0999", 1), "R-1000")
        self.assertEqual(reg_para_carta("2026-001-0099", 2), "2026-001-0101")

    def test_pasa_de_un_digito_a_dos(self):
        self.assertEqual(reg_para_carta("9", 1), "10")

    def test_espacios_alrededor_se_ignoran(self):
        self.assertEqual(reg_para_carta("  15230 ", 1), "15231")

    def test_sin_numero_final_se_rechaza(self):
        for malo in ("", "   ", "ABC", "12A", "R-1 ", "A&B <1>"):
            if malo.strip().endswith(tuple("0123456789")):
                continue
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                validar_reg(malo)


class TandasTest(unittest.TestCase):
    def test_reparto_por_cantidad(self):
        f = parsear_fechas_por_tanda("14/12/2026\n15/12/2026\n16/12/2026", hoy=HOY)
        self.assertEqual(fecha_de_carta(f, 300, 0), date(2026, 12, 14))
        self.assertEqual(fecha_de_carta(f, 300, 299), date(2026, 12, 14))
        self.assertEqual(fecha_de_carta(f, 300, 300), date(2026, 12, 15))
        self.assertEqual(fecha_de_carta(f, 300, 899), date(2026, 12, 16))
        self.assertIsNone(fecha_de_carta(f, 300, 900))

    def test_fechas_necesarias(self):
        self.assertEqual(fechas_necesarias(900, 300), 3)
        self.assertEqual(fechas_necesarias(901, 300), 4)
        self.assertEqual(fechas_necesarias(1, 300), 1)

    def test_acepta_comas_y_rechaza_malas(self):
        self.assertEqual(len(parsear_fechas_por_tanda("14/12/2026, 15/12/2026", hoy=HOY)), 2)
        for texto, frag in (
            ("", "al menos una fecha"), ("127795-129000 14/12/2026", "no es una fecha"),
            ("01/01/2020", "ya pasó"), ("14/12/2026\n14/12/2026", "repetida"), ("14/12/2062", "más de un año"),
        ):
            with self.subTest(texto=texto), self.assertRaises(ValueError) as cm:
                parsear_fechas_por_tanda(texto, hoy=HOY)
            self.assertIn(frag, str(cm.exception))

    def test_reg_que_empieza_como_formula_se_rechaza(self):
        for texto in ("=1+1", "+5", "-3", "@9"):
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                validar_reg(texto)


if __name__ == "__main__":
    unittest.main()
