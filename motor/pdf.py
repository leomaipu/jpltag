"""
Conversión de un paquete de cartas Word a PDF con LibreOffice.

El PDF sale con permisos restringidos: se puede abrir e imprimir sin clave,
pero los lectores de PDF no permiten modificarlo. La clave de permisos es
aleatoria y no se guarda: nadie necesita levantar la restricción (las
cartas se vuelven a generar desde la nómina). Es una barrera contra la
edición casual, no una firma electrónica: no prueba quién emitió la carta.

Este módulo no sabe nada de cartas ni de Django: recibe la ruta de un
.docx y deja el .pdf a su lado.
"""

import json
import os
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path

BINARIO_DEFECTO = "soffice"
# 100 cartas tardan unos 10 s (medido el 10-nov-2026); el margen cubre un
# servidor ocupado sin llegar al tiempo límite del lote.
TIEMPO_MAXIMO_CONVERSION = 90  # s


class ErrorPdf(Exception):
    """El PDF no se pudo generar (el Word no se ve afectado)."""


def conversion_disponible(binario=BINARIO_DEFECTO):
    """¿Está LibreOffice en este servidor?"""
    return shutil.which(binario) is not None


def _filtro_de_exportacion():
    opciones = {
        "RestrictPermissions": {"type": "boolean", "value": "true"},
        "PermissionPassword": {"type": "string", "value": secrets.token_urlsafe(24)},
        "Changes": {"type": "long", "value": "0"},            # no se permite ningún cambio
        "Printing": {"type": "long", "value": "2"},           # impresión en alta resolución
        "EnableCopyingOfContent": {"type": "boolean", "value": "true"},
    }
    return f"pdf:writer_pdf_Export:{json.dumps(opciones)}"


def es_pdf(ruta):
    """El archivo existe y es un PDF completo (no uno a medio escribir)."""
    try:
        with open(ruta, "rb") as f:
            if f.read(5) != b"%PDF-":
                return False
            f.seek(max(0, os.path.getsize(ruta) - 1024))
            return b"%%EOF" in f.read()
    except OSError:
        return False


def ruta_pdf_de(ruta_docx):
    return str(Path(ruta_docx).with_suffix(".pdf"))


def convertir_a_pdf(ruta_docx, binario=BINARIO_DEFECTO, tiempo_maximo=TIEMPO_MAXIMO_CONVERSION):
    """Deja junto a `ruta_docx` su versión PDF y devuelve la ruta. Lanza
    ErrorPdf si no se pudo.

    Cada conversión usa un perfil de LibreOffice propio y desechable: así
    varias pueden correr a la vez sin estorbarse y no queda nada escrito
    fuera de la carpeta temporal."""
    destino = ruta_pdf_de(ruta_docx)
    with tempfile.TemporaryDirectory(prefix="jpltag-pdf-") as tmp:
        perfil = Path(tmp, "perfil")
        salida = Path(tmp, "salida")
        salida.mkdir()
        orden = [
            binario, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
            f"-env:UserInstallation={perfil.as_uri()}",
            "--convert-to", _filtro_de_exportacion(), "--outdir", str(salida), str(ruta_docx),
        ]
        try:
            hecho = subprocess.run(orden, capture_output=True, timeout=tiempo_maximo, check=False)
        except FileNotFoundError:
            raise ErrorPdf(f"LibreOffice («{binario}») no está instalado en el servidor") from None
        except subprocess.TimeoutExpired:
            raise ErrorPdf(f"LibreOffice no terminó en {tiempo_maximo} s") from None
        generado = salida / f"{Path(ruta_docx).stem}.pdf"
        if not es_pdf(generado):
            detalle = (hecho.stderr or hecho.stdout or b"").decode("utf-8", "replace").strip()[-300:]
            raise ErrorPdf(f"LibreOffice no produjo el PDF (código {hecho.returncode}): {detalle}")
        # Se copia aparte y se renombra al final: nunca queda un PDF
        # truncado con el nombre definitivo.
        temporal = f"{destino}.tmp"
        shutil.copyfile(generado, temporal)
        os.replace(temporal, destino)
    return destino
