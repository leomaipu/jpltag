"""
La nómina que deja cleandata/ajustar_jpltag.py (los Excel de SMC limpios)
se lee sin adaptadores y llena las cartas del 1er JPL sin huecos: nombre con
sus dos apellidos, disposición sin el « - ID:…» de SMC, fecha y hora de
infracción con el formato de la carta y la fecha de citación sin la hora
00:00 que SMC deja cuando no hay hora. Datos SINTÉTICOS.
"""

import io
import tempfile
import unittest

import pandas as pd
from docx import Document
from docx.oxml.ns import qn

from motor.agrupacion import construir_grupos, expandir_en_paginas
from motor.cartas import fecha_en_palabras
from motor.columnas import fecha_para_carta, hora_para_carta, normalizar_hora, sin_id_smc
from motor.lectura import leer_nomina
from motor.plantilla import (
    combinar_cartas,
    construir_contexto,
    generar_cartas_certificadas,
    renderizar_carta,
    resolver_plantilla,
    texto_de,
    validar_plantilla,
)

# Encabezados exactos de <nombre>_jpltag.xlsx (ajustar_jpltag.py, nov-2026).
COLUMNAS = [
    "ROL", "NRO PARTE", "OF EMISORA", "DESCRIPCION_CAUSA", "LUGAR INFRACCION", "FECHA_PROCESO",
    "HORA_PROCESO", "FECHA_CITACION", "HORA_CITACION", "FECHA INFRACCIÓN", "HORA INFRACCIÓN",
    "PATENTE", "MARCA", "MODELO", "RUT", "DV", "NOMBRE INFRACTOR", "APELLIDO PATERNO",
    "APELLIDO MATERNO", "DOMICILIO INFRACTOR", "COMUNA", "ULTIMO_TRAMITE", "FECHA_TRAMITE",
    "HORA_TRAMITE", "COD_ESTADO", "ÚLTIMO ESTADO", "ÚLTIMO TRAMITE",
]


def _fila(i, patente="ZZZZ99", rut=99999999, dv="9", nombre="MARIA JOSE", paterno="PRUEBA",
          materno="SINTETICA", cita="2026-12-15", hora_cita="00:00:00"):
    return {
        "ROL": 7000000 + i, "NRO PARTE": f"{1700000 + i}-MVN", "OF EMISORA": "MINISTERIO DE OBRAS PUBLICAS",
        "DESCRIPCION_CAUSA": f"Infraccion al Art. 114 de la Ley N. 18.290 del Transito. - ID:{15000000 + i}",
        "LUGAR INFRACCION": f"Pórtico sintético {i}, Autopista de Prueba", "FECHA_PROCESO": "2026-03-01",
        "HORA_PROCESO": "10:00:00", "FECHA_CITACION": cita, "HORA_CITACION": hora_cita,
        "FECHA INFRACCIÓN": f"2026-02-{i + 1:02d}", "HORA INFRACCIÓN": "06:41:00",
        "PATENTE": patente, "MARCA": "MARCA X", "MODELO": "6 V 2.0", "RUT": rut, "DV": dv,
        "NOMBRE INFRACTOR": nombre, "APELLIDO PATERNO": paterno, "APELLIDO MATERNO": materno,
        "DOMICILIO INFRACTOR": "CALLE INVENTADA 123", "COMUNA": "MAIPÚ", "ULTIMO_TRAMITE": 2,
        "FECHA_TRAMITE": "2026-03-02", "HORA_TRAMITE": "09:00:00", "COD_ESTADO": 158,
        "ÚLTIMO ESTADO": "EN TRAMITACIÓN", "ÚLTIMO TRAMITE": "CITACION",
    }


def _nomina(filas):
    """La nómina como la lee el motor: escrita a .xlsx y vuelta a leer."""
    buf = io.BytesIO()
    pd.DataFrame(filas, columns=COLUMNAS).to_excel(buf, index=False)
    buf.seek(0)
    return leer_nomina(buf)


def _config(tipo, **extra):
    return {"tipo_carta": tipo, "slug_juzgado": "jpl1", "nombre_juzgado": "PRIMER JUZGADO DE POLICÍA LOCAL",
            "utm_valor": 69000, "max_causas_por_pagina": 12, "nombre_firmante": "ANA FIRMANTE",
            "cargo_firma": "SECRETARIA ABOGADA", "fecha_audiencia": "02/12/2026", **extra}


class AyudantesTest(unittest.TestCase):
    def test_sin_id_smc(self):
        self.assertEqual(sin_id_smc("Infraccion al Art. 114 de la Ley N. 18.290 del Transito. - ID:15022921"),
                         "Infraccion al Art. 114 de la Ley N. 18.290 del Transito.")
        self.assertEqual(sin_id_smc("TAG no vigente"), "TAG no vigente")

    def test_normalizar_hora_descarta_la_medianoche_de_relleno(self):
        self.assertEqual(normalizar_hora("00:00:00"), "")
        self.assertEqual(normalizar_hora("00:00"), "")
        self.assertEqual(normalizar_hora("9:30:00"), "09:30")
        self.assertEqual(normalizar_hora("10:15"), "10:15")
        for malo in ("", None, "25:00", "mañana", "12:61"):
            self.assertEqual(normalizar_hora(malo), "")

    def test_hora_para_carta_conserva_la_medianoche(self):
        self.assertEqual(hora_para_carta("06:41:00"), "06:41")
        self.assertEqual(hora_para_carta("00:00:00"), "00:00")
        self.assertEqual(hora_para_carta("08:30"), "08:30")
        self.assertEqual(hora_para_carta("s/i"), "s/i")

    def test_fecha_para_carta(self):
        self.assertEqual(fecha_para_carta("2026-02-06"), "06/02/2026")
        self.assertEqual(fecha_para_carta("06/02/2026"), "06/02/2026")
        self.assertEqual(fecha_para_carta(""), "")
        self.assertEqual(fecha_para_carta("sin fecha"), "sin fecha")

    def test_fecha_en_palabras(self):
        self.assertEqual(fecha_en_palabras("02/12/2026"), "2 de diciembre de 2026")
        self.assertEqual(fecha_en_palabras("A COORDINAR CON EL JUZGADO"), "")
        self.assertEqual(fecha_en_palabras(""), "")


class NominaAjustadaTest(unittest.TestCase):
    def test_grupo_con_todos_los_datos_de_la_carta(self):
        g, _, avisos = construir_grupos(_nomina([_fila(0), _fila(1)]), [], 1)
        info = g["ZZZZ99"]
        self.assertEqual(info["nombre"], "MARIA JOSE PRUEBA SINTETICA")
        self.assertEqual(info["rut"], "99999999-9")
        self.assertEqual(info["domicilio"], "CALLE INVENTADA 123")
        self.assertEqual(info["comuna"], "MAIPÚ")
        self.assertEqual(info["vehiculo"], "MARCA X, 6 V 2.0")
        causa = info["causas"][0]
        self.assertEqual(causa["rol_raw"], "7000000")
        self.assertEqual(causa["nroParte"], "1700000-MVN")
        self.assertEqual(causa["disposicion"], "Infraccion al Art. 114 de la Ley N. 18.290 del Transito.")
        self.assertEqual(causa["fecha"], "01/02/2026")
        self.assertEqual(causa["hora"], "06:41")
        self.assertEqual(causa["lugar"], "Pórtico sintético 0, Autopista de Prueba")
        self.assertEqual(info["citacion"], "15/12/2026")
        self.assertEqual(info["citacion_hora"], "")          # 00:00:00 = sin hora
        self.assertEqual(avisos["disposiciones_sin_multa_definida"], [])

    def test_hora_de_citacion_real_se_usa(self):
        g, _, _ = construir_grupos(_nomina([_fila(0, hora_cita="10:30:00"), _fila(1, hora_cita="10:30:00")]), [], 1)
        self.assertEqual(g["ZZZZ99"]["citacion_hora"], "10:30")

    def test_horas_de_citacion_distintas_no_se_afirman(self):
        g, _, _ = construir_grupos(_nomina([_fila(0, hora_cita="10:30:00"), _fila(1, hora_cita="11:00:00")]), [], 1)
        self.assertEqual(g["ZZZZ99"]["citacion_hora"], "")

    def test_la_hora_es_la_de_la_fecha_mas_temprana(self):
        g, _, _ = construir_grupos(_nomina([_fila(0, cita="2026-12-20", hora_cita="11:00:00"),
                                            _fila(1, cita="2026-12-15", hora_cita="09:00:00")]), [], 1)
        self.assertEqual((g["ZZZZ99"]["citacion"], g["ZZZZ99"]["citacion_hora"]), ("15/12/2026", "09:00"))

    def test_persona_juridica_sin_apellidos(self):
        g, _, _ = construir_grupos(_nomina([_fila(0, rut=76123456, dv="K", nombre="TRANSPORTES PRUEBA SPA",
                                                  paterno="", materno="")]), [], 1)
        self.assertEqual(g["ZZZZ99"]["nombre"], "TRANSPORTES PRUEBA SPA")
        self.assertEqual(g["ZZZZ99"]["rut"], "76123456-K")

    def test_el_grupo_se_puede_guardar_como_json(self):
        """El trabajo en segundo plano guarda los grupos en JSON."""
        import json
        g, _, _ = construir_grupos(_nomina([_fila(0)]), [], 1)
        json.dumps(g, ensure_ascii=False)


class CartasDel1erJplTest(unittest.TestCase):
    def _texto(self, tipo, slot=("15/12/2026", "")):
        g, _, _ = construir_grupos(_nomina([_fila(i) for i in range(3)]), [], 1)
        cfg = _config(tipo)
        pg = expandir_en_paginas(g, 12)[0]
        return texto_de(renderizar_carta(resolver_plantilla(cfg, tipo), construir_contexto(pg, slot, 1, cfg), cfg))

    def test_citacion_y_sentencia(self):
        t = self._texto("CITACION_SENTENCIA")
        for dato in ("NOMBRE: MARIA JOSE PRUEBA SINTETICA", "DIRECCIÓN: CALLE INVENTADA 123", "COMUNA: MAIPÚ",
                     "RUT: 99999999-9", "Vehículo PPU: ZZZZ99",
                     "a formular descargo el día 15/12/2026, por transitar",
                     "se le CONDENA al pago de una multa de 1 UTM",
                     "7000000", "01/02/2026", "06:41", "Pórtico sintético 2, Autopista de Prueba",
                     "Infraccion al Art. 114 de la Ley N. 18.290 del Transito.", "ANA FIRMANTE"):
            self.assertIn(dato, t)
        for nada in ("{{", "{%", "ID:", "00:00", "«Fecha", "«Hora", "«Placa"):
            self.assertNotIn(nada, t)

    def test_citacion_y_sentencia_con_hora(self):
        t = self._texto("CITACION_SENTENCIA", ("15/12/2026", "10:30"))
        self.assertIn("a formular descargo el día 15/12/2026, a las 10:30 hrs., por transitar", t)

    def test_notificacion_de_sentencia(self):
        t = self._texto("SENTENCIA")
        self.assertIn("2 de diciembre de 2026", t)
        self.assertIn("NOTIFICACIÓN DE SENTENCIA", t)
        self.assertIn("Condénese al pago de una multa de 1 UTM", t)
        self.assertNotIn("15/12/2026", t)
        for nada in ("{{", "{%", "ID:", "«Placa"):
            self.assertNotIn(nada, t)

    def test_genera_el_envio_completo_y_la_patente_larga_sale_en_partes(self):
        filas = [_fila(i) for i in range(17)] + [_fila(20 + i, patente="YYYY88", rut=88888888, dv="8")
                                                for i in range(2)]
        g, _, _ = construir_grupos(_nomina(filas), [], 1)
        with tempfile.TemporaryDirectory() as d:
            for tipo in ("CITACION_SENTENCIA", "SENTENCIA"):
                res = generar_cartas_certificadas(g, f"jpl1_{tipo}", {}, 0, _config(tipo), carpeta_salida=d)
                self.assertEqual(res["errores"], [])
                t = texto_de(Document(res["archivos"][0]))
                self.assertIn("Parte 1 de 2: infracciones 1 a 12 de 17", t)
                self.assertIn("Parte 2 de 2: infracciones 13 a 17 de 17", t)
                self.assertEqual(t.count("ANA FIRMANTE"), 3)        # 2 partes + 1 carta
                self.assertEqual(len(pd.read_excel(res["seguimiento"])), 19)


class CombinarCartasTest(unittest.TestCase):
    def test_cada_carta_empieza_en_hoja_nueva_sin_parrafo_de_salto(self):
        """Un párrafo con el salto al final de una carta que llena la hoja cae
        solo en la hoja siguiente y deja una hoja en blanco."""
        cfg = _config("SENTENCIA")
        g, _, _ = construir_grupos(_nomina([_fila(0)]), [], 1)
        pg = expandir_en_paginas(g, 12)[0]
        docs = [renderizar_carta(resolver_plantilla(cfg, "SENTENCIA"), construir_contexto(pg, ("", ""), n, cfg), cfg)
                for n in (1, 2, 3)]
        cuerpo = combinar_cartas(docs).element.body
        saltos = [br for br in cuerpo.iter(qn("w:br")) if br.get(qn("w:type")) == "page"]
        self.assertEqual(saltos, [])
        marcados = [p for p in cuerpo.iter(qn("w:p")) if p.find(f"{qn('w:pPr')}/{qn('w:pageBreakBefore')}") is not None]
        self.assertEqual(len(marcados), 2)

    def test_un_salto_apagado_en_la_plantilla_se_vuelve_a_encender(self):
        from docx.oxml import OxmlElement
        def doc(texto):
            d = Document()
            p = d.add_paragraph(texto)
            salto = OxmlElement("w:pageBreakBefore")
            salto.set(qn("w:val"), "0")
            p._p.get_or_add_pPr().append(salto)
            return d
        unido = combinar_cartas([doc("uno"), doc("dos")])
        saltos = [p.find(f"{qn('w:pPr')}/{qn('w:pageBreakBefore')}") for p in unido.element.body.iter(qn("w:p"))]
        encendidos = [s for s in saltos if s is not None and s.get(qn("w:val")) is None]
        self.assertEqual(len(encendidos), 1)

    def test_carta_que_empieza_con_tabla(self):
        def doc_con_tabla(texto):
            doc = Document()
            cuerpo = doc.element.body
            for hijo in list(cuerpo):
                if hijo.tag != qn("w:sectPr"):
                    cuerpo.remove(hijo)
            doc.add_table(rows=1, cols=1).rows[0].cells[0].text = texto
            return doc
        unido = combinar_cartas([doc_con_tabla("uno"), doc_con_tabla("dos")])
        self.assertIn("dos", texto_de(unido))
        primeros = [p for p in unido.element.body.iter(qn("w:p"))
                    if p.find(f"{qn('w:pPr')}/{qn('w:pageBreakBefore')}") is not None]
        self.assertEqual(len(primeros), 1)


class NegritaDelModeloTest(unittest.TestCase):
    """Como en el modelo: fecha/hora de citación y monto de la condena en negrita."""

    def _negritas(self, tipo):
        cfg = _config(tipo)
        g, _, _ = construir_grupos(_nomina([_fila(0)]), [], 1)
        pg = expandir_en_paginas(g, 12)[0]
        doc = renderizar_carta(resolver_plantilla(cfg, tipo), construir_contexto(pg, ("15/12/2026", "10:30"), 1, cfg), cfg)
        return ["".join(r.text for r in p.runs if r.bold) for p in doc.paragraphs]

    def test_citacion_y_sentencia(self):
        negritas = " ".join(self._negritas("CITACION_SENTENCIA"))
        self.assertIn("el día 15/12/2026, a las 10:30 hrs.", negritas)
        self.assertIn("1 UTM,", negritas)

    def test_sentencia(self):
        self.assertIn("1 UTM", " ".join(self._negritas("SENTENCIA")))


class ValidacionTest(unittest.TestCase):
    def test_sentencia_acepta_la_fecha_en_palabras(self):
        doc = Document()
        doc.add_paragraph("Maipú, {{ fecha_audiencia_larga }}. {{ nombre_infractor }} {{ patente }} {{ condena_monto }}")
        tabla = doc.add_table(rows=3, cols=1)
        tabla.rows[0].cells[0].text = "{%tr for causa in causas %}"
        tabla.rows[1].cells[0].text = "{{ causa.lugar }}"
        tabla.rows[2].cells[0].text = "{%tr endfor %}"
        buf = io.BytesIO()
        doc.save(buf)
        buf.seek(0)
        self.assertEqual(validar_plantilla(buf, "SENTENCIA"), (True, ""))


if __name__ == "__main__":
    unittest.main()
