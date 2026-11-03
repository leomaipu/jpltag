"""
«Arma tu nómina» en la web: toma los dos reportes de SMC (Ingresos diarios y
Causas por estado), arma la nómina consolidada con motor.armar_nomina y la
deja guardada para descargarla o usarla de inmediato al generar cartas.

La nómina y el informe quedan en una carpeta de SALIDAS_ROOT (la misma
convención de las nóminas guardadas: «<juzgado>/<32 hex>»); la limpieza de
carpetas huérfanas las borra a las 12 horas si nadie las usa, porque traen
datos personales. Los dos reportes de SMC subidos NO se guardan: se leen y
se descartan.
"""

import json
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from django.conf import settings
from django.core import signing

from motor.armar_nomina import ReporteInvalido, armar_nomina, escribir_informe, escribir_nomina

from .generacion import _SAL_NOMINA_GUARDADA, NOMINA, VIGENCIA_NOMINA_GUARDADA, firmar_nomina_guardada

logger = logging.getLogger(__name__)

INFORME = "Informe_consolidacion.xlsx"
RESULTADO = "resultado.json"
NOMBRE_NOMINA_ARMADA = "Nomina_consolidada.xlsx"
ARCHIVOS = {"nomina": (NOMINA, NOMBRE_NOMINA_ARMADA), "informe": (INFORME, INFORME)}


class ErrorArmado(Exception):
    """Algo de los reportes impide armar la nómina (mensaje para la persona)."""


def armar(juzgado, usuario, archivo_ingresos, archivo_causas):
    """Arma y guarda la nómina. Devuelve el token firmado de la nómina (su
    resumen queda en la carpeta: cargar_resultado); ErrorArmado si no se pudo."""
    carpeta_rel = f"{juzgado.slug}/{uuid.uuid4().hex}"
    carpeta = Path(settings.SALIDAS_ROOT) / carpeta_rel
    carpeta.mkdir(parents=True, exist_ok=True)
    try:
        # Los reportes originales se leen desde una carpeta temporal DENTRO de la
        # carpeta de la nómina: si el proceso muere, la limpieza de 12 h también se la lleva.
        with tempfile.TemporaryDirectory(prefix="armar-", dir=carpeta) as tmp:
            rutas = []
            for nombre, archivo in (("ingresos.xlsx", archivo_ingresos), ("causas.xlsx", archivo_causas)):
                ruta = Path(tmp) / nombre
                with open(ruta, "wb") as f:
                    for trozo in archivo.chunks():
                        f.write(trozo)
                rutas.append(ruta)
            try:
                nomina, informe = armar_nomina(*rutas)
            except ReporteInvalido as exc:
                raise ErrorArmado(str(exc) + " ¿Cambiaste el orden de los dos archivos?") from None
            except Exception:
                logger.exception("Arma tu nómina: falló al leer los reportes")
                raise ErrorArmado(
                    "No se pudieron leer los reportes. Revisa que sean los Excel originales de SMC "
                    "(Listado de ingresos diarios y Listado de causas por estado) y que no estén dañados."
                ) from None
        if nomina is None or len(nomina) == 0:
            raise ErrorArmado(
                "Con estos reportes no quedó ninguna causa para notificar. Revisa que el Listado de "
                "ingresos diarios tenga causas PENDIENTES y que sus N° de parte estén en el de causas por estado."
            )
        escribir_nomina(nomina, carpeta / NOMINA)
        escribir_informe(informe, carpeta / INFORME)
        resultado = {
            "resumen": [[k, v if isinstance(v, (int, float, str)) else str(v)] for k, v in informe.resumen.items()],
            "alertas": [[a, b] for a, b in informe.alertas],
            "n_filas": len(nomina),
            "n_patentes": int(nomina["patente"].nunique()),
            "n_excluidas": 0 if informe.excluidas is None else len(informe.excluidas),
        }
        (carpeta / RESULTADO).write_text(json.dumps(resultado, ensure_ascii=False), encoding="utf-8")
    except BaseException:
        shutil.rmtree(carpeta, ignore_errors=True)
        raise
    return firmar_nomina_guardada(carpeta_rel, juzgado, usuario, NOMBRE_NOMINA_ARMADA)


def _carpeta_de(token, juzgado, usuario):
    try:
        datos = signing.loads(token, salt=_SAL_NOMINA_GUARDADA, max_age=VIGENCIA_NOMINA_GUARDADA.total_seconds())
    except signing.BadSignature:
        return None
    carpeta = datos.get("c", "")
    if datos.get("j") != juzgado.pk or datos.get("u") != usuario.pk or not re.fullmatch(
        rf"{re.escape(juzgado.slug)}/[0-9a-f]{{32}}", carpeta
    ):
        return None
    return Path(settings.SALIDAS_ROOT) / carpeta


def cargar_resultado(token, juzgado, usuario):
    """El resumen de una nómina armada (para mostrarlo tras redirigir), o None."""
    carpeta = _carpeta_de(token, juzgado, usuario)
    try:
        datos = json.loads((carpeta / RESULTADO).read_text(encoding="utf-8")) if carpeta else None
    except (OSError, ValueError):
        return None
    return {**datos, "token": token} if datos else None


def ruta_de_descarga(token, juzgado, usuario, que):
    """Ruta del archivo («nomina» o «informe») de una nómina armada, si el
    token es auténtico, vigente, de esta persona y de este juzgado. Sirve
    aunque la nómina ya se haya usado para generar cartas, mientras la
    carpeta siga en disco. None si no."""
    if que not in ARCHIVOS:
        return None
    carpeta = _carpeta_de(token, juzgado, usuario)
    if carpeta is None:
        return None
    ruta = carpeta / ARCHIVOS[que][0]
    return (ruta, ARCHIVOS[que][1]) if ruta.is_file() else None
