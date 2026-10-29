"""
Los tres tipos de carta (citación / citación y sentencia / sentencia) de
punta a punta por la web: el formulario, qué plantilla se usa, que la
sentencia no gaste cupos de agenda, y la descarga de plantillas con el
mismo aislamiento por Juzgado que el resto de la app. Datos sintéticos.
"""

import io
import shutil
import tempfile
from datetime import date, time, timedelta

from django.test import TestCase, override_settings
from docx import Document

from motor.plantilla import texto_de
from nucleo.models import BloqueHorario, Rol

from .fixtures import (
    construir_nomina_xlsx,
    construir_plantilla_valida,
    crear_juzgado,
    crear_usuario,
    fila_nomina,
    ingresar,
)

_TMP_MEDIA = tempfile.mkdtemp(prefix="jpltag-pruebas-media-tipos-")
_TMP_SALIDAS = tempfile.mkdtemp(prefix="jpltag-pruebas-salidas-tipos-")


def _texto_descargado(client, archivo):
    resp = client.get(f"/descargar/{archivo.pk}/")
    assert resp.status_code == 200
    return texto_de(Document(io.BytesIO(b"".join(resp.streaming_content))))


@override_settings(MEDIA_ROOT=_TMP_MEDIA, SALIDAS_ROOT=_TMP_SALIDAS, JPLTAG_AGENDA_ACTIVA=True)
class TiposDeCartaTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.juzgado = crear_juzgado()  # slug jpl1 -> plantillas por defecto del 1er JPL
        crear_usuario("operador", juzgado=self.juzgado)
        ingresar(self.client, "operador")
        self.bloque = BloqueHorario.objects.create(
            juzgado=self.juzgado, fecha=date.today() + timedelta(days=10),
            hora=time(9, 30), cupos_totales=5,
        )

    def _subir(self, tipo, **extra):
        datos = {
            "nomina": construir_nomina_xlsx([
                fila_nomina(pat="AAAA11", nro_parte="1001", fecha="03/09/2026"),
                fila_nomina(pat="AAAA11", nro_parte="1002", fecha="04/09/2026"),
            ]),
            "tipo_carta": tipo,
            "nombre_firmante": "Secretaria de Prueba",
            "cargo_firma": "SECRETARIA ABOGADA",
            "n_cartas_paquete": 0,
        }
        datos.update(extra)
        return self.client.post("/subir/", datos, follow=True)

    def _cartas(self):
        from procesos.models import ArchivoGenerado, GeneracionCartas
        gen = GeneracionCartas.objects.get(juzgado=self.juzgado)
        return gen, gen.archivos.get(tipo=ArchivoGenerado.Tipo.CARTAS)

    def test_citacion_asigna_fecha_numerica_de_la_agenda_y_gasta_cupo(self):
        self._subir("CITACION")
        gen, cartas = self._cartas()
        self.assertEqual(gen.tipo_carta, "CITACION")
        t = _texto_descargado(self.client, cartas)
        self.assertIn(f"el día {self.bloque.fecha:%d/%m/%Y}, a las 09:30 hrs.", t)
        self.assertIn("CÍTESE", t)
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 1)

    def test_citacion_y_sentencia_trae_el_aviso_de_condena(self):
        self._subir("CITACION_SENTENCIA")
        gen, cartas = self._cartas()
        self.assertEqual(gen.tipo_carta, "CITACION_SENTENCIA")
        t = _texto_descargado(self.client, cartas)
        self.assertIn("será CONDENADO(A) al pago de 1 UTM por infracción", t)

    def test_sentencia_exige_la_fecha_de_la_sentencia(self):
        resp = self._subir("SENTENCIA")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Indica la fecha de la sentencia")
        from procesos.models import GeneracionCartas
        self.assertEqual(GeneracionCartas.objects.count(), 0)

    def test_sentencia_usa_su_fecha_y_no_toca_la_agenda(self):
        self._subir("SENTENCIA", fecha_audiencia="2026-10-02")
        gen, cartas = self._cartas()
        self.assertEqual(gen.tipo_carta, "SENTENCIA")
        self.assertEqual(gen.fecha_sentencia, date(2026, 10, 2))
        self.assertEqual(gen.total_sin_cupo_agenda, 0)
        t = _texto_descargado(self.client, cartas)
        self.assertIn("NOTIFÍQUESE la SENTENCIA", t)
        self.assertIn("con fecha 02/10/2026", t)
        self.bloque.refresh_from_db()
        self.assertEqual(self.bloque.cupos_usados, 0)

    def test_sentencia_sin_agenda_cargada_igual_genera(self):
        BloqueHorario.objects.all().delete()
        self._subir("SENTENCIA", fecha_audiencia="2026-10-02")
        gen, _ = self._cartas()
        self.assertEqual(gen.estado, "OK")

    def test_el_seguimiento_registra_el_tipo_de_carta(self):
        import pandas as pd
        from procesos.models import ArchivoGenerado
        self._subir("CITACION")
        gen, _ = self._cartas()
        seg = gen.archivos.get(tipo=ArchivoGenerado.Tipo.SEGUIMIENTO)
        df = pd.read_excel(f"{_TMP_SALIDAS}/{seg.ruta}")
        self.assertEqual(set(df["Tipo de carta"]), {"CITACION"})

    def test_juzgado_sin_plantilla_posible_falla_con_mensaje_claro(self):
        self.juzgado.delete()
        otro = crear_juzgado(slug="especial", nombre="Juzgado Especial")
        crear_usuario("op2", juzgado=otro)
        self.client.logout()
        ingresar(self.client, "op2")
        self._subir("CITACION")
        from procesos.models import GeneracionCartas
        gen = GeneracionCartas.objects.get(juzgado=otro)
        self.assertEqual(gen.estado, "FALLIDO")
        self.assertIn("No hay plantilla de CITACION", gen.mensaje_error)

    def test_rechaza_cargo_inventado(self):
        resp = self._subir("CITACION", cargo_firma="PRESIDENTE")
        from procesos.models import GeneracionCartas
        self.assertEqual(GeneracionCartas.objects.count(), 0)
        self.assertEqual(resp.status_code, 200)


@override_settings(MEDIA_ROOT=_TMP_MEDIA, SALIDAS_ROOT=_TMP_SALIDAS)
class PlantillasPorTipoTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_MEDIA, ignore_errors=True)
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.juzgado = crear_juzgado()
        crear_usuario("secretaria", rol=Rol.SECRETARIO, juzgado=self.juzgado)
        ingresar(self.client, "secretaria")

    def test_cada_plantilla_se_guarda_en_su_campo(self):
        self.client.post("/plantilla/", {
            "juzgado": self.juzgado.slug,
            "plantilla_sentencia": construir_plantilla_valida("SENT", tipo="SENTENCIA"),
            "plantilla_citacion_sentencia": construir_plantilla_valida("CYS", tipo="CITACION_SENTENCIA"),
        })
        self.juzgado.refresh_from_db()
        self.assertTrue(self.juzgado.plantilla_sentencia)
        self.assertTrue(self.juzgado.plantilla_citacion_sentencia)
        self.assertFalse(self.juzgado.plantilla_citacion)

    def test_una_plantilla_de_citacion_no_vale_como_sentencia(self):
        resp = self.client.post("/plantilla/", {
            "juzgado": self.juzgado.slug,
            "plantilla_sentencia": construir_plantilla_valida("X", tipo="CITACION"),
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "A la plantilla le falta")
        self.juzgado.refresh_from_db()
        self.assertFalse(self.juzgado.plantilla_sentencia)

    def test_la_plantilla_propia_de_sentencia_se_usa_al_generar(self):
        self.client.post("/plantilla/", {
            "juzgado": self.juzgado.slug,
            "plantilla_sentencia": construir_plantilla_valida("SENTENCIA PROPIA XYZ", tipo="SENTENCIA"),
        })
        self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx([fila_nomina(pat="AAAA11", fecha="03/09/2026")]),
            "tipo_carta": "SENTENCIA", "fecha_audiencia": "2026-10-02",
            "nombre_firmante": "X", "cargo_firma": "SECRETARIO", "n_cartas_paquete": 0,
        })
        from procesos.models import ArchivoGenerado, GeneracionCartas
        gen = GeneracionCartas.objects.get(juzgado=self.juzgado)
        cartas = gen.archivos.get(tipo=ArchivoGenerado.Tipo.CARTAS)
        self.assertIn("SENTENCIA PROPIA XYZ", _texto_descargado(self.client, cartas))

    def test_descarga_la_plantilla_por_defecto_del_juzgado(self):
        for tipo in ("CITACION", "CITACION_SENTENCIA", "SENTENCIA"):
            resp = self.client.get(f"/plantilla/descargar/{self.juzgado.pk}/{tipo}/")
            self.assertEqual(resp.status_code, 200, tipo)
            self.assertIn("attachment", resp["Content-Disposition"])
            doc = Document(io.BytesIO(b"".join(resp.streaming_content)))
            self.assertIn("{{ nombre_infractor }}", texto_de(doc))

    def test_descargar_plantilla_de_otro_juzgado_da_403(self):
        otro = crear_juzgado(slug="jpl2", nombre="2do Juzgado")
        resp = self.client.get(f"/plantilla/descargar/{otro.pk}/CITACION/")
        self.assertEqual(resp.status_code, 403)

    def test_tipo_desconocido_da_404(self):
        resp = self.client.get(f"/plantilla/descargar/{self.juzgado.pk}/OTRA/")
        self.assertEqual(resp.status_code, 404)

    def test_juez_no_puede_descargar_plantillas(self):
        crear_usuario("jueza", rol=Rol.JUEZ, juzgado=self.juzgado)
        self.client.logout()
        ingresar(self.client, "jueza")
        resp = self.client.get(f"/plantilla/descargar/{self.juzgado.pk}/CITACION/")
        self.assertEqual(resp.status_code, 403)

    def test_la_pantalla_muestra_las_tres_plantillas(self):
        resp = self.client.get("/plantilla/")
        for tipo in ("CITACION", "CITACION_SENTENCIA", "SENTENCIA"):
            self.assertContains(resp, f"/plantilla/descargar/{self.juzgado.pk}/{tipo}/")
            self.assertContains(resp, f'name="plantilla_{tipo.lower()}"')
        self.assertContains(resp, "Por defecto")


class CapacidadPorPaginaTest(TestCase):
    def test_el_valor_por_defecto_es_12(self):
        """Solo fija el valor. Que 12 filas quepan en una hoja con firma y
        sello se midió a mano, abriendo las plantillas por defecto en Word
        con datos reales (ver el comentario de Juzgado.max_causas_por_pagina):
        aquí no hay cómo paginar un .docx. Si se cambian las plantillas, hay
        que volver a medirlo."""
        from nucleo.models import Juzgado
        self.assertEqual(Juzgado._meta.get_field("max_causas_por_pagina").default, 12)
        self.assertEqual(crear_juzgado().max_causas_por_pagina, 12)
