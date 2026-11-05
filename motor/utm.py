"""
Valor de la UTM desde una fuente externa (Módulo 4 de la propuesta).

Este módulo solo consulta: no conoce Django ni guarda nada. El valor define el
monto de cada multa, así que ante cualquier duda falla en vez de adivinar, y una
persona lo confirma antes de guardarlo (ver procesos/utm.py).

Dos adaptadores detrás del mismo puerto (FuenteUTM):
  - FuenteCMF: API oficial de la CMF. Exige una API key.
  - FuenteMindicador: pública, para instalaciones sin esa key.

La API de la CMF recibe la key en la URL, y el repositorio es público: ningún
error conserva la URL (tampoco en su contexto), y no se siguen redirecciones
(podrían llevar la key a otro destino).
"""

import http.client
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

# Tope de cada operación de red y de la descarga completa: un servidor lento no
# debe retener un worker de gunicorn.
TIMEOUT_SEGUNDOS = 4
TIEMPO_TOTAL_SEGUNDOS = 5
TOPE_RESPUESTA_BYTES = 256 * 1024
MESES_DE_SERIE = 12

# Un valor fuera de este rango es un error de la fuente, no una UTM real.
VALOR_MINIMO = 10_000
VALOR_MAXIMO = 1_000_000
# Más atrasada que esto, la fuente no sirve para hablar del mes en curso.
MESES_DE_ATRASO_ADMITIDOS = 1

_FORMATO_CHILENO = re.compile(r"[0-9]{1,3}(\.[0-9]{3})*(,00)?")


class ErrorConsultaUTM(Exception):
    """La fuente no entregó un valor confiable. El mensaje se puede registrar
    y mostrar: nunca incluye la URL ni la key."""


@dataclass(frozen=True)
class ValorUTM:
    valor: int
    vigente_desde: date  # siempre el día 1 del mes
    fuente: str


class FuenteUTM(Protocol):
    nombre: str

    def vigente(self, hoy: date) -> ValorUTM:
        """El último valor ya vigente, con hasta un mes de atraso."""

    def serie(self, hoy: date) -> list[ValorUTM]:
        """Los últimos meses ya vigentes, del más antiguo al más reciente."""


class _SinRedirecciones(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_ABRIDOR = urllib.request.build_opener(_SinRedirecciones)


class _SinDatos(Exception):
    """La fuente respondió 404: no publica datos para ese período."""


def _descargar(url):
    """Corre en su propio hilo porque ningún timeout de socket acota una respuesta
    que llega gota a gota, ni siquiera en las cabeceras. Si se pasa del tope total,
    se abandona: el hilo termina solo con el próximo corte de la conexión."""
    resultado = {}

    def tarea():
        try:
            partes, leidos = [], 0
            limite = time.monotonic() + TIEMPO_TOTAL_SEGUNDOS
            with _ABRIDOR.open(url, timeout=TIMEOUT_SEGUNDOS) as resp:
                while parte := resp.read1(16 * 1024):
                    partes.append(parte)
                    leidos += len(parte)
                    if leidos > TOPE_RESPUESTA_BYTES or time.monotonic() > limite:
                        raise OverflowError
            resultado["cuerpo"] = b"".join(partes)
        except Exception as exc:
            resultado["error"] = exc

    hilo = threading.Thread(target=tarea, daemon=True)
    hilo.start()
    hilo.join(TIEMPO_TOTAL_SEGUNDOS)
    if hilo.is_alive():
        raise OverflowError
    if "error" in resultado:
        raise resultado["error"]
    return resultado["cuerpo"]


def _leer_json(url, nombre):
    """El error se lanza fuera del `except` para que no conserve la excepción
    original, cuya URL trae la key."""
    sin_datos = False
    datos = None
    try:
        datos = json.loads(_descargar(url).decode("utf-8"))
        mensaje = None if isinstance(datos, dict) else f"{nombre} respondió con un formato inesperado."
    except urllib.error.HTTPError as exc:
        sin_datos = exc.code == 404
        mensaje = f"{nombre} respondió con el error HTTP {exc.code}."
        exc.close()
    except OverflowError:
        mensaje = f"{nombre} respondió con un contenido demasiado grande o demasiado lento."
    except (ValueError, RecursionError):
        mensaje = f"{nombre} respondió con un formato inesperado."
    except (urllib.error.URLError, http.client.HTTPException, OSError) as exc:
        mensaje = f"No se pudo conectar a {nombre} ({type(getattr(exc, 'reason', exc)).__name__})."
    if sin_datos:
        raise _SinDatos
    if mensaje:
        raise ErrorConsultaUTM(mensaje)
    return datos


def valor_chileno(texto):
    """"72.440" o "68.647,00" → entero. Cualquier otra forma se rechaza: leer
    mal un separador multiplica el valor por 10, y la UTM no tiene centavos."""
    if not isinstance(texto, str) or not _FORMATO_CHILENO.fullmatch(texto.strip()):
        raise ValueError("valor UTM no interpretable")
    return int(texto.strip().removesuffix(",00").replace(".", ""))


def valor_entero(numero):
    """Para fuentes que entregan un número JSON: no trunca ni redondea."""
    if isinstance(numero, bool) or not isinstance(numero, (int, float)) or numero != int(numero):
        raise ValueError("valor UTM no interpretable")
    return int(numero)


def _construir(valor, fecha, fuente):
    if not VALOR_MINIMO <= valor <= VALOR_MAXIMO:
        raise ValueError("valor UTM fuera de rango")
    return ValorUTM(valor, fecha.replace(day=1), fuente)


def _meses_entre(desde, hasta):
    return (hasta.year - desde.year) * 12 + hasta.month - desde.month


def _mas_reciente_vigente(valores, hoy, nombre):
    """Una fuente puede publicar el mes siguiente antes de que empiece: ese
    valor todavía no rige."""
    vigentes = [v for v in valores if v.vigente_desde <= hoy]
    if not vigentes:
        raise ErrorConsultaUTM(f"{nombre} no entregó un valor vigente.")
    reciente = max(vigentes, key=lambda v: v.vigente_desde)
    if _meses_entre(reciente.vigente_desde, hoy) > MESES_DE_ATRASO_ADMITIDOS:
        raise ErrorConsultaUTM(f"{nombre} entregó un valor demasiado antiguo.")
    return reciente


def _ultimos_meses(valores, hoy):
    vigentes = {v.vigente_desde: v for v in valores if v.vigente_desde <= hoy}
    return [vigentes[f] for f in sorted(vigentes)][-MESES_DE_SERIE:]


class FuenteCMF:
    nombre = "CMF"
    URL_BASE = "https://api.cmfchile.cl/api-sbifv3/recursos_api/utm"

    def __init__(self, api_key):
        self._api_key = api_key

    def __repr__(self):
        return "FuenteCMF()"

    def _consultar(self, ruta=""):
        clave = urllib.parse.quote(self._api_key, safe="")
        datos = _leer_json(f"{self.URL_BASE}{ruta}?apikey={clave}&formato=json", self.nombre)
        try:
            return [
                _construir(valor_chileno(r["Valor"]), date.fromisoformat(r["Fecha"]), self.nombre)
                for r in datos["UTMs"]
            ]
        except (KeyError, ValueError, TypeError):
            pass
        raise ErrorConsultaUTM("CMF respondió con un formato inesperado.")

    def vigente(self, hoy):
        try:
            valores = self._consultar()
            if not any(v.vigente_desde <= hoy for v in valores):
                # Publicó el mes siguiente: el vigente se busca en la lista del año.
                valores = self._consultar(f"/{hoy.year}")
        except _SinDatos:
            raise ErrorConsultaUTM("CMF no tiene datos de UTM publicados.") from None
        return _mas_reciente_vigente(valores, hoy, self.nombre)

    def serie(self, hoy):
        valores = []
        for anio in (hoy.year - 1, hoy.year):
            try:
                valores += self._consultar(f"/{anio}")
            except _SinDatos:
                continue
        return _ultimos_meses(valores, hoy)


class FuenteMindicador:
    nombre = "mindicador.cl"
    URL = "https://mindicador.cl/api/utm"

    def _consultar(self):
        try:
            datos = _leer_json(self.URL, self.nombre)
        except _SinDatos:
            raise ErrorConsultaUTM("mindicador.cl no tiene datos de UTM.") from None
        try:
            return [
                _construir(
                    valor_entero(r["valor"]),
                    datetime.fromisoformat(str(r["fecha"]).replace("Z", "+00:00")).date(),
                    self.nombre,
                )
                for r in datos["serie"]
            ]
        except (KeyError, ValueError, TypeError, OverflowError):
            pass
        raise ErrorConsultaUTM("mindicador.cl respondió con un formato inesperado.")

    def vigente(self, hoy):
        return _mas_reciente_vigente(self._consultar(), hoy, self.nombre)

    def serie(self, hoy):
        return _ultimos_meses(self._consultar(), hoy)
