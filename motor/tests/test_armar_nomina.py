"""«Arma tu nómina»: cruce de Ingresos diarios + Causas por estado."""
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from motor.agrupacion import construir_grupos
from motor.armar_nomina import (COLUMNAS_NOMINA, ReporteInvalido, armar_nomina,
                                escribir_informe, escribir_nomina)
from motor.lectura import detectar_duplicados, leer_nomina


def ing_fila(n, patente="AAAA11", rut="11111111-1_x000D_", estado="PENDIENTE", cita=datetime(2026, 10, 1),
             fecha_inf=datetime(2026, 4, 6), rol=None, parte=None, desc=None):
    return {
        "CAUSA ": rol or f"2026-Y-{127000 + n}", "NRO PARTE": parte or f"{1000 + n}-MVN",
        "OFICINA REMISORA": "MOP", "DESCRIPCIÓN CAUSA ": desc or f"Infraccion al Art. 114. - ID:{16000000 + n}",
        "FECHA PROCESO": datetime(2026, 10, 1), "FECHA CITACIÓN": cita, "ESTADO CAUSA": estado,
        "FECHA TRÁMITE": datetime(2026, 10, 1), "TRÁMITE": "CARGA MOP", "RUT": rut,
        "DENUNCIADO": "X_x000D_", "FECHA INFRACCIÓN": fecha_inf, "PLACA": f" {patente}_x000D_",
        "TIPO VEHÍCULO": "AUTOMOVIL_x000D_",
    }


def cau_fila(n, rut="11111111-1", nombre="PEREZ SOTO JUAN", dir_="CALLE 1 ", comuna="MAIPU", estado="PENDIENTE",
             rol=None, parte=None):
    return {"causarol": rol or f"2026-Y-{127000 + n}", "nombre": nombre, "rut": rut, "direccion": dir_,
            "comuna": comuna, "lugarhechogen": "Pórtico 1", "parte": parte or f"{1000 + n}-MVN",
            "estado": estado}


class ArmarNominaTest(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def _armar(self, ing, cau, **kw):
        a, b = self.dir / "ing.xlsx", self.dir / "cau.xlsx"
        pd.DataFrame(ing).to_excel(a, index=False)
        pd.DataFrame(cau).to_excel(b, index=False)
        return armar_nomina(a, b, **kw)

    def test_cruza_y_deja_las_columnas_del_motor(self):
        nom, inf = self._armar([ing_fila(1), ing_fila(2)], [cau_fila(1), cau_fila(2)])
        self.assertEqual(list(nom.columns[:21]), COLUMNAS_NOMINA)
        self.assertEqual(len(nom), 2)
        fila = nom.iloc[0]
        self.assertEqual(fila["ROL"], "2026-Y-127001")
        self.assertEqual(fila["patente"], "AAAA11")             # sin espacios ni _x000D_
        self.assertEqual(fila["rut_infractor"], "11111111-1")
        self.assertEqual(fila["domicilio_infractor"], "CALLE 1")
        self.assertEqual(fila["disposicion"], "Infraccion al Art. 114.")
        self.assertEqual(fila["ID MOP"], "16000001")
        self.assertEqual(fila["vehiculo"], "AUTOMOVIL")
        self.assertEqual(fila["FECHA INFRACCIÓN"], date(2026, 4, 6))
        self.assertEqual(fila["FECHA CITACIÓN"], date(2026, 10, 1))

    def test_lo_que_el_reporte_no_trae_queda_vacio(self):
        nom, _ = self._armar([ing_fila(1)], [cau_fila(1)])
        for c in ("HORA INFRACCIÓN", "MARCA DEL VEHÍCULO", "MODELO DEL VEHÍCULO"):
            self.assertEqual(nom.iloc[0][c], "")

    def test_solo_pendientes_y_lo_demas_queda_listado(self):
        nom, inf = self._armar([ing_fila(1), ing_fila(2, estado="PAGADO Y ARCHIVADO")], [cau_fila(1), cau_fila(2)])
        self.assertEqual(list(nom["NRO PARTE"]), ["1001-MVN"])
        self.assertEqual(len(inf.excluidas), 1)
        self.assertIn("PAGADO Y ARCHIVADO", inf.excluidas.iloc[0]["Motivo"])

    def test_sin_cruce_se_excluye_con_motivo(self):
        nom, inf = self._armar([ing_fila(1), ing_fila(2)], [cau_fila(1)])
        self.assertEqual(len(nom), 1)
        self.assertIn("No está en Causas por estado", inf.excluidas.iloc[0]["Motivo"])

    def test_partes_repetidos_se_excluyen_todas_las_copias(self):
        nom, inf = self._armar([ing_fila(1), ing_fila(2, parte="1001-MVN")], [cau_fila(1), cau_fila(2)])
        self.assertEqual(len(nom), 0)
        self.assertEqual(sum("repetido en Ingresos diarios" in m for m in inf.excluidas["Motivo"]), 2)

    def test_parte_repetido_en_causas_por_estado(self):
        nom, inf = self._armar([ing_fila(1)], [cau_fila(1), cau_fila(1)])
        self.assertEqual(len(nom), 0)
        self.assertIn("repetido en Causas por estado", inf.excluidas.iloc[0]["Motivo"])

    def test_contradicciones_entre_reportes(self):
        nom, inf = self._armar([ing_fila(1), ing_fila(2), ing_fila(3)],
                               [cau_fila(1, rol="2026-Y-1"), cau_fila(2, rut="22222222-2"), cau_fila(3, estado="FALLADA")])
        self.assertEqual(len(nom), 0)
        motivos = " | ".join(inf.excluidas["Motivo"])
        self.assertIn("ROL", motivos); self.assertIn("RUT", motivos); self.assertIn("otro estado", motivos)

    def test_domicilio_se_completa_con_otra_causa_del_mismo_rut(self):
        nom, inf = self._armar([ing_fila(1), ing_fila(2, patente="BBBB22")],
                               [cau_fila(1, dir_=None, comuna=None), cau_fila(2)])
        self.assertEqual(nom.iloc[0]["domicilio_infractor"], "CALLE 1")
        self.assertEqual(nom.iloc[0]["comuna_infractor"], "MAIPU")
        self.assertEqual(inf.resumen["Domicilios completados desde otra causa del mismo RUT"], 1)
        self.assertFalse(any("sin domicilio" in t for _, t in inf.alertas))

    def test_comuna_siempre_con_su_direccion(self):
        nom, _ = self._armar([ing_fila(1), ing_fila(2, patente="BBBB22")],
                             [cau_fila(1, dir_=None, comuna="LAS CONDES"), cau_fila(2, comuna="MAIPU")])
        self.assertEqual((nom.iloc[0]["domicilio_infractor"], nom.iloc[0]["comuna_infractor"]), ("CALLE 1", "MAIPU"))

    def test_partes_en_blanco_se_excluyen_con_motivo(self):
        nom, inf = self._armar([ing_fila(1, parte=" "), ing_fila(2, parte=" ")],
                               [cau_fila(1, parte=" "), cau_fila(2, parte=" ")])
        self.assertEqual(len(nom), 0)
        self.assertEqual(list(inf.excluidas["Motivo"]), ["Sin N° de parte en Ingresos diarios"] * 2)

    def test_alertas_de_causas_que_no_entran(self):
        _, inf = self._armar([ing_fila(1)], [cau_fila(1), cau_fila(2), cau_fila(3, estado="FALLADA")])
        textos = " | ".join(t for _, t in inf.alertas)
        self.assertIn("PENDIENTES del listado por estado no están en Ingresos diarios", textos)
        self.assertIn("FALLADA", textos)
        self.assertEqual(len(inf.revisar["Pendientes sin ingreso"]), 1)

    def test_fecha_de_infraccion_en_formato_mm_dd_queda_ilegible(self):
        nom, inf = self._armar([ing_fila(1, fecha_inf="04/25/2026"), ing_fila(2, fecha_inf="06/04/2026")],
                               [cau_fila(1), cau_fila(2)])
        self.assertEqual(nom.iloc[1]["FECHA INFRACCIÓN"], date(2026, 4, 6))
        self.assertTrue(any("FECHA INFRACCIÓN ilegible" in t for _, t in inf.alertas))

    def test_alertas_de_mes_y_dias(self):
        _, inf = self._armar([ing_fila(1), ing_fila(2, fecha_inf=datetime(2026, 5, 15))], [cau_fila(1), cau_fila(2)])
        textos = " | ".join(t for _, t in inf.alertas)
        self.assertIn("más de un mes", textos)
        self.assertNotIn("Todas las fechas de infracción caen", textos)
        _, inf = self._armar([ing_fila(1)], [cau_fila(1)])
        self.assertIn("Todas las fechas de infracción caen en los días 1–10", " | ".join(t for _, t in inf.alertas))
        self.assertIn("1er juzgado (días 1–10): 1", " | ".join(t for _, t in inf.alertas))

    def test_estado_en_minuscula_y_con_espacios(self):
        nom, _ = self._armar([ing_fila(1, estado="  pendiente ")], [cau_fila(1, estado="Pendiente")])
        self.assertEqual(len(nom), 1)

    def test_sin_domicilio_y_sin_otro_rut_coincidente_queda_fuera_y_registrada(self):
        nom, inf = self._armar([ing_fila(1)], [cau_fila(1, dir_=None)])
        self.assertEqual(len(nom), 0)
        motivos = list(inf.excluidas["Motivo"])
        self.assertEqual(len(motivos), 1)
        self.assertTrue(motivos[0].startswith("Sin domicilio"))
        self.assertTrue(any("sin tener domicilio" in t or "por no tener domicilio" in t for _, t in inf.alertas))
        self.assertEqual(len(inf.revisar["Sin domicilio"]), 1)

    def test_domicilio_se_busca_por_rut_en_todo_el_listado_aunque_este_fallada(self):
        # La otra causa del RUT está FALLADA y no entra a la nómina, pero su dirección sirve.
        nom, inf = self._armar([ing_fila(1)], [cau_fila(1, dir_=None, comuna=None),
                                               cau_fila(2, estado="FALLADA", dir_="AV SIEMPRE VIVA 742", comuna="LO PRADO")])
        self.assertEqual(len(nom), 1)
        self.assertEqual((nom.iloc[0]["domicilio_infractor"], nom.iloc[0]["comuna_infractor"]), ("AV SIEMPRE VIVA 742", "LO PRADO"))
        self.assertEqual(inf.resumen["Domicilios completados desde otra causa del mismo RUT"], 1)

    def test_rut_con_puntos_o_minusculas_coincide(self):
        nom, _ = self._armar([ing_fila(1, rut="1.111.111-k")], [cau_fila(1, rut="1.111.111-k", dir_=None),
                                                              cau_fila(2, rut="1111111-K", estado="FALLADA")])
        self.assertEqual(len(nom), 1)

    def test_pendientes_sin_ingreso_van_a_excluidas(self):
        _, inf = self._armar([ing_fila(1)], [cau_fila(1), cau_fila(2)])
        self.assertIn("PENDIENTE en Causas por estado pero sin Ingresos diarios", " ".join(inf.excluidas["Motivo"]))

    def test_domicilio_basura_se_trata_como_faltante(self):
        for basura in ("A 1", "0", "00", "A 0", "1", "M 136"):
            with self.subTest(basura=basura):
                nom, inf = self._armar([ing_fila(1)], [cau_fila(1, dir_=basura)])
                self.assertEqual(len(nom), 0)
                self.assertIn(f"«{basura}»", inf.excluidas.iloc[0]["Motivo"])

    def test_domicilio_basura_se_completa_con_una_direccion_real_del_mismo_rut(self):
        nom, inf = self._armar([ing_fila(1)], [cau_fila(1, dir_="A 1", comuna="X"),
                                               cau_fila(2, estado="FALLADA", dir_="AV REAL 12", comuna="LO PRADO")])
        self.assertEqual((nom.iloc[0]["domicilio_infractor"], nom.iloc[0]["comuna_infractor"]), ("AV REAL 12", "LO PRADO"))

    def test_una_direccion_real_corta_no_se_descarta(self):
        nom, _ = self._armar([ing_fila(1)], [cau_fila(1, dir_="AV 5 NORTE 12")])
        self.assertEqual(len(nom), 1)

    def test_resumen_separa_excluidas_y_pendientes_sin_ingreso(self):
        _, inf = self._armar([ing_fila(1), ing_fila(2, estado="PAGADO Y ARCHIVADO")], [cau_fila(1), cau_fila(2), cau_fila(3)])
        self.assertEqual(inf.resumen["Filas excluidas de Ingresos diarios (ver hoja «Excluidas»)"], 1)
        self.assertEqual(inf.resumen["Causas PENDIENTES sin Ingresos diarios (también en «Excluidas»)"], 1)
        self.assertEqual(len(inf.excluidas), 2)

    def test_citacion_en_el_pasado_se_alerta(self):
        _, inf = self._armar([ing_fila(1, cita=datetime(2020, 1, 1))], [cau_fila(1)])
        self.assertTrue(any("anterior a hoy" in t for _, t in inf.alertas))

    def test_fecha_citacion_por_bloque_de_folios(self):
        ing = [ing_fila(n) for n in (1, 50, 99)]
        nom, inf = self._armar(ing, [cau_fila(n) for n in (1, 50, 99)],
                               citaciones_por_bloque=[(127001, 127050, date(2026, 12, 14)), (127051, 127060, date(2026, 12, 15))])
        self.assertEqual(list(nom["FECHA CITACIÓN"]), [date(2026, 12, 14), date(2026, 12, 14), date(2026, 10, 1)])
        self.assertTrue(any("ningún bloque" in t for _, t in inf.alertas))

    def test_patente_con_varios_rut_se_alerta(self):
        _, inf = self._armar([ing_fila(1), ing_fila(2, rut="22222222-2")],
                             [cau_fila(1), cau_fila(2, rut="22222222-2")])
        self.assertTrue(any("más de un RUT" in t and "una carta por RUT" in t for _, t in inf.alertas))

    def test_columnas_faltantes_dan_error_claro(self):
        with self.assertRaises(ReporteInvalido) as cm:
            self._armar([{"CAUSA": "x"}], [cau_fila(1)])
        self.assertIn("ingresos diarios", str(cm.exception))
        self.assertIn("PLACA", str(cm.exception))

    def test_el_motor_lee_la_nomina_y_agrupa_por_patente(self):
        ing = [ing_fila(1), ing_fila(2), ing_fila(3, patente="BBBB22", rut="33333333-3")]
        cau = [cau_fila(1), cau_fila(2), cau_fila(3, rut="33333333-3", nombre="LOPEZ ANA")]
        nom, inf = self._armar(ing, cau)
        ruta = self.dir / "nomina.xlsx"
        escribir_nomina(nom, ruta)
        escribir_informe(inf, self.dir / "informe.xlsx")
        df = leer_nomina(ruta)
        self.assertEqual(df.attrs["avisos_lectura"], {})
        limpio, dup = detectar_duplicados(df)
        grupos, _, _ = construir_grupos(limpio, [], 1)
        self.assertEqual(sorted(grupos), ["AAAA11", "BBBB22"])
        self.assertEqual(len(grupos["AAAA11"]["causas"]), 2)
        g = grupos["AAAA11"]
        self.assertEqual((g["nombre"], g["rut"], g["domicilio"], g["comuna"]), ("PEREZ SOTO JUAN", "11111111-1", "CALLE 1", "MAIPU"))
        self.assertEqual(g["citacion"], "01/10/2026")
        self.assertEqual((g["anio"], g["mes"], g["dia"]), (2026, 4, 6))
        self.assertEqual(g["causas"][0]["rol_raw"], "2026-Y-127001")
        self.assertEqual(g["causas"][0]["lugar"], "Pórtico 1")


if __name__ == "__main__":
    unittest.main()
