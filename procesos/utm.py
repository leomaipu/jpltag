"""
Caso de uso «confirmar la UTM»: conecta la fuente externa (motor/utm.py) con los
juzgados (nucleo). Las vistas solo traducen esto a HTTP.

Dos pasos a propósito: consultar no guarda nada, y lo consultado viaja firmado
hasta la confirmación, junto con lo que la persona vio de cada juzgado. Así se
guarda exactamente lo revisado, y si algo cambió entretanto no se pisa.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from motor.cartas import fmt_pesos
from motor.fechas_en_letras import MESES
from motor.utm import ErrorConsultaUTM, FuenteCMF, FuenteMindicador, ValorUTM
from nucleo.models import ConflictoUTM, ConstanciaUTM

logger = logging.getLogger(__name__)

SAL_FIRMA = "procesos.utm.consulta"
VIGENCIA_FIRMA_SEGUNDOS = 15 * 60
VIGENTE_CACHE_SEGUNDOS = 10 * 60
SERIE_CACHE_SEGUNDOS = 6 * 60 * 60
FALLO_CACHE_SEGUNDOS = 60
FALLO_SERIE_CACHE_SEGUNDOS = 10 * 60
ENTRE_REINTENTOS_SEGUNDOS = 15

# Entre un mes y otro la UTM se mueve poco. Un salto mayor no se bloquea (podría
# ser correcto), pero nada queda marcado por defecto y la pantalla lo advierte.
DISTANCIA_SOSPECHOSA = 0.10
EJEMPLOS_DE_MULTA = (1, 3, 5)
URL_OFICIAL = "https://www.sii.cl/valores_y_fechas/utm/utm{anio}.htm"
_SLUG = re.compile(r"[-\w]{1,50}")

AL_DIA = "al_dia"
DE_OTRO_MES = "de_otro_mes"
SIN_VERIFICAR = "sin_verificar"
SIN_VALOR = "sin_valor"
DIFIERE = "difiere"


class ConsultaInvalida(Exception):
    """La confirmación venció o no corresponde a lo que se consultó."""


@dataclass(frozen=True)
class EstadoUTM:
    codigo: str
    etiqueta: str

    @property
    def al_dia(self):
        return self.codigo == AL_DIA

    @property
    def tono(self):
        """Color del estado en la pantalla: siempre acompaña a la etiqueta."""
        if self.codigo == AL_DIA:
            return "ok"
        return "error" if self.codigo == SIN_VALOR else "aviso"


def periodo(fecha):
    return f"{MESES[fecha.month - 1]} de {fecha.year}"


def _mismo_mes(a, b):
    return (a.year, a.month) == (b.year, b.month)


def fuente_configurada():
    """Con key de la CMF se usa su API oficial. Sin ella sigue la fuente pública,
    para no romper las instalaciones que no la tienen."""
    if settings.CMF_API_KEY:
        return FuenteCMF(settings.CMF_API_KEY)
    return FuenteMindicador()


def consultar_vigente(hoy=None, fresco=False):
    """(ValorUTM o None, fecha y hora de la consulta). La respuesta y los fallos
    se recuerdan un rato: abrir la pantalla no debe retener un worker por cada
    recarga mientras la fuente esté lenta. `fresco` lo salta a pedido, con un
    mínimo entre intentos, y un fallo no reemplaza a un valor bueno ya obtenido."""
    hoy = hoy or timezone.localdate()
    fuente = fuente_configurada()
    clave = f"utm:vigente:{fuente.nombre}:{hoy:%Y-%m}"
    guardado = cache.get(clave)
    if guardado is not None and not (fresco and cache.add(f"{clave}:fresco", True, ENTRE_REINTENTOS_SEGUNDOS)):
        return guardado
    try:
        resultado = (fuente.vigente(hoy), timezone.now())
        duracion = VIGENTE_CACHE_SEGUNDOS
    except ErrorConsultaUTM as exc:
        logger.warning("Consulta UTM fallida: %s", exc)
        if guardado is not None and guardado[0] is not None:
            return guardado
        resultado = (None, timezone.now())
        duracion = FALLO_CACHE_SEGUNDOS
    cache.set(clave, resultado, duracion)
    return resultado


def consultar_serie(consulta, hoy=None):
    """Los últimos meses, siempre alineados con el valor recién consultado. Es un
    complemento: si falla, la pantalla sigue sin el gráfico."""
    hoy = hoy or timezone.localdate()
    fuente = fuente_configurada()
    clave = f"utm:serie:{fuente.nombre}:{hoy:%Y-%m}"
    serie = cache.get(clave)
    if serie is None:
        try:
            serie = fuente.serie(hoy)
            duracion = SERIE_CACHE_SEGUNDOS
        except ErrorConsultaUTM as exc:
            logger.warning("Serie UTM no disponible: %s", exc)
            serie = []
            duracion = FALLO_SERIE_CACHE_SEGUNDOS
        cache.set(clave, serie, duracion)
    if not serie:
        return []
    anteriores = [v for v in serie if v.vigente_desde < consulta.vigente_desde]
    return anteriores + [consulta]


def estado_de(juzgado, hoy=None, consulta=None):
    hoy = hoy or timezone.localdate()
    vigente_desde = juzgado.utm_vigente_desde
    if not juzgado.utm_valor:
        return EstadoUTM(SIN_VALOR, "Sin valor")
    if vigente_desde is None:
        return EstadoUTM(SIN_VERIFICAR, "Sin verificar")
    if not _mismo_mes(vigente_desde, hoy):
        return EstadoUTM(DE_OTRO_MES, f"Valor de {MESES[vigente_desde.month - 1]}")
    if consulta and _mismo_mes(consulta.vigente_desde, hoy) and consulta.valor != juzgado.utm_valor:
        return EstadoUTM(DIFIERE, "Difiere de la fuente")
    return EstadoUTM(AL_DIA, "Al día")


def juzgados_por_revisar(juzgados, hoy=None):
    hoy = hoy or timezone.localdate()
    return [j for j in juzgados if not estado_de(j, hoy).al_dia]


def por_confirmar(juzgado, consulta):
    return juzgado.utm_valor != consulta.valor or juzgado.utm_vigente_desde != consulta.vigente_desde


def _foto(juzgado):
    fecha = juzgado.utm_vigente_desde
    return [juzgado.utm_valor, fecha.isoformat() if fecha else None]


def firmar(consulta, juzgados, usuario):
    return signing.dumps(
        {
            "v": consulta.valor,
            "d": consulta.vigente_desde.isoformat(),
            "f": consulta.fuente,
            "u": usuario.pk,
            "j": {j.slug: _foto(j) for j in juzgados},
        },
        salt=SAL_FIRMA,
    )


def leer_firma(token, usuario, hoy=None):
    """(ValorUTM, lo que la persona vio de cada juzgado)."""
    hoy = hoy or timezone.localdate()
    try:
        datos = signing.loads(token, salt=SAL_FIRMA, max_age=VIGENCIA_FIRMA_SEGUNDOS)
        consulta = ValorUTM(int(datos["v"]), date.fromisoformat(datos["d"]), str(datos["f"]))
        vistos, dueno = datos["j"], datos["u"]
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ConsultaInvalida from None
    if dueno != usuario.pk or not _mismo_mes(consulta.vigente_desde, hoy):
        raise ConsultaInvalida
    return consulta, vistos


def slugs_validos(slugs):
    return all(_SLUG.fullmatch(s) for s in slugs)


def aplicar(consulta, vistos, juzgados, usuario):
    """Todo o nada: devuelve los juzgados que cambiaron de valor. Lanza
    ConflictoUTM si alguno cambió desde que la persona lo revisó."""
    cambiados = []
    with transaction.atomic():
        for juzgado in juzgados:
            if juzgado.slug not in vistos:
                raise ConflictoUTM
            valor, fecha = vistos[juzgado.slug]
            esperado = (valor, date.fromisoformat(fecha) if fecha else None)
            if juzgado.registrar_utm(
                consulta.valor, consulta.vigente_desde, consulta.fuente, usuario, esperado=esperado,
            ):
                cambiados.append(juzgado)
    return cambiados


def _hay_que_revisar(consulta, serie, juzgados):
    """¿Se aleja de algo conocido, o no hay con qué compararlo? La serie sola no
    basta: viene de la misma fuente y podría estar mal igual."""
    conocidos = [v.valor for v in serie[:-1]][-1:] + [j.utm_valor for j in juzgados if j.utm_valor]
    if not conocidos:
        return True
    return any(abs(consulta.valor - c) / c > DISTANCIA_SOSPECHOSA for c in conocidos)


def _antes_comun(pendientes, consulta):
    """El «antes» solo existe si todos los juzgados por confirmar parten del mismo
    valor, distinto de cero y del nuevo."""
    valores = {j.utm_valor for j in pendientes}
    if len(valores) == 1 and not valores & {0, consulta.valor}:
        return valores.pop()
    return None


def _fila(juzgado, hoy, consulta=None, confirmable=False):
    return {
        "juzgado": juzgado,
        "pesos": fmt_pesos(juzgado.utm_valor),
        "estado": estado_de(juzgado, hoy, consulta),
        "pendiente": confirmable and por_confirmar(juzgado, consulta),
    }


def pantalla(juzgados, usuario, hoy=None, fresco=False):
    """Todo lo que muestra «Valor de la UTM». No guarda nada."""
    hoy = hoy or timezone.localdate()
    consulta, consultado_en = consultar_vigente(hoy, fresco)
    publicada = consulta is not None and _mismo_mes(consulta.vigente_desde, hoy)
    filas = [_fila(j, hoy, consulta, confirmable=publicada) for j in juzgados]
    datos = {
        "consulta": consulta,
        "publicada": publicada,
        "consultado_en": consultado_en,
        "filas": filas,
        "fuente": fuente_configurada().nombre,
        "mes_en_curso": periodo(hoy),
        "url_oficial": URL_OFICIAL.format(anio=hoy.year),
        "constancias": ConstanciaUTM.objects.filter(juzgado__in=juzgados).select_related("juzgado")[:5],
    }
    if consulta is None:
        return datos

    serie = consultar_serie(consulta, hoy)
    pendientes = [f["juzgado"] for f in filas if f["pendiente"]]
    antes = _antes_comun(pendientes, consulta)
    datos |= {
        "pesos": fmt_pesos(consulta.valor),
        "periodo": periodo(consulta.vigente_desde),
        "serie": serie,
        "pendientes": len(pendientes),
        "antes": fmt_pesos(antes) if antes else None,
        "revisar": publicada and _hay_que_revisar(consulta, serie, juzgados),
        "ejemplos": [
            {
                "utm": n,
                "pesos": fmt_pesos(n * consulta.valor),
                "antes": fmt_pesos(n * antes) if antes else None,
            }
            for n in EJEMPLOS_DE_MULTA
        ],
    }
    if publicada and pendientes:
        datos["firma"] = firmar(consulta, juzgados, usuario)
    return datos
