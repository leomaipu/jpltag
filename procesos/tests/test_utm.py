"""Pantalla y caso de uso de la UTM. La fuente externa se reemplaza: lo que se
prueba es que nada se guarde sin confirmación, que los estados digan la verdad y
que los permisos se cumplan."""

import http.server
import threading
from datetime import date, timedelta
from unittest.mock import patch

import sentry_sdk
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.db.models import ProtectedError
from django.db.models.fields.files import FieldFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from config.observabilidad import sin_migas_http
from motor.utm import ErrorConsultaUTM, FuenteCMF, FuenteMindicador, ValorUTM
from nucleo.models import ConflictoUTM, ConstanciaUTM, Rol
from procesos import utm, utm_grafico

from .fixtures import crear_juzgado, crear_usuario, ingresar

KEY = "clave-de-prueba-que-no-debe-filtrarse"
URL = "/plantilla/utm/"
CONFIRMAR = "/plantilla/utm/confirmar/"


def primer_dia(fecha=None):
    return (fecha or timezone.localdate()).replace(day=1)


def mes_pasado():
    return primer_dia(primer_dia() - timedelta(days=1))


def consulta_del_mes(valor=72440):
    return ValorUTM(valor, primer_dia(), "CMF")


class FuenteFalsa:
    nombre = "CMF"

    def __init__(self, consulta=None, error=None, serie=()):
        self.consulta, self.error, self._serie = consulta, error, list(serie)
        self.llamadas = 0

    def vigente(self, hoy):
        self.llamadas += 1
        if self.error:
            raise self.error
        return self.consulta

    def serie(self, hoy):
        return self._serie


class BaseUTM(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.jpl1 = crear_juzgado(utm_valor=70000)
        self.jpl2 = crear_juzgado(slug="jpl2", nombre="2do Juzgado", dia_desde=11, dia_hasta=20, utm_valor=70000)

    def usar_fuente(self, fuente):
        parche = patch("procesos.utm.fuente_configurada", return_value=fuente)
        parche.start()
        self.addCleanup(parche.stop)
        return fuente

    def secretaria(self):
        self.usuario = crear_usuario("secretaria", rol=Rol.SECRETARIO, juzgado=self.jpl1)
        ingresar(self.client, "secretaria")

    def administrador(self):
        self.usuario = crear_usuario("adm", rol=Rol.ADMINISTRADOR)
        ingresar(self.client, "adm")

    def firma(self, consulta=None, *juzgados):
        return utm.firmar(consulta or consulta_del_mes(), juzgados or [self.jpl1, self.jpl2], self.usuario)


class ConsultaTest(BaseUTM):
    def test_consultar_no_guarda_nada(self):
        self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertContains(resp, "$72.440")
        self.assertContains(resp, "¿Es correcto?")
        self.assertContains(resp, "sii.cl")
        self.jpl1.refresh_from_db()
        self.assertEqual(self.jpl1.utm_valor, 70000)
        self.assertIsNone(self.jpl1.utm_vigente_desde)
        self.assertFalse(ConstanciaUTM.objects.exists())

    def test_falla_de_la_fuente_mantiene_el_valor_y_permite_reintentar(self):
        self.usar_fuente(FuenteFalsa(error=ErrorConsultaUTM("sin conexión")))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "No se pudo consultar")
        self.assertContains(resp, "$70.000")
        self.assertContains(resp, "Reintentar")
        self.assertNotContains(resp, 'name="firma"')

    def test_el_vigente_y_sus_fallos_se_recuerdan_salvo_a_pedido(self):
        fuente = self.usar_fuente(FuenteFalsa(error=ErrorConsultaUTM("sin conexión")))
        self.secretaria()
        self.client.get(URL)
        self.client.get(URL)
        self.assertEqual(fuente.llamadas, 1)
        self.client.get(URL + "?fresco=1")
        self.assertEqual(fuente.llamadas, 2)

    def test_juez_no_puede_consultar_ni_confirmar(self):
        self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.usuario = crear_usuario("jueza", rol=Rol.JUEZ, juzgado=self.jpl1)
        ingresar(self.client, "jueza")
        self.assertEqual(self.client.get(URL).status_code, 403)
        resp = self.client.post(CONFIRMAR, {"firma": self.firma(), "juzgados": ["jpl1"]})
        self.assertEqual(resp.status_code, 403)

    def test_sin_sesion_redirige_al_ingreso(self):
        self.assertEqual(self.client.get(URL).status_code, 302)

    def test_secretaria_solo_ve_su_juzgado(self):
        self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertContains(resp, self.jpl1.nombre)
        self.assertNotContains(resp, self.jpl2.nombre)

    def test_todo_al_dia_no_ofrece_confirmar(self):
        consulta = consulta_del_mes(70000)
        self.jpl1.registrar_utm(70000, consulta.vigente_desde, "CMF", None)
        self.usar_fuente(FuenteFalsa(consulta))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertContains(resp, "Todo al día")
        self.assertNotContains(resp, "data-utm-boton")

    def test_fuente_atrasada_no_ofrece_confirmar_ni_dice_al_dia(self):
        atrasada = ValorUTM(69000, mes_pasado(), "CMF")
        self.usar_fuente(FuenteFalsa(atrasada))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertContains(resp, "aún no publica")
        self.assertNotContains(resp, "Todo al día")
        self.assertNotContains(resp, 'name="firma"')

    def test_salto_grande_avisa_y_no_marca_nada(self):
        self.usar_fuente(FuenteFalsa(consulta_del_mes(724405)))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertContains(resp, "se aleja más de un 10 %")
        self.assertNotContains(resp, " checked")

    def test_serie_entera_mal_no_tapa_el_salto_ante_lo_guardado(self):
        hoy = primer_dia()
        serie = [ValorUTM(724000, mes_pasado(), "CMF"), ValorUTM(723000, primer_dia(hoy - timedelta(days=62)), "CMF")]
        self.usar_fuente(FuenteFalsa(consulta_del_mes(726000), serie=sorted(serie, key=lambda v: v.vigente_desde)))
        self.secretaria()
        self.assertContains(self.client.get(URL), "se aleja más de un 10 %")

    def test_sin_nada_con_que_comparar_no_marca(self):
        self.jpl1.utm_valor = 0
        self.jpl1.save()
        self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.secretaria()
        self.assertNotContains(self.client.get(URL), " checked")

    def test_al_confirmar_la_pantalla_marca_una_vez_lo_recien_guardado(self):
        self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.secretaria()
        resp = self.client.post(CONFIRMAR, {"firma": self.firma(), "juzgados": ["jpl1"]}, follow=True)
        self.assertContains(resp, "utm-juzgado--recien")
        self.assertNotContains(self.client.get(URL), "utm-juzgado--recien")

    def test_consultar_de_nuevo_no_cambia_un_valor_bueno_por_un_fallo(self):
        fuente = self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.secretaria()
        self.client.get(URL)
        fuente.error = ErrorConsultaUTM("momentáneo")
        self.assertContains(self.client.get(URL + "?fresco=1"), "$72.440")

    def test_consultar_de_nuevo_tiene_un_minimo_entre_intentos(self):
        fuente = self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.secretaria()
        self.client.get(URL)
        self.client.get(URL + "?fresco=1")
        self.client.get(URL + "?fresco=1")
        self.assertEqual(fuente.llamadas, 2)

    def test_valor_normal_queda_marcado(self):
        self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        self.secretaria()
        self.assertContains(self.client.get(URL), " checked")

    def test_la_key_no_llega_al_html(self):
        with override_settings(CMF_API_KEY=KEY), patch.object(
            FuenteCMF, "vigente", side_effect=ErrorConsultaUTM("sin conexión"),
        ):
            self.secretaria()
            self.assertNotContains(self.client.get(URL), KEY)

    def test_el_error_inesperado_de_la_serie_no_rompe_la_pantalla(self):
        fuente = self.usar_fuente(FuenteFalsa(consulta_del_mes()))
        fuente.serie = lambda hoy: (_ for _ in ()).throw(ErrorConsultaUTM("basura"))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertEqual(resp.status_code, 200)
        self.assertNotContains(resp, "data-utm-serie")

    def test_serie_se_alinea_con_el_valor_consultado(self):
        hoy = primer_dia()
        serie = [ValorUTM(70000, primer_dia(hoy - timedelta(days=62)), "CMF"), ValorUTM(70100, mes_pasado(), "CMF")]
        self.usar_fuente(FuenteFalsa(consulta_del_mes(), serie=serie))
        alineada = utm.consultar_serie(consulta_del_mes())
        self.assertEqual(alineada[-1], consulta_del_mes())
        self.assertEqual(len(alineada), 3)

    def test_con_serie_muestra_escalera_y_tabla(self):
        hoy = primer_dia()
        serie = [ValorUTM(70000 + i * 100, primer_dia(hoy - timedelta(days=30 * (5 - i))), "CMF") for i in range(6)]
        self.usar_fuente(FuenteFalsa(consulta_del_mes(), serie=serie))
        self.secretaria()
        resp = self.client.get(URL)
        self.assertContains(resp, "data-utm-serie")
        self.assertContains(resp, "no parte de cero")
        self.assertContains(resp, "Ver los valores mes a mes")


class ConfirmacionTest(BaseUTM):
    def test_confirmar_guarda_y_deja_constancia(self):
        self.secretaria()
        consulta = consulta_del_mes()
        resp = self.client.post(CONFIRMAR, {"firma": self.firma(consulta), "juzgados": ["jpl1"]})
        self.assertRedirects(resp, URL, fetch_redirect_response=False)
        self.jpl1.refresh_from_db()
        self.assertEqual(self.jpl1.utm_valor, 72440)
        self.assertEqual(self.jpl1.utm_vigente_desde, consulta.vigente_desde)
        self.assertEqual(self.jpl1.utm_fuente, "CMF")
        constancia = ConstanciaUTM.objects.get()
        self.assertEqual((constancia.valor_anterior, constancia.valor_nuevo), (70000, 72440))
        self.assertEqual(constancia.nombre_usuario, "secretaria")

    def test_firma_alterada_no_guarda(self):
        self.secretaria()
        self.client.post(CONFIRMAR, {"firma": self.firma()[:-2] + "xx", "juzgados": ["jpl1"]})
        self.jpl1.refresh_from_db()
        self.assertEqual(self.jpl1.utm_valor, 70000)
        self.assertFalse(ConstanciaUTM.objects.exists())

    def test_firma_vencida_no_guarda(self):
        self.secretaria()
        firma = self.firma()
        with patch("procesos.utm.VIGENCIA_FIRMA_SEGUNDOS", -1):
            self.client.post(CONFIRMAR, {"firma": firma, "juzgados": ["jpl1"]})
        self.jpl1.refresh_from_db()
        self.assertEqual(self.jpl1.utm_valor, 70000)

    def test_firma_de_otro_mes_no_se_acepta(self):
        self.secretaria()
        for fecha in (mes_pasado(), timezone.localdate() + timedelta(days=40)):
            with self.subTest(fecha=fecha), self.assertRaises(utm.ConsultaInvalida):
                utm.leer_firma(self.firma(ValorUTM(70000, fecha, "CMF")), self.usuario)

    def test_firma_de_otro_contexto_no_sirve(self):
        self.secretaria()
        with self.assertRaises(utm.ConsultaInvalida):
            utm.leer_firma(signing.dumps({"v": 1, "d": "2026-01-01", "f": "x"}), self.usuario)

    def test_firma_de_otra_persona_no_sirve(self):
        self.secretaria()
        firma = self.firma()
        otra = crear_usuario("otra", rol=Rol.SECRETARIO, juzgado=self.jpl1)
        with self.assertRaises(utm.ConsultaInvalida):
            utm.leer_firma(firma, otra)

    def test_secretaria_no_puede_cambiar_otro_juzgado(self):
        self.secretaria()
        resp = self.client.post(CONFIRMAR, {"firma": self.firma(), "juzgados": ["jpl1", "jpl2"]})
        self.assertEqual(resp.status_code, 403)
        for j in (self.jpl1, self.jpl2):
            j.refresh_from_db()
            self.assertEqual(j.utm_valor, 70000)

    def test_slug_malformado_no_llega_a_la_base(self):
        self.secretaria()
        resp = self.client.post(CONFIRMAR, {"firma": self.firma(), "juzgados": ["jpl1\x00"]})
        self.assertEqual(resp.status_code, 403)

    def test_administrador_aplica_a_varios_juzgados(self):
        self.administrador()
        self.client.post(CONFIRMAR, {"firma": self.firma(), "juzgados": ["jpl1", "jpl2"]})
        for j in (self.jpl1, self.jpl2):
            j.refresh_from_db()
            self.assertEqual(j.utm_valor, 72440)
        self.assertEqual(ConstanciaUTM.objects.count(), 2)

    def test_sin_juzgados_marcados_no_guarda(self):
        self.secretaria()
        resp = self.client.post(CONFIRMAR, {"firma": self.firma()})
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(ConstanciaUTM.objects.exists())

    def test_doble_envio_deja_una_sola_constancia(self):
        self.secretaria()
        datos = {"firma": self.firma(), "juzgados": ["jpl1"]}
        self.client.post(CONFIRMAR, datos)
        self.client.post(CONFIRMAR, datos)
        self.assertEqual(ConstanciaUTM.objects.count(), 1)

    def test_mismo_valor_en_un_mes_nuevo_deja_constancia(self):
        self.secretaria()
        resp = self.client.post(
            CONFIRMAR, {"firma": self.firma(consulta_del_mes(70000)), "juzgados": ["jpl1"]}, follow=True,
        )
        self.assertContains(resp, "El valor no cambió")
        self.jpl1.refresh_from_db()
        self.assertIsNotNone(self.jpl1.utm_verificada_en)
        self.assertFalse(ConstanciaUTM.objects.get().cambio_valor)

    def test_un_cambio_durante_la_revision_no_se_pisa(self):
        self.secretaria()
        firma = self.firma()
        self.jpl1.registrar_utm(73000, None, "Ingreso manual", None)
        resp = self.client.post(CONFIRMAR, {"firma": firma, "juzgados": ["jpl1"]}, follow=True)
        self.assertContains(resp, "cambió mientras lo revisabas")
        self.jpl1.refresh_from_db()
        self.assertEqual(self.jpl1.utm_valor, 73000)

    def test_el_conflicto_en_un_juzgado_no_guarda_los_demas(self):
        self.administrador()
        firma = self.firma()
        self.jpl2.registrar_utm(73000, None, "Ingreso manual", None)
        self.client.post(CONFIRMAR, {"firma": firma, "juzgados": ["jpl1", "jpl2"]})
        self.jpl1.refresh_from_db()
        self.assertEqual(self.jpl1.utm_valor, 70000)

    def test_juzgado_que_la_persona_no_vio_no_se_guarda(self):
        self.administrador()
        firma = self.firma(None, self.jpl1)
        self.client.post(CONFIRMAR, {"firma": firma, "juzgados": ["jpl2"]})
        self.jpl2.refresh_from_db()
        self.assertEqual(self.jpl2.utm_valor, 70000)

    def test_get_no_confirma(self):
        self.secretaria()
        self.assertEqual(self.client.get(CONFIRMAR).status_code, 405)


class RegistroTest(BaseUTM):
    def test_esperado_distinto_lanza_conflicto(self):
        with self.assertRaises(ConflictoUTM):
            self.jpl1.registrar_utm(72440, primer_dia(), "CMF", None, esperado=(1, None))

    def test_constancia_protege_al_juzgado(self):
        self.jpl1.registrar_utm(72440, primer_dia(), "CMF", None)
        with self.assertRaises(ProtectedError):
            self.jpl1.delete()

    def test_constancia_conserva_el_nombre_si_se_borra_la_cuenta(self):
        usuario = crear_usuario("temporal", rol=Rol.SECRETARIO, juzgado=self.jpl1)
        self.jpl1.registrar_utm(72440, primer_dia(), "CMF", usuario)
        usuario.delete()
        constancia = ConstanciaUTM.objects.get()
        self.assertIsNone(constancia.usuario)
        self.assertEqual(constancia.nombre_usuario, "temporal")


class EstadoTest(BaseUTM):
    HOY = date(2026, 11, 15)

    def test_estados(self):
        self.jpl1.utm_valor = 0
        self.assertEqual(utm.estado_de(self.jpl1, self.HOY).codigo, utm.SIN_VALOR)
        self.jpl1.utm_valor = 72440
        self.assertEqual(utm.estado_de(self.jpl1, self.HOY).codigo, utm.SIN_VERIFICAR)
        self.jpl1.utm_vigente_desde = date(2026, 10, 1)
        estado = utm.estado_de(self.jpl1, self.HOY)
        self.assertEqual((estado.codigo, estado.etiqueta), (utm.DE_OTRO_MES, "Valor de octubre"))
        self.jpl1.utm_vigente_desde = date(2026, 11, 1)
        self.assertTrue(utm.estado_de(self.jpl1, self.HOY).al_dia)

    def test_mismo_mes_con_otro_valor_no_dice_al_dia(self):
        self.jpl1.utm_valor, self.jpl1.utm_vigente_desde = 72000, date(2026, 11, 1)
        consulta = ValorUTM(72440, date(2026, 11, 1), "CMF")
        self.assertEqual(utm.estado_de(self.jpl1, self.HOY, consulta).codigo, utm.DIFIERE)

    def test_panel_avisa_a_quien_puede_corregirlo(self):
        self.secretaria()
        self.assertContains(self.client.get("/"), "Revisa la UTM")

    def test_panel_no_avisa_a_quien_no_puede(self):
        crear_usuario("jueza", rol=Rol.JUEZ, juzgado=self.jpl1)
        ingresar(self.client, "jueza")
        self.assertNotContains(self.client.get("/"), "Revisa la UTM")

    def test_plantilla_muestra_el_estado_y_el_acceso(self):
        self.secretaria()
        resp = self.client.get("/plantilla/")
        self.assertContains(resp, "Sin verificar")
        self.assertContains(resp, URL)


class FuenteConfiguradaTest(TestCase):
    @override_settings(CMF_API_KEY=KEY)
    def test_con_key_usa_la_cmf(self):
        self.assertIsInstance(utm.fuente_configurada(), FuenteCMF)

    @override_settings(CMF_API_KEY="")
    def test_sin_key_usa_la_fuente_publica(self):
        self.assertIsInstance(utm.fuente_configurada(), FuenteMindicador)


class AdminTest(BaseUTM):
    def _guardar(self, valor):
        self.client.force_login(get_user_model().objects.create_superuser("root", password="x-Prueba-123"))
        url = f"/admin/nucleo/juzgado/{self.jpl1.pk}/change/"
        form = self.client.get(url).context["adminform"].form
        datos = {k: ("" if v is None else v) for k, v in form.initial.items() if k in form.fields}
        datos = {k: v for k, v in datos.items() if not isinstance(v, FieldFile)}
        self.client.post(url, datos | {"utm_valor": valor, "activo": "on"})

    def test_cambio_a_mano_deja_constancia_y_queda_sin_verificar(self):
        self.jpl1.registrar_utm(70000, primer_dia(), "CMF", None)
        ConstanciaUTM.objects.all().delete()
        self._guardar(71000)
        self.jpl1.refresh_from_db()
        self.assertEqual(self.jpl1.utm_valor, 71000)
        self.assertIsNone(self.jpl1.utm_vigente_desde)
        constancia = ConstanciaUTM.objects.get()
        self.assertEqual((constancia.fuente, constancia.nombre_usuario), ("Ingreso manual", "root"))

    def test_constancias_son_de_solo_lectura(self):
        self._guardar(71000)
        constancia = ConstanciaUTM.objects.get()
        url = f"/admin/nucleo/constanciautm/{constancia.pk}/delete/"
        self.assertEqual(self.client.post(url, {"post": "yes"}).status_code, 403)


class GraficoTest(SimpleTestCase):
    def _serie(self, *valores, meses=None):
        meses = meses or range(1, len(valores) + 1)
        return [ValorUTM(v, date(2026, m, 1), "CMF") for v, m in zip(valores, meses)]

    def test_pocos_puntos_no_dibuja(self):
        self.assertIsNone(utm_grafico.construir(self._serie(70000, 71000)))

    def test_valores_iguales_no_dividen_por_cero(self):
        self.assertEqual(utm_grafico.construir(self._serie(70000, 70000, 70000))["variacion"], "0,0 %")

    def test_variacion_y_extremos(self):
        g = utm_grafico.construir(self._serie(70000, 72000, 72720))
        self.assertEqual(g["variacion"], "+1,0 %")
        self.assertEqual((g["bajo"], g["alto_pesos"]), ("$70.000", "$72.720"))
        self.assertEqual(g["mes_previo"], "febrero")

    def test_coordenadas_sin_coma_decimal(self):
        g = utm_grafico.construir(self._serie(70000, 72000, 72720))
        self.assertNotIn(",", g["trazo"])

    def test_es_una_escalera_y_un_mes_faltante_corta_el_trazo(self):
        g = utm_grafico.construir(self._serie(70000, 71000, 72000, meses=[1, 2, 4]))
        self.assertEqual(g["trazo"].count("H"), 3)
        self.assertEqual(g["trazo"].count("M"), 2)
        self.assertEqual(len(g["iniciales"]), 4)


class _ServidorQueResponde(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        cuerpo = b'{"UTMs": [{"Valor": "72.440", "Fecha": "2026-11-01"}]}'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(cuerpo)

    def log_message(self, *args):
        pass


class KeyEnGlitchTipTest(SimpleTestCase):
    """Prueba con el SDK real: las migas de las peticiones salientes traen la query."""

    def _eventos_tras_consultar(self, filtro):
        eventos = []
        servidor = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ServidorQueResponde)
        threading.Thread(target=servidor.serve_forever, daemon=True).start()
        self.addCleanup(servidor.server_close)
        self.addCleanup(servidor.shutdown)
        opciones = {"before_breadcrumb": filtro} if filtro else {}

        def capturar(evento, _pista):
            eventos.append(evento)
            return None

        with patch.object(FuenteCMF, "URL_BASE", f"http://127.0.0.1:{servidor.server_port}/utm"):
            with sentry_sdk.new_scope():
                sentry_sdk.init(dsn="http://x@127.0.0.1:9/1", before_send=capturar, **opciones)
                try:
                    FuenteCMF(KEY).vigente(date(2026, 11, 15))
                    sentry_sdk.capture_message("error posterior")
                finally:
                    sentry_sdk.init(dsn=None)
        return repr(eventos)

    def test_sin_el_filtro_la_key_viajaria(self):
        self.assertIn(KEY, self._eventos_tras_consultar(None))

    def test_con_el_filtro_la_key_no_viaja(self):
        self.assertNotIn(KEY, self._eventos_tras_consultar(sin_migas_http))
