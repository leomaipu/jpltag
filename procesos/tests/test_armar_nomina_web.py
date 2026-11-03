"""La sección web «Arma tu nómina»: dos reportes de SMC → nómina descargable
y utilizable para generar las cartas."""

import io
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from motor.tests.test_armar_nomina import cau_fila, ing_fila
from nucleo.models import Rol
from procesos.models import GeneracionCartas

from .fixtures import CAMPOS_REG_Y_AUDIENCIA, crear_juzgado, crear_usuario, ingresar

_TMP = tempfile.mkdtemp(prefix="jpltag-pruebas-armar-")
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def excel(filas, nombre="r.xlsx"):
    from motor.armar_nomina import _todo_como_texto
    buf = io.BytesIO()
    # Como los reportes reales de SMC: el texto que empieza con «=» es texto, no fórmula.
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        pd.DataFrame(filas).to_excel(w, index=False)
        _todo_como_texto(w.book)
    return SimpleUploadedFile(nombre, buf.getvalue(), content_type=XLSX)


@override_settings(SALIDAS_ROOT=_TMP)
class ArmarNominaWebTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.j1 = crear_juzgado(slug="jpl1")
        self.j2 = crear_juzgado(slug="jpl2", nombre="2do Juzgado", dia_desde=11, dia_hasta=20)
        crear_usuario("sec1", rol=Rol.SECRETARIO, juzgado=self.j1)
        ingresar(self.client, "sec1")
        self.url = reverse("procesos:armar_nomina")

    def _reportes(self, n=3):
        ing = [ing_fila(i, patente=f"PAT{i:03d}", rut=f"1111111{i}-1") for i in range(1, n + 1)]
        ing.append(ing_fila(90, estado="PAGADO Y ARCHIVADO"))
        cau = [cau_fila(i, rut=f"1111111{i}-1", nombre=f"PEREZ {i}") for i in range(1, n + 1)] + [cau_fila(90)]
        return ing, cau

    def _armar(self, ing=None, cau=None, **extra):
        if ing is None:
            ing, cau = self._reportes()
        return self.client.post(self.url, {"ingresos": excel(ing), "causas": excel(cau), **extra}, follow=True)

    def _token(self, resp):
        return resp.context["resultado"]["token"]

    def test_la_pantalla_pide_los_dos_reportes_y_esta_en_el_menu(self):
        resp = self.client.get(self.url)
        self.assertContains(resp, "Listado de ingresos diarios")
        self.assertContains(resp, "Listado de causas por estado")
        self.assertContains(resp, f'href="{self.url}"')

    def test_arma_la_nomina_y_muestra_el_resumen(self):
        resp = self._armar()
        self.assertEqual(resp.status_code, 200)
        r = resp.context["resultado"]
        self.assertEqual((r["n_filas"], r["n_patentes"], r["n_excluidas"]), (3, 3, 1))
        self.assertContains(resp, "Generar cartas con esta nómina")
        self.assertContains(resp, "Descargar la nómina")

    def test_la_nomina_descargada_es_la_consolidada(self):
        resp = self._armar()
        d = self.client.get(reverse("procesos:descargar_nomina_armada", args=["nomina", self._token(resp)]) + "?juzgado=jpl1")
        self.assertEqual(d.status_code, 200)
        self.assertIn("Nomina_consolidada.xlsx", d["Content-Disposition"])
        df = pd.read_excel(io.BytesIO(b"".join(d.streaming_content)), dtype=object)
        self.assertEqual(sorted(df["patente"]), ["PAT001", "PAT002", "PAT003"])
        i = self.client.get(reverse("procesos:descargar_nomina_armada", args=["informe", self._token(resp)]) + "?juzgado=jpl1")
        self.assertEqual(i.status_code, 200)
        hojas = pd.ExcelFile(io.BytesIO(b"".join(i.streaming_content))).sheet_names
        self.assertIn("Excluidas", hojas)

    def test_los_reportes_de_smc_no_quedan_guardados(self):
        from django.core import signing
        from procesos.generacion import _SAL_NOMINA_GUARDADA
        carpeta = Path(_TMP) / signing.loads(self._token(self._armar()), salt=_SAL_NOMINA_GUARDADA)["c"]
        self.assertEqual({p.name for p in carpeta.iterdir()}, {"nomina_original.xlsx", "Informe_consolidacion.xlsx", "resultado.json"})

    def test_archivos_cambiados_de_lugar_explican_que_falta(self):
        ing, cau = self._reportes()
        resp = self._armar(ing=cau, cau=ing)
        self.assertContains(resp, "faltan las columnas")
        self.assertContains(resp, "Cambiaste el orden")
        self.assertIsNone(resp.context["resultado"])

    def test_archivo_que_no_es_excel(self):
        ing, cau = self._reportes()
        for nombre in ("a.csv", "a.xls"):
            resp = self.client.post(self.url, {"ingresos": SimpleUploadedFile(nombre, b"x"), "causas": excel(cau)})
            self.assertContains(resp, "debe ser un Excel")

    def test_falta_un_reporte(self):
        resp = self.client.post(self.url, {"ingresos": excel(self._reportes()[0])})
        self.assertContains(resp, "Elige el Excel del Listado de causas por estado")

    def test_excel_danado_no_da_error_500(self):
        resp = self.client.post(self.url, {"ingresos": SimpleUploadedFile("a.xlsx", b"no es excel"), "causas": SimpleUploadedFile("b.xlsx", b"no")})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No se pudieron leer los reportes")

    def test_sin_causas_pendientes_no_arma_nada_y_no_deja_carpetas(self):
        antes = len(list((Path(_TMP) / "jpl1").glob("*"))) if (Path(_TMP) / "jpl1").exists() else 0
        resp = self._armar([ing_fila(1, estado="PAGADO Y ARCHIVADO")], [cau_fila(1)])
        self.assertContains(resp, "no quedó ninguna causa para notificar")
        despues = len(list((Path(_TMP) / "jpl1").glob("*"))) if (Path(_TMP) / "jpl1").exists() else 0
        self.assertEqual(antes, despues)

    def test_la_descarga_es_solo_de_quien_armo_la_nomina(self):
        token = self._token(self._armar())
        crear_usuario("sec1b", rol=Rol.SECRETARIO, juzgado=self.j1)
        self.client.logout()
        ingresar(self.client, "sec1b")
        d = self.client.get(reverse("procesos:descargar_nomina_armada", args=["nomina", token]) + "?juzgado=jpl1")
        self.assertEqual(d.status_code, 404)

    def test_token_adulterado_o_archivo_invalido(self):
        token = self._token(self._armar())
        for que, tk in (("nomina", token + "x"), ("otro", token), ("%2e%2e", token), ("nomina", "basura")):
            d = self.client.get(f"/armar-nomina/descargar/{que}/{tk}/?juzgado=jpl1")
            self.assertEqual(d.status_code, 404, que)

    def test_el_juez_no_entra(self):
        crear_usuario("juez1", rol=Rol.JUEZ, juzgado=self.j1)
        self.client.logout()
        ingresar(self.client, "juez1")
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_sin_sesion_pide_ingresar(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    # ── de la nómina armada a las cartas ──────────────────────────────────

    def test_la_nomina_armada_se_usa_para_generar_las_cartas(self):
        token = self._token(self._armar())
        pantalla = self.client.get(reverse("procesos:subir_nomina") + f"?juzgado=jpl1&nomina_armada={token}")
        self.assertContains(pantalla, "Lista para usar")
        self.assertContains(pantalla, 'name="nomina_guardada"')
        resp = self.client.post(reverse("procesos:subir_nomina"), {
            "juzgado": "jpl1", "nomina_guardada": token, "tipo_carta": "CITACION", "nombre_firmante": "Ana",
            "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA,
        }, follow=True)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK, corrida.mensaje_error if hasattr(corrida, "mensaje_error") else "")
        self.assertEqual(corrida.total_patentes, 3)
        self.assertEqual(resp.status_code, 200)

    def test_nomina_armada_vencida_o_ajena_avisa(self):
        resp = self.client.get(reverse("procesos:subir_nomina") + "?juzgado=jpl1&nomina_armada=basura", follow=True)
        self.assertContains(resp, "ya no está disponible")
        self.assertNotContains(resp, "Lista para usar")

    def test_la_nomina_armada_no_sirve_en_otro_juzgado(self):
        crear_usuario("adm", rol=Rol.ADMINISTRADOR)
        self.client.logout()
        ingresar(self.client, "adm")
        token = self._token(self.client.post(self.url + "?juzgado=jpl1", {"juzgado": "jpl1", "ingresos": excel(self._reportes()[0]), "causas": excel(self._reportes()[1])}, follow=True))
        resp = self.client.get(reverse("procesos:subir_nomina") + f"?juzgado=jpl2&nomina_armada={token}", follow=True)
        self.assertNotContains(resp, "Lista para usar")

    def test_actualizar_la_pagina_no_arma_otra_nomina(self):
        resp = self._armar()
        self.assertEqual(len(resp.redirect_chain), 1)
        carpetas = len(list((Path(_TMP) / "jpl1").glob("*")))
        self.client.get(resp.redirect_chain[0][0])
        self.client.get(resp.redirect_chain[0][0])
        self.assertEqual(len(list((Path(_TMP) / "jpl1").glob("*"))), carpetas)
        self.assertContains(self.client.get(resp.redirect_chain[0][0]), "Generar cartas con esta nómina")

    def test_resultado_con_token_ajeno_o_vencido_avisa(self):
        resp = self.client.get(self.url + "?juzgado=jpl1&nomina=basura", follow=True)
        self.assertContains(resp, "ya no está disponible")

    def test_texto_que_empieza_con_igual_no_se_vuelve_formula(self):
        from openpyxl import load_workbook
        ing, cau = self._reportes(1)
        ing[0]["PLACA"] = " =1+1_x000D_"
        cau[0]["nombre"] = "=HYPERLINK(\"http://x\",\"A\")"
        cau[0]["direccion"] = "=cmd|' /C calc'!A0"
        resp = self._armar(ing, cau)
        for que in ("nomina", "informe"):
            d = self.client.get(reverse("procesos:descargar_nomina_armada", args=[que, self._token(resp)]) + "?juzgado=jpl1")
            wb = load_workbook(io.BytesIO(b"".join(d.streaming_content)))
            tipos = {c.data_type for ws in wb for fila in ws.iter_rows() for c in fila if isinstance(c.value, str) and c.value.startswith("=")}
            self.assertLessEqual(tipos, {"s"}, que)
        df = pd.read_excel(io.BytesIO(b"".join(self.client.get(reverse("procesos:descargar_nomina_armada", args=["nomina", self._token(resp)]) + "?juzgado=jpl1").streaming_content)), dtype=object)
        self.assertEqual(df.iloc[0]["patente"], "=1+1")
        self.assertTrue(str(df.iloc[0]["nombre_infractor"]).startswith("=HYPERLINK"))

    def test_nomina_armada_vencida_en_el_post_no_dice_lista_para_usar(self):
        token = self._token(self._armar())
        shutil.rmtree(Path(_TMP) / __import__("json").loads(__import__("base64").urlsafe_b64decode(token.split(":")[0] + "=="))["c"])
        resp = self.client.post(reverse("procesos:subir_nomina"), {
            "juzgado": "jpl1", "nomina_guardada": token, "tipo_carta": "CITACION", "nombre_firmante": "Ana",
            "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50, **CAMPOS_REG_Y_AUDIENCIA,
        })
        self.assertContains(resp, "ya no está disponible")
        self.assertNotContains(resp, "Lista para usar")
