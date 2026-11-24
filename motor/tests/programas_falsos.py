"""Programas falsos para las pruebas del conversor a PDF.

Imitan a LibreOffice (fallar, colgarse, lanzar otros procesos) con un
script de shell en una carpeta temporal. Eso exige poder ejecutar programas
desde ahí: el despliegue corre las pruebas con el directorio temporal en un
tmpfs montado con «noexec» (por seguridad), y entonces estas pruebas no
tienen cómo hacerse. En ese caso se saltan con el motivo, en vez de fallar
por algo que no es de la app: en producción la conversión ejecuta el
LibreOffice instalado en la imagen, nunca algo del directorio temporal.
"""

import os
import stat
import subprocess
import unittest
from pathlib import Path


def _escribir_ejecutable(ruta, cuerpo):
    ruta.write_text("#!/bin/sh\n" + cuerpo + "\n")
    ruta.chmod(ruta.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return ruta


def se_puede_ejecutar_en(carpeta):
    """¿Se pueden ejecutar programas guardados en `carpeta`?"""
    prueba = _escribir_ejecutable(Path(carpeta, ".prueba-ejecucion"), "exit 0")
    try:
        return subprocess.run([str(prueba)], capture_output=True, timeout=10, check=False).returncode == 0
    except (PermissionError, OSError):
        return False
    finally:
        try:
            os.unlink(prueba)
        except OSError:
            pass


def programa_falso(carpeta, cuerpo, nombre="soffice-falso"):
    """Ruta de un script ejecutable con `cuerpo`, dentro de `carpeta`. Salta
    la prueba (unittest.SkipTest) si en esa carpeta no se puede ejecutar."""
    if not se_puede_ejecutar_en(carpeta):
        raise unittest.SkipTest(
            f"el directorio temporal ({carpeta}) no permite ejecutar programas (montado con noexec): "
            "no se puede imitar a LibreOffice con un programa falso"
        )
    return str(_escribir_ejecutable(Path(carpeta, nombre), cuerpo))
