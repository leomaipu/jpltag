"""
Módulo 4 de la propuesta: valor UTM vigente, consultado desde una fuente
pública en vez de quedar fijo a mano en cada Juzgado (ver
Juzgado.utm_valor en nucleo/models.py).

Se eligió mindicador.cl en vez de la API oficial de la CMF
(api.cmfchile.cl) porque esta última exige una API key que hay que
tramitar aparte con la CMF — mindicador.cl es pública, gratuita, sin
registro, y el valor que publica sale igual del Banco Central/SII
(decisión confirmada con Leonardo el 11-oct-2026). Si más adelante se
consigue una API key de la CMF, ESTE es el único archivo que habría que
tocar: nada más en el repo sabe de dónde sale el valor, todo lo demás
solo llama a consultar_utm_vigente().

Este módulo SOLO consulta — no pisa Juzgado.utm_valor por su cuenta. Un
valor tan sensible (afecta el monto de cada multa en la carta) lo
actualiza quien tiene el permiso de editar la plantilla, con un clic
(ver procesos.views.ActualizarUTMView), nunca en silencio.
"""

import json
import urllib.error
import urllib.request
from datetime import datetime

URL_MINDICADOR = "https://mindicador.cl/api/utm"
TIMEOUT_SEGUNDOS = 6


class ErrorConsultaUTM(Exception):
    """Cualquier problema consultando el valor UTM vigente: red caída,
    mindicador.cl no responde o no está disponible, respuesta con un
    formato inesperado, etc. Quien llama a consultar_utm_vigente() la
    atrapa y muestra un mensaje al usuario — nunca debería traducirse en
    un error 500 de la vista."""


def consultar_utm_vigente():
    """Devuelve {"valor": int, "fecha": date, "fuente": str} con el valor
    UTM del mes más reciente que entrega mindicador.cl (su "serie" viene
    ordenada del más reciente al más antiguo)."""
    try:
        with urllib.request.urlopen(URL_MINDICADOR, timeout=TIMEOUT_SEGUNDOS) as resp:
            datos = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ErrorConsultaUTM(f"No se pudo conectar a mindicador.cl: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ErrorConsultaUTM(f"mindicador.cl respondió con un formato inesperado: {exc}") from exc

    serie = datos.get("serie") or []
    if not serie:
        raise ErrorConsultaUTM("mindicador.cl respondió sin datos de UTM.")

    actual = serie[0]
    try:
        valor = int(actual["valor"])
        fecha = datetime.fromisoformat(str(actual["fecha"]).replace("Z", "+00:00")).date()
    except (KeyError, ValueError, TypeError) as exc:
        raise ErrorConsultaUTM(f"mindicador.cl respondió con un formato inesperado: {exc}") from exc

    return {"valor": valor, "fecha": fecha, "fuente": "mindicador.cl"}
