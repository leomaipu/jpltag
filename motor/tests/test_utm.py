"""
Pruebas de motor.utm.consultar_utm_vigente (Módulo 4 — UTM automático).

Este sandbox de desarrollo no puede llegar a mindicador.cl (la salida a
internet está bloqueada por una política de red — ver DESPLIEGUE.md), así
que estas pruebas reemplazan urllib.request.urlopen con una respuesta
fabricada en vez de llamar a la API real. Lo que no queda probado acá es
si el SERVIDOR de producción efectivamente puede llegar a mindicador.cl;
eso hay que confirmarlo aparte (ver nota en DESPLIEGUE.md).
"""

import io
import json
import unittest
import urllib.error
from unittest.mock import patch

from motor.utm import ErrorConsultaUTM, consultar_utm_vigente


def _respuesta_mindicador(serie):
    """Fabrica un objeto que se comporta como el resultado de
    urllib.request.urlopen(...) usado dentro de un `with`."""
    cuerpo = json.dumps({
        "version": "1.0.0", "autor": "mindicador.cl", "codigo": "utm",
        "nombre": "Unidad Tributaria Mensual", "unidad_medida": "Pesos",
        "serie": serie,
    }).encode("utf-8")
    return io.BytesIO(cuerpo)


class TestConsultarUTMVigente(unittest.TestCase):
    @patch("motor.utm.urllib.request.urlopen")
    def test_devuelve_el_valor_mas_reciente_de_la_serie(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value = _respuesta_mindicador([
            {"fecha": "2026-10-01T03:00:00.000Z", "valor": 72151},
            {"fecha": "2026-09-01T03:00:00.000Z", "valor": 71873},
        ])
        info = consultar_utm_vigente()
        self.assertEqual(info["valor"], 72151)
        self.assertEqual(info["fecha"].isoformat(), "2026-10-01")
        self.assertEqual(info["fuente"], "mindicador.cl")

    @patch("motor.utm.urllib.request.urlopen")
    def test_error_de_red_se_traduce_en_errorconsultautm(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.URLError("Tunnel connection failed: 403 Forbidden")
        with self.assertRaises(ErrorConsultaUTM):
            consultar_utm_vigente()

    @patch("motor.utm.urllib.request.urlopen")
    def test_serie_vacia_se_traduce_en_errorconsultautm(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value = _respuesta_mindicador([])
        with self.assertRaises(ErrorConsultaUTM):
            consultar_utm_vigente()

    @patch("motor.utm.urllib.request.urlopen")
    def test_respuesta_con_formato_inesperado_se_traduce_en_errorconsultautm(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value = _respuesta_mindicador([
            {"fecha": "2026-10-01T03:00:00.000Z", "valor": "no-es-un-numero"},
        ])
        with self.assertRaises(ErrorConsultaUTM):
            consultar_utm_vigente()

    @patch("motor.utm.urllib.request.urlopen")
    def test_json_invalido_se_traduce_en_errorconsultautm(self, mock_urlopen):
        mock_urlopen.return_value.__enter__.return_value = io.BytesIO(b"esto no es json")
        with self.assertRaises(ErrorConsultaUTM):
            consultar_utm_vigente()


if __name__ == "__main__":
    unittest.main()
