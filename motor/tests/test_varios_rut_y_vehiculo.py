"""Patente con dos RUT = dos cartas; tipo de vehículo cuando no hay marca/modelo."""
import unittest

import pandas as pd

from motor.agrupacion import construir_grupos, expandir_en_paginas


def _df(filas):
    base = {"FECHA INFRACCIÓN": "06/04/2026", "disposicion": "x", "lugar": "y", "domicilio_infractor": "CALLE 1"}
    return pd.DataFrame([{**base, **f} for f in filas])


class VariosRutTest(unittest.TestCase):
    def test_una_carta_por_rut(self):
        df = _df([
            {"patente": "AAAA11", "NRO PARTE": "1", "rut_infractor": "11111111-1", "nombre_infractor": "ANA"},
            {"patente": "AAAA11", "NRO PARTE": "2", "rut_infractor": "22222222-2", "nombre_infractor": "BETO"},
            {"patente": "AAAA11", "NRO PARTE": "3", "rut_infractor": "11111111-1", "nombre_infractor": "ANA"},
        ])
        g, _, av = construir_grupos(df, [], 1)
        self.assertEqual(len(g), 2)
        self.assertEqual(av["patentes_con_varios_rut"], 1)
        por_rut = {x["rut"]: x for x in g.values()}
        self.assertEqual([c["nroParte"] for c in por_rut["11111111-1"]["causas"]], ["1", "3"])
        self.assertEqual([c["nroParte"] for c in por_rut["22222222-2"]["causas"]], ["2"])
        self.assertTrue(all(x["placa"] == "AAAA11" for x in g.values()))
        # Cada página imprime la placa, no la clave del grupo.
        pags = expandir_en_paginas(g, 12)
        self.assertEqual({p["patente"] for p in pags}, {"AAAA11"})
        self.assertEqual(len({p["clave"] for p in pags}), 2)

    def test_rut_con_formato_distinto_es_el_mismo(self):
        df = _df([
            {"patente": "AAAA11", "NRO PARTE": "1", "rut_infractor": "1.111.111-k"},
            {"patente": "AAAA11", "NRO PARTE": "2", "rut_infractor": "1111111-K"},
        ])
        g, _, av = construir_grupos(df, [], 1)
        self.assertEqual(len(g), 1)
        self.assertEqual(av["patentes_con_varios_rut"], 0)

    def test_un_solo_rut_la_clave_sigue_siendo_la_patente(self):
        g, _, _ = construir_grupos(_df([{"patente": "AAAA11", "NRO PARTE": "1", "rut_infractor": "1-9"}]), [], 1)
        self.assertEqual(list(g), ["AAAA11"])

    def test_fila_sin_rut_en_patente_con_varios_queda_con_el_primero(self):
        df = _df([
            {"patente": "AAAA11", "NRO PARTE": "1", "rut_infractor": "11111111-1"},
            {"patente": "AAAA11", "NRO PARTE": "2", "rut_infractor": "22222222-2"},
            {"patente": "AAAA11", "NRO PARTE": "3", "rut_infractor": ""},
        ])
        g, _, av = construir_grupos(df, [], 1)
        self.assertEqual(av["filas_sin_rut_en_patente_con_varios_rut"], 1)
        self.assertEqual(len(g["AAAA11 · 11111111-1"]["causas"]), 2)


class VehiculoTest(unittest.TestCase):
    def _veh(self, **extra):
        df = _df([{"patente": "AAAA11", "NRO PARTE": "1", **extra}])
        return construir_grupos(df, [], 1)[0]["AAAA11"]["vehiculo"]

    def test_sin_marca_ni_modelo_usa_el_tipo(self):
        self.assertEqual(self._veh(vehiculo="STATION WAGON"), "STATION WAGON")
        self.assertEqual(self._veh(**{"TIPO VEHÍCULO": "CAMIONETA"}), "CAMIONETA")

    def test_con_marca_y_modelo_no_agrega_el_tipo(self):
        self.assertEqual(self._veh(vehiculo="SEDAN", **{"MARCA DEL VEHÍCULO": "KIA", "MODELO DEL VEHÍCULO": "RIO"}), "KIA, RIO")

    def test_solo_marca(self):
        self.assertEqual(self._veh(vehiculo="SEDAN", **{"MARCA DEL VEHÍCULO": "KIA"}), "KIA")

    def test_ninguno_es_guion(self):
        self.assertEqual(self._veh(), "—")

    def test_marca_del_vehiculo_no_se_toma_como_tipo(self):
        self.assertEqual(self._veh(**{"MARCA DEL VEHICULO": "KIA"}), "KIA")


if __name__ == "__main__":
    unittest.main()
