"""
Pruebas de motor.agrupacion: construir_grupos (regla de causa válida y
exclusión de patentes sin causas), detectar_mes_predominante,
filtrar_por_rango_dias y expandir_en_paginas (división en páginas de a
lo más N causas).
"""

import unittest

import pandas as pd

from motor.agrupacion import (
    construir_grupos,
    detectar_mes_predominante,
    expandir_en_paginas,
    filtrar_por_rango_dias,
)

COLUMNAS = [
    "Patente", "Digito Patente", "Número de Parte", "Rol", "Reg N",
    "Fecha Infracción", "Hora Infracción", "Lugar de la Infracción",
    "Disposiciones Infringidas", "Nombre Infractor", "Apellido 1", "Apellido 2",
    "Cedula de Identidad", "Digito Verifcador", "Domicilio Infractor", "Comuna",
    "Marca del Vehiculo", "Modelo del Vehiculo",
]


def fila(pat="AAAA11", dig="1", nro_parte="1001", rol="", reg="", fecha="03/09/2026",
         hora="08:30", lugar="Autopista km 1", disposicion="TAG no vigente",
         nombre="Juan", ap1="Perez", ap2="Soto", rut="11111111", dv="1",
         domicilio="Calle 1", comuna="Maipu", marca="Chevrolet", modelo="Spark"):
    return dict(zip(COLUMNAS, [
        pat, dig, nro_parte, rol, reg, fecha, hora, lugar, disposicion,
        nombre, ap1, ap2, rut, dv, domicilio, comuna, marca, modelo,
    ]))


def df_de(filas):
    return pd.DataFrame(filas).fillna("")


class TestConstruirGrupos(unittest.TestCase):
    def test_agrupa_causas_de_la_misma_patente(self):
        df = df_de([
            fila(pat="AAAA11", nro_parte="1001", fecha="03/09/2026"),
            fila(pat="AAAA11", nro_parte="1002", fecha="05/09/2026"),
            fila(pat="BBBB22", nro_parte="1003", fecha="06/09/2026"),
        ])
        grupos, sin_causas, avisos = construir_grupos(df, [], utm_multa_defecto=1)
        self.assertEqual(set(grupos.keys()), {"AAAA11", "BBBB22"})
        self.assertEqual(len(grupos["AAAA11"]["causas"]), 2)
        self.assertEqual(len(grupos["BBBB22"]["causas"]), 1)
        self.assertEqual(avisos["filas_sin_patente"], 0)
        self.assertEqual(avisos["causas_vacias"], 0)

    def test_fila_sin_nro_parte_ni_fecha_se_descarta_como_causa(self):
        """Causa 'vacía' (sin N° de parte NI fecha): no cuenta, no
        aparece en la tabla, y si es la única fila de esa patente, la
        patente queda excluida por completo."""
        df = df_de([
            fila(pat="AAAA11", nro_parte="", fecha=""),
        ])
        grupos, sin_causas, avisos = construir_grupos(df, [], utm_multa_defecto=1)
        self.assertNotIn("AAAA11", grupos)
        self.assertEqual(avisos["causas_vacias"], 1)

    def test_fila_con_solo_nro_parte_cuenta_como_causa_valida(self):
        df = df_de([fila(pat="AAAA11", nro_parte="1001", fecha="")])
        grupos, sin_causas, avisos = construir_grupos(df, [], utm_multa_defecto=1)
        self.assertIn("AAAA11", grupos)
        self.assertEqual(len(grupos["AAAA11"]["causas"]), 1)

    def test_fila_sin_patente_se_excluye_y_se_cuenta(self):
        df = df_de([fila(pat=""), fila(pat="AAAA11")])
        grupos, sin_causas, avisos = construir_grupos(df, [], utm_multa_defecto=1)
        self.assertEqual(avisos["filas_sin_patente"], 1)
        self.assertEqual(set(grupos.keys()), {"AAAA11"})

    def test_reg_raw_se_preserva_para_el_bloque_postal(self):
        df = df_de([fila(pat="AAAA11", reg="R-42")])
        grupos, _, _ = construir_grupos(df, [], utm_multa_defecto=1)
        self.assertEqual(grupos["AAAA11"]["reg_raw"], "R-42")


class TestDetectarMesPredominante(unittest.TestCase):
    def test_elige_el_mes_con_mas_causas(self):
        df = df_de([
            fila(pat="A1", nro_parte="1", fecha="03/09/2026"),
            fila(pat="A2", nro_parte="2", fecha="05/09/2026"),
            fila(pat="A3", nro_parte="3", fecha="15/08/2026"),
        ])
        grupos, _, _ = construir_grupos(df, [], utm_multa_defecto=1)
        anio, mes = detectar_mes_predominante(grupos)
        self.assertEqual((anio, mes), (2026, 9))

    def test_sin_fechas_devuelve_none(self):
        anio, mes = detectar_mes_predominante({})
        self.assertIsNone(anio)
        self.assertIsNone(mes)


class TestFiltrarPorRangoDias(unittest.TestCase):
    def test_separa_incluidos_otro_mes_y_otro_rango(self):
        df = df_de([
            fila(pat="A1", nro_parte="1", fecha="03/09/2026"),   # día 3 -> incluido (1-10)
            fila(pat="A2", nro_parte="2", fecha="15/09/2026"),   # día 15 -> otro rango
            fila(pat="A3", nro_parte="3", fecha="05/08/2026"),   # otro mes
            fila(pat="A4", nro_parte="4", fecha="no-es-fecha"),  # sin fecha legible
        ])
        grupos, _, _ = construir_grupos(df, [], utm_multa_defecto=1)
        incluidos, sin_fecha, otro_mes, otro_rango = filtrar_por_rango_dias(
            grupos, 2026, 9, 1, 10)
        self.assertEqual(set(incluidos.keys()), {"A1"})
        self.assertEqual(set(otro_rango.keys()), {"A2"})
        self.assertEqual(set(otro_mes.keys()), {"A3"})
        self.assertEqual(set(sin_fecha.keys()), {"A4"})


class TestExpandirEnPaginas(unittest.TestCase):
    def test_divide_en_paginas_de_a_lo_mas_n_causas(self):
        filas = [fila(pat="A1", nro_parte=str(i), fecha="03/09/2026") for i in range(5)]
        df = df_de(filas)
        grupos, _, _ = construir_grupos(df, [], utm_multa_defecto=1)
        paginas = expandir_en_paginas(grupos, max_causas_por_pagina=2)
        self.assertEqual(len(paginas), 3)  # 2 + 2 + 1
        self.assertEqual([len(p["chunk"]) for p in paginas], [2, 2, 1])
        self.assertEqual(paginas[0]["total_partes"], 3)
        self.assertEqual(paginas[0]["total_causas"], 5)

    def test_una_sola_pagina_si_no_se_supera_el_maximo(self):
        df = df_de([fila(pat="A1", nro_parte="1", fecha="03/09/2026")])
        grupos, _, _ = construir_grupos(df, [], utm_multa_defecto=1)
        paginas = expandir_en_paginas(grupos, max_causas_por_pagina=20)
        self.assertEqual(len(paginas), 1)
        self.assertEqual(paginas[0]["parte"], 1)
        self.assertEqual(paginas[0]["total_partes"], 1)


if __name__ == "__main__":
    unittest.main()
