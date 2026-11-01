"""
Nómina con la forma de la que entregó el 3er JPL (31-oct-2026): encabezados
mezclados («ROL», «rut_infractor», «patente», «disposicion»…), RUT con
dígito verificador en la misma celda, espacios y espacios pegados, fechas de
infracción en texto y en formato de fecha. Los datos son inventados.
"""

import io
import unittest
from datetime import datetime

import pandas as pd

from motor.agrupacion import construir_grupos
from motor.cartas import formatear_rol
from motor.columnas import columna_rol
from motor.lectura import detectar_duplicados, leer_nomina
from motor.plantilla import construir_contexto

ENCABEZADOS = [
    "ROL", "NRO PARTE", "OFICINA REMISORA", "DESCRIPCIÓN CAUSA ", "disposicion", "lugar",
    "FECHA PROCESO", "FECHA CITACIÓN", "ESTADO CAUSA", "FECHA TRÁMITE", "TRÁMITE",
    "rut_infractor", "nombre_infractor", "FECHA INFRACCIÓN", "HORA INFRACCIÓN", "patente",
    "vehiculo", "MARCA DEL VEHÍCULO", "MODELO DEL VEHÍCULO", "domicilio_infractor", "comuna_infractor",
]


def _fila(rol, parte, patente, fecha, rut=" 11111111-1", modelo="SAIL 1.5"):
    return [rol, parte, "MINISTERIO DE OBRAS PUBLICAS", "Infraccion al Art. 114", "Infraccion al Art. 114 de la Ley N. 18.290",
            "Pórtico cl08x111", datetime(2026, 5, 10), datetime(2026, 8, 24), "EN TRAMITACIÒN", datetime(2026, 5, 10),
            "CARGA ARCHIVO TAG", rut, "NOMBRE PRUEBA", fecha, "00:01", patente, "SEDAN", "CHEVROLET", modelo,
            " CALLE 1", "MACUL"]


def _leer(filas):
    buffer = io.BytesIO()
    pd.DataFrame(filas, columns=ENCABEZADOS).to_excel(buffer, index=False)
    buffer.seek(0)
    return leer_nomina(buffer)


class NominaJpl3Test(unittest.TestCase):
    def setUp(self):
        self.df = _leer([
            _fila("13Y95046/26", "1772043-MVN", " JKDX72 ", "21/11/2026"),
            _fila("13Y97421/26", "1759157-MVN", " JKDX72 ", datetime(2026, 11, 22)),
            _fila("13Y124375/26", "1257882-M78", "KFPZ90", "23/11/2026"),
            _fila("13Y129033/26", "1257882-M78", "JCJS90", "23/11/2026"),
        ])

    def test_reconoce_la_columna_rol_y_no_avisa(self):
        self.assertEqual(columna_rol(self.df), "ROL")
        self.assertEqual(self.df.attrs["avisos_lectura"], {})

    def test_el_n_de_parte_repetido_se_excluye_y_el_resto_se_agrupa_por_patente(self):
        limpio, duplicados = detectar_duplicados(self.df)
        self.assertEqual(sorted(duplicados["ROL"]), ["13Y124375/26", "13Y129033/26"])
        grupos, _, _ = construir_grupos(limpio, [], utm_multa_defecto=1)
        self.assertEqual(list(grupos), ["JKDX72"])
        causas = grupos["JKDX72"]["causas"]
        self.assertEqual([c["rol_raw"] for c in causas], ["13Y95046/26", "13Y97421/26"])
        self.assertEqual([c["fecha"] for c in causas], ["21/11/2026", "22/11/2026"])

    def test_los_datos_salen_limpios(self):
        limpio, _ = detectar_duplicados(self.df)
        grupos, _, _ = construir_grupos(limpio, [], utm_multa_defecto=1)
        g = grupos["JKDX72"]
        self.assertEqual(g["rut"], "11111111-1")
        self.assertEqual(g["domicilio"], "CALLE 1")
        self.assertEqual(g["vehiculo"], "CHEVROLET, SAIL 1.5")

    def test_la_tabla_de_la_carta_imprime_el_rol_de_la_nomina(self):
        limpio, _ = detectar_duplicados(self.df)
        grupos, _, _ = construir_grupos(limpio, [], utm_multa_defecto=1)
        info = grupos["JKDX72"]
        pg = {"patente": "JKDX72", "info": info, "chunk": info["causas"], "parte": 1, "total_partes": 1,
              "total_causas": 2}
        ctx = construir_contexto(pg, ("", ""), 1, {"utm_valor": 68166, "nombre_juzgado": "TERCER JPL"})
        self.assertEqual([c["rol"] for c in ctx["causas"]], ["13Y95046/26", "13Y97421/26"])

    def test_rolmop_no_reemplaza_al_rol(self):
        df = self.df.rename(columns={"ROL": "ROLMOP"})
        self.assertIsNone(columna_rol(df))
        self.assertEqual(formatear_rol(""), "PENDIENTE")


class LimpiezaDeCeldasTest(unittest.TestCase):
    def test_get_col_quita_espacios_y_saltos_pegados_a_la_celda(self):
        # En el archivo real las celdas traen «\r» al final (p. ej. «JKDX72\r»).
        from motor.columnas import get_col
        fila = pd.Series({"patente": " JKDX72\r", "rut_infractor": " 77636200-K\r"})
        self.assertEqual(get_col(fila, "PATENTE"), "JKDX72")
        self.assertEqual(get_col(fila, "RUT"), "77636200-K")
