"""Programas falsos para las pruebas del conversor a PDF.

Imitan a LibreOffice (fallar, colgarse, lanzar otros procesos) con un
script de shell en una carpeta temporal. Eso exige poder ejecutar programas
desde ahí: el despliegue corre las pruebas con el directorio temporal en un
tmpfs montado con «noexec» (por seguridad), y entonces estas pruebas no
tienen cómo hacerse. Solo en ese caso se saltan, con el motivo; cualquier
otra falla al ejecutar el programa falso hace fallar la prueba. En
producción la conversión ejecuta el LibreOffice instalado en la imagen,
nunca algo del directorio temporal.
"""

import os
import stat
import unittest
from pathlib import Path


def montada_sin_ejecucion(carpeta):
    """¿La carpeta está en un sistema de archivos montado con noexec?"""
    return bool(os.statvfs(carpeta).f_flag & os.ST_NOEXEC)


def programa_falso(carpeta, cuerpo, nombre="soffice-falso"):
    """Ruta de un script ejecutable con `cuerpo`, dentro de `carpeta`. Salta
    la prueba (unittest.SkipTest) si esa carpeta está montada con noexec."""
    if montada_sin_ejecucion(carpeta):
        raise unittest.SkipTest(
            f"el directorio temporal ({carpeta}) está montado con noexec: "
            "no se puede imitar a LibreOffice con un programa falso"
        )
    ruta = Path(carpeta, nombre)
    ruta.write_text("#!/bin/sh\n" + cuerpo + "\n")
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC)
    return str(ruta)
