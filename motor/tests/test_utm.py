"""Adaptadores de la UTM, con respuestas fabricadas (las formas son las reales
de cada API, observadas el 04-nov-2026). Las pruebas no salen a internet."""

import http.client
import io
import json
import socket
import threading
import time
import unittest
import urllib.error
from datetime import date
from unittest.mock import patch

from motor.utm import ErrorConsultaUTM, FuenteCMF, FuenteMindicador, _ABRIDOR, valor_chileno, valor_entero

HOY = date(2026, 11, 15)
KEY = "clave-de-prueba-que-no-debe-filtrarse"


def _json(datos):
    return io.BytesIO(json.dumps(datos).encode("utf-8"))


def _error_http(codigo, url="https://api.cmfchile.cl/x?apikey=" + KEY):
    return urllib.error.HTTPError(url, codigo, "error", {}, io.BytesIO(b"{}"))


def _respuesta(*valores):
    return _json({"UTMs": [{"Valor": v, "Fecha": f} for v, f in valores]})


class TestValorChileno(unittest.TestCase):
    def test_formatos_de_la_cmf(self):
        self.assertEqual(valor_chileno("72.440"), 72440)
        self.assertEqual(valor_chileno("68.647,00"), 68647)
        self.assertEqual(valor_chileno(" 72.440 "), 72440)

    def test_rechaza_lo_que_cambiaria_el_monto(self):
        casos = ("72440.5", "68.647,50", "7.2.4.4.0", "72_440", "1e5", "٧٢٤٤٠", "", "abc", None, 72440, "NaN")
        for texto in casos:
            with self.subTest(texto=texto), self.assertRaises(ValueError):
                valor_chileno(texto)

    def test_valor_entero_no_trunca(self):
        self.assertEqual(valor_entero(72440), 72440)
        self.assertEqual(valor_entero(72440.0), 72440)
        for numero in (72440.5, True, "72440", None):
            with self.subTest(numero=numero), self.assertRaises(ValueError):
                valor_entero(numero)


@patch.object(_ABRIDOR, "open")
class TestFuenteCMF(unittest.TestCase):
    def setUp(self):
        self.fuente = FuenteCMF(KEY)

    def _falla(self):
        with self.assertRaises(ErrorConsultaUTM) as ctx:
            self.fuente.vigente(HOY)
        self.assertNotIn(KEY, str(ctx.exception))
        self.assertIsNone(ctx.exception.__cause__)
        self.assertIsNone(ctx.exception.__context__)
        return str(ctx.exception)

    def test_devuelve_el_valor_vigente(self, abrir):
        abrir.return_value = _respuesta(("72.440", "2026-11-01"))
        info = self.fuente.vigente(HOY)
        self.assertEqual((info.valor, info.vigente_desde, info.fuente), (72440, date(2026, 11, 1), "CMF"))
        self.assertIn("formato=json", abrir.call_args.args[0])

    def test_normaliza_la_fecha_al_dia_uno(self, abrir):
        abrir.return_value = _respuesta(("72.440", "2026-11-03"))
        self.assertEqual(self.fuente.vigente(HOY).vigente_desde, date(2026, 11, 1))

    def test_no_adelanta_un_mes_que_aun_no_rige(self, abrir):
        abrir.return_value = _respuesta(("72.440", "2026-11-01"), ("73.000", "2026-12-01"))
        self.assertEqual(self.fuente.vigente(HOY).valor, 72440)

    def test_si_solo_entrega_el_mes_siguiente_busca_en_el_anio(self, abrir):
        abrir.side_effect = [
            _respuesta(("73.000", "2026-12-01")),
            _respuesta(("72.440", "2026-11-01"), ("73.000", "2026-12-01")),
        ]
        self.assertEqual(self.fuente.vigente(HOY).valor, 72440)
        self.assertIn("/2026?", abrir.call_args.args[0])

    def test_rechaza_un_valor_demasiado_antiguo(self, abrir):
        abrir.return_value = _respuesta(("52.000", "2021-03-01"))
        self.assertIn("antiguo", self._falla())

    def test_admite_un_mes_de_atraso(self, abrir):
        abrir.return_value = _respuesta(("72.100", "2026-10-01"))
        self.assertEqual(self.fuente.vigente(HOY).vigente_desde, date(2026, 10, 1))

    def test_toma_el_mas_reciente_sin_importar_el_orden(self, abrir):
        abrir.return_value = _respuesta(("72.440", "2026-11-01"), ("72.100", "2026-10-01"))
        self.assertEqual(self.fuente.vigente(HOY).valor, 72440)

    def test_key_rechazada_no_aparece_en_el_error(self, abrir):
        abrir.side_effect = _error_http(500)
        self.assertIn("500", self._falla())

    def test_redireccion_no_se_sigue(self, abrir):
        abrir.side_effect = _error_http(302)
        self.assertIn("302", self._falla())
        self.assertEqual(abrir.call_count, 1)

    def test_fallas_de_red_y_protocolo(self, abrir):
        fallas = [
            urllib.error.URLError(f"sin red apikey={KEY}"),
            TimeoutError(KEY),
            http.client.BadStatusLine(KEY),
            http.client.InvalidURL(f"URL can't contain control characters. {KEY}"),
            ConnectionResetError(KEY),
        ]
        for falla in fallas:
            with self.subTest(falla=type(falla).__name__):
                abrir.side_effect = falla
                self._falla()

    def test_formatos_inesperados(self, abrir):
        casos = [
            b"no es json", b"\xff\xfe", b"[]", b"{}", b'{"UTMs": "x"}', b'{"UTMs": []}',
            b'{"UTMs": [{"Valor": "72.440"}]}',
            b'{"UTMs": [{"Valor": "72.440", "Fecha": "ayer"}]}',
            b'{"UTMs": [{"Valor": "abc", "Fecha": "2026-11-01"}]}',
            b'{"UTMs": [{"Valor": 72440.5, "Fecha": null}]}',
            b'{"UTMs": ["x"]}',
            b"[" * 200_000,
        ]
        for cuerpo in casos:
            with self.subTest(cuerpo=cuerpo[:40]):
                abrir.return_value = io.BytesIO(cuerpo)
                self._falla()

    def test_valor_fuera_de_rango(self, abrir):
        for valor in ("7", "72.440.000"):
            with self.subTest(valor=valor):
                abrir.return_value = _respuesta((valor, "2026-11-01"))
                self._falla()

    def test_respuesta_demasiado_grande(self, abrir):
        abrir.return_value = io.BytesIO(b" " * (256 * 1024 + 1))
        self._falla()

    def test_key_con_caracteres_raros_se_codifica(self, abrir):
        abrir.return_value = _respuesta(("72.440", "2026-11-01"))
        FuenteCMF("abc def\n").vigente(HOY)
        self.assertIn("apikey=abc%20def%0A&", abrir.call_args.args[0])

    def test_serie_une_dos_anios_y_descarta_lo_futuro(self, abrir):
        abrir.side_effect = [
            _respuesta(("68.000", "2025-11-01"), ("68.100", "2025-12-01")),
            _respuesta(("72.440", "2026-11-01"), ("73.000", "2026-12-01")),
        ]
        self.assertEqual([v.valor for v in self.fuente.serie(HOY)], [68000, 68100, 72440])

    def test_serie_tolera_un_anio_sin_datos(self, abrir):
        abrir.side_effect = [_error_http(404), _respuesta(("72.440", "2026-11-01"))]
        self.assertEqual(len(self.fuente.serie(HOY)), 1)

    def test_repr_no_muestra_la_key(self, abrir):
        self.assertNotIn(KEY, repr(self.fuente))


@patch.object(_ABRIDOR, "open")
class TestFuenteMindicador(unittest.TestCase):
    def _falla(self, cuerpo):
        with self.assertRaises(ErrorConsultaUTM):
            FuenteMindicador().vigente(HOY)

    def test_devuelve_el_mas_reciente_ya_vigente(self, abrir):
        abrir.return_value = _json({"serie": [
            {"fecha": "2026-12-01T03:00:00.000Z", "valor": 73000},
            {"fecha": "2026-11-01T03:00:00.000Z", "valor": 72440},
        ]})
        info = FuenteMindicador().vigente(HOY)
        self.assertEqual((info.valor, info.fuente), (72440, "mindicador.cl"))

    def test_rechaza_lo_que_no_es_confiable(self, abrir):
        casos = [
            b'{"serie": []}',
            b'{"serie": [{"fecha": "x", "valor": 72440}]}',
            b'{"serie": [{"fecha": "2026-11-01T03:00:00.000Z", "valor": 72440.5}]}',
            b'{"serie": [{"fecha": "2026-11-01T03:00:00.000Z", "valor": Infinity}]}',
            b'{"serie": [{"fecha": "2026-11-01T03:00:00.000Z", "valor": NaN}]}',
        ]
        for cuerpo in casos:
            with self.subTest(cuerpo=cuerpo[:60]):
                abrir.return_value = io.BytesIO(cuerpo)
                self._falla(cuerpo)


class TestTopeTotal(unittest.TestCase):
    """Con un socket real: un servidor que gotea no debe retener al worker."""

    def _servidor(self, *prefijo):
        escucha = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(escucha.close)
        detener = threading.Event()
        self.addCleanup(detener.set)

        def atender():
            cliente, _ = escucha.accept()
            with cliente:
                try:
                    cliente.sendall(b"".join(prefijo))
                    while not detener.wait(0.2):
                        cliente.sendall(b" ")
                except OSError:
                    pass

        threading.Thread(target=atender, daemon=True).start()
        return f"http://127.0.0.1:{escucha.getsockname()[1]}/utm"

    def _corta_a_tiempo(self, url):
        with patch.object(FuenteCMF, "URL_BASE", url), patch("motor.utm.TIEMPO_TOTAL_SEGUNDOS", 1):
            inicio = time.monotonic()
            with self.assertRaises(ErrorConsultaUTM):
                FuenteCMF(KEY).vigente(HOY)
            self.assertLess(time.monotonic() - inicio, 2.5)

    def test_cuerpo_gota_a_gota(self):
        self._corta_a_tiempo(self._servidor(b"HTTP/1.1 200 OK\r\nContent-Length: 100000\r\n\r\n"))

    def test_cabeceras_gota_a_gota(self):
        self._corta_a_tiempo(self._servidor(b"HTTP/1.1 200 OK\r\nX-Lenta: "))
