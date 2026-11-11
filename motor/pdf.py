"""
Conversión de un paquete de cartas Word a PDF con LibreOffice.

El PDF sale con permisos restringidos: se puede abrir e imprimir sin clave,
y los lectores de PDF no ofrecen modificarlo. La clave de permisos es
aleatoria y no se guarda: nadie necesita levantar la restricción (las
cartas se vuelven a generar desde la nómina). Es una barrera contra la
edición casual, NO una protección real: quien use una herramienta que
ignore los permisos puede reescribir el archivo, y no hay firma
electrónica que pruebe quién emitió la carta ni que no cambió.

Este módulo no sabe nada de cartas ni de Django: recibe la ruta de un
.docx y deja el .pdf a su lado.
"""

import json
import os
import secrets
import shutil
import signal
import subprocess
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


def carpeta_de_trabajo(ruta_docx):
    """Dónde trabaja la conversión de ese Word (se borra al terminar)."""
    return f"{ruta_pdf_de(ruta_docx)}-trabajo"


def _descendientes(pid):
    """Los procesos que cuelgan de `pid` (hijos, nietos…), leídos de /proc.
    LibreOffice se lanza a través de un proceso intermedio: al cortar una
    conversión colgada hay que cortar también al que de verdad convierte."""
    hijos_de = {}
    for entrada in os.listdir("/proc") if os.path.isdir("/proc") else ():
        if not entrada.isdigit():
            continue
        try:
            with open(f"/proc/{entrada}/stat", encoding="utf-8", errors="replace") as f:
                padre = int(f.read().rsplit(") ", 1)[1].split()[1])
        except (OSError, IndexError, ValueError):
            continue
        hijos_de.setdefault(padre, []).append(int(entrada))
    encontrados, por_revisar = [], [pid]
    while por_revisar:
        for hijo in hijos_de.get(por_revisar.pop(), ()):
            encontrados.append(hijo)
            por_revisar.append(hijo)
    return encontrados


def _cortar_con_su_descendencia(proceso):
    # La lista se arma ANTES de cortar: después, los nietos quedan sin padre
    # y ya no se pueden encontrar.
    for pid in [*_descendientes(proceso.pid), proceso.pid]:
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proceso.communicate(timeout=5)      # recoge al proceso y cierra sus tuberías
    except subprocess.TimeoutExpired:
        for tuberia in (proceso.stdout, proceso.stderr):
            tuberia.close()
        proceso.wait()


def convertir_a_pdf(ruta_docx, binario=BINARIO_DEFECTO, tiempo_maximo=TIEMPO_MAXIMO_CONVERSION):
    """Deja junto a `ruta_docx` su versión PDF y devuelve la ruta. Lanza
    ErrorPdf si no se pudo; en ese caso no queda ningún PDF con ese nombre
    (tampoco uno anterior).

    Cada conversión usa una carpeta de trabajo propia y desechable, junto
    al Word (`<nombre>.pdf-trabajo`), con el perfil de LibreOffice y sus
    temporales: así varias conversiones pueden correr a la vez sin
    estorbarse y, si el proceso muere a la mitad, lo que quede está en una
    carpeta de nombre conocido (carpeta_de_trabajo) que se puede borrar."""
    destino = ruta_pdf_de(ruta_docx)
    trabajo = Path(carpeta_de_trabajo(ruta_docx))
    try:
        shutil.rmtree(trabajo, ignore_errors=True)
        salida = trabajo / "salida"
        salida.mkdir(parents=True)
        orden = [
            binario, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
            f"-env:UserInstallation={(trabajo / 'perfil').resolve().as_uri()}",
            "--convert-to", _filtro_de_exportacion(), "--outdir", str(salida), str(ruta_docx),
        ]
        entorno = {**os.environ, "TMPDIR": str(trabajo), "TMP": str(trabajo), "TEMP": str(trabajo)}
        try:
            proceso = subprocess.Popen(orden, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=entorno)
        except FileNotFoundError:
            raise ErrorPdf(f"LibreOffice («{binario}») no está instalado en el servidor") from None
        try:
            _, errores = proceso.communicate(timeout=tiempo_maximo)
        except subprocess.TimeoutExpired:
            _cortar_con_su_descendencia(proceso)
            raise ErrorPdf(f"LibreOffice no terminó en {tiempo_maximo} s") from None
        generado = salida / f"{Path(ruta_docx).stem}.pdf"
        if not es_pdf(generado):
            detalle = (errores or b"").decode("utf-8", "replace").strip()[-300:]
            raise ErrorPdf(f"LibreOffice no produjo el PDF (código {proceso.returncode}): {detalle}")
        # Se copia aparte y se renombra al final: nunca queda un PDF
        # truncado con el nombre definitivo.
        temporal = f"{destino}.tmp"
        shutil.copyfile(generado, temporal)
        os.replace(temporal, destino)
    except ErrorPdf:
        _quitar(destino)
        raise
    except OSError as exc:
        _quitar(destino)
        raise ErrorPdf(f"no se pudo escribir el PDF: {exc}") from exc
    finally:
        shutil.rmtree(trabajo, ignore_errors=True)
        _quitar(f"{destino}.tmp")
    return destino


def _quitar(ruta):
    try:
        os.unlink(ruta)
    except OSError:
        pass
