"""
Pruebas de motor.multas.buscar_multa (Módulo 5): monto UTM por tipo de
causa, con coincidencia exacta preferida sobre parcial, y valor por
defecto cuando no se reconoce la disposición.
"""

import unittest

from motor.multas import buscar_multa


class TestBuscarMulta(unittest.TestCase):
    def setUp(self):
        # buscar_multa no ordena la tabla por su cuenta — cargar_tabla_multas
        # la entrega ya ordenada de más larga a más corta, para que un texto
        # más específico (ej. "...por falta de pago") no quede tapado por
        # uno más genérico que también calza como substring ("TAG no
        # vigente"). Se arma ya en ese orden acá, igual que en producción.
        self.tabla = [
            ("TAG no vigente por falta de pago", 2.5),
            ("TAG no vigente", 1.0),
            ("Exceso de velocidad", 3.0),
        ]

    def test_coincidencia_exacta(self):
        self.assertEqual(buscar_multa("TAG no vigente", self.tabla, 99), 1.0)

    def test_coincidencia_exacta_prevalece_sobre_parcial(self):
        """'TAG no vigente' calza EXACTO con la primera fila y también
        estaría CONTENIDO en la segunda — debe ganar la exacta."""
        self.assertEqual(buscar_multa("TAG no vigente", self.tabla, 99), 1.0)

    def test_coincidencia_parcial(self):
        self.assertEqual(
            buscar_multa("TAG no vigente por falta de pago - reincidencia", self.tabla, 99),
            2.5,
        )

    def test_sin_coincidencia_usa_defecto(self):
        avisos = set()
        monto = buscar_multa("Disposición completamente desconocida", self.tabla, 7, avisos)
        self.assertEqual(monto, 7)
        self.assertIn("Disposición completamente desconocida", avisos)

    def test_disposicion_vacia_usa_defecto_sin_avisar(self):
        avisos = set()
        self.assertEqual(buscar_multa("", self.tabla, 7, avisos), 7)
        self.assertEqual(buscar_multa("—", self.tabla, 7, avisos), 7)
        self.assertEqual(len(avisos), 0)

    def test_tabla_vacia_usa_defecto_sin_avisar(self):
        """Sin tipo_causas.xlsx cargado, se usa el valor fijo para todo,
        sin llenar `avisos_no_encontrados` (no tiene sentido avisar que
        'no se encontró' si nunca hubo tabla)."""
        avisos = set()
        self.assertEqual(buscar_multa("Cualquier cosa", [], 1, avisos), 1)
        self.assertEqual(len(avisos), 0)

    def test_avisa_una_sola_vez_por_texto_distinto(self):
        avisos = set()
        buscar_multa("Desconocida A", self.tabla, 1, avisos)
        buscar_multa("Desconocida A", self.tabla, 1, avisos)
        buscar_multa("Desconocida B", self.tabla, 1, avisos)
        self.assertEqual(avisos, {"Desconocida A", "Desconocida B"})


if __name__ == "__main__":
    unittest.main()
