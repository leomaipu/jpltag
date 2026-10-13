"""
Limpieza de control de cambios y comentarios de Word.

Las plantillas de las cartas se editan en Word, y a veces se suben con el
control de cambios activo y comentarios de revisión. Word imprime esas marcas
(los cambios en color con barras al margen y los comentarios en globos al
costado) salvo que quien imprime elija "Sin marcas", y las cartas son
documentos oficiales que van por correo certificado: no pueden salir con
anotaciones internas.

`plantilla_limpia()` entrega la plantilla como si todos los cambios estuvieran
ACEPTADOS y sin ningún comentario:

- las inserciones quedan como texto normal y las eliminaciones desaparecen;
- los registros de cambios de formato se descartan (queda el formato actual);
- se quitan los comentarios y la lista de revisores (nombres de personas);
- se desactiva el seguimiento de cambios del documento.

No toca el archivo original: trabaja sobre una copia en memoria.
"""

import io
import os
import re
import zipfile
from functools import lru_cache

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_NS = {"w": W}

# Partes XML de contenido en las que viven cambios y marcas de comentario.
_PARTES_CONTENIDO = re.compile(
    r"^word/(document|header\d*|footer\d*|footnotes|endnotes)\.xml$"
)

# Partes que solo existen por los comentarios o por los revisores.
_PARTES_COMENTARIO = re.compile(
    r"^word/(comments|commentsExtended|commentsIds|commentsExtensible|people)\.xml$"
)

_TIPOS_REL_COMENTARIO = (
    "/comments", "/commentsExtended", "/commentsIds", "/commentsExtensible", "/people",
)

# Registros de "qué formato había antes": se descartan.
_CAMBIOS_DE_FORMATO = (
    "rPrChange", "pPrChange", "sectPrChange", "tblPrChange", "tblPrExChange",
    "tcPrChange", "trPrChange", "tblGridChange", "numberingChange",
)


def _w(nombre):
    return f"{{{W}}}{nombre}"


def _quitar(elemento):
    """Elimina el elemento conservando el texto que queda después de él."""
    padre = elemento.getparent()
    if padre is None:
        return
    cola = elemento.tail
    if cola:
        anterior = elemento.getprevious()
        if anterior is not None:
            anterior.tail = (anterior.tail or "") + cola
        else:
            padre.text = (padre.text or "") + cola
    padre.remove(elemento)


def _desenvolver(elemento):
    """Reemplaza el elemento por sus hijos (acepta una inserción)."""
    padre = elemento.getparent()
    if padre is None:
        return
    indice = padre.index(elemento)
    for hijo in list(elemento):
        padre.insert(indice, hijo)
        indice += 1
    _quitar(elemento)


def _limpiar_contenido(raiz):
    # Eliminaciones aceptadas: desaparecen (incluido lo movido hacia otro lado).
    for el in list(raiz.iter(_w("del"), _w("moveFrom"))):
        _quitar(el)
    # Inserciones aceptadas: quedan como texto normal.
    for el in list(raiz.iter(_w("ins"), _w("moveTo"))):
        padre = el.getparent()
        if padre is not None and padre.tag in (_w("rPr"), _w("pPr"), _w("trPr")):
            _quitar(el)            # marca "se insertó el salto de párrafo"
        else:
            _desenvolver(el)
    # Marcas de inicio/fin de movimiento.
    for nombre in ("moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd"):
        for el in list(raiz.iter(_w(nombre))):
            _quitar(el)
    # Registros de formato anterior.
    for nombre in _CAMBIOS_DE_FORMATO:
        for el in list(raiz.iter(_w(nombre))):
            _quitar(el)
    # Celdas insertadas/eliminadas/combinadas con seguimiento.
    for nombre in ("cellIns", "cellDel", "cellMerge"):
        for el in list(raiz.iter(_w(nombre))):
            _quitar(el)
    # Comentarios: rangos y el run que contiene la referencia (el globito).
    for nombre in ("commentRangeStart", "commentRangeEnd"):
        for el in list(raiz.iter(_w(nombre))):
            _quitar(el)
    for el in list(raiz.iter(_w("commentReference"))):
        run = el.getparent()
        if run is not None and run.tag == _w("r"):
            _quitar(run)
        else:
            _quitar(el)


def _limpiar_ajustes(raiz):
    for el in list(raiz.iter(_w("trackRevisions"))):
        _quitar(el)


def _limpiar_relaciones(datos):
    raiz = etree.fromstring(datos)
    for rel in list(raiz):
        tipo = rel.get("Type", "")
        if any(tipo.endswith(t) for t in _TIPOS_REL_COMENTARIO):
            raiz.remove(rel)
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8", standalone=True)


def _limpiar_tipos_de_contenido(datos):
    raiz = etree.fromstring(datos)
    for el in list(raiz):
        parte = (el.get("PartName") or "").lstrip("/")
        if _PARTES_COMENTARIO.match(parte):
            raiz.remove(el)
    return etree.tostring(raiz, xml_declaration=True, encoding="UTF-8", standalone=True)


def limpiar_docx(origen):
    """Devuelve los bytes de un .docx sin control de cambios ni comentarios.
    `origen` puede ser una ruta, bytes o un archivo abierto (subido)."""
    if isinstance(origen, (str, os.PathLike)):
        with open(origen, "rb") as f:
            datos = f.read()
    elif isinstance(origen, (bytes, bytearray)):
        datos = bytes(origen)
    else:
        posicion = origen.tell() if hasattr(origen, "tell") else None
        if hasattr(origen, "seek"):
            origen.seek(0)
        datos = origen.read()
        if posicion is not None and hasattr(origen, "seek"):
            origen.seek(posicion)

    salida = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(datos)) as entrada, \
            zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as nuevo:
        for info in entrada.infolist():
            nombre = info.filename
            contenido = entrada.read(nombre)
            if _PARTES_COMENTARIO.match(nombre):
                continue
            if _PARTES_CONTENIDO.match(nombre):
                raiz = etree.fromstring(contenido)
                _limpiar_contenido(raiz)
                contenido = etree.tostring(
                    raiz, xml_declaration=True, encoding="UTF-8", standalone=True,
                )
            elif nombre == "word/settings.xml":
                raiz = etree.fromstring(contenido)
                _limpiar_ajustes(raiz)
                contenido = etree.tostring(
                    raiz, xml_declaration=True, encoding="UTF-8", standalone=True,
                )
            elif nombre.endswith(".rels") and nombre.startswith("word/_rels/"):
                contenido = _limpiar_relaciones(contenido)
            elif nombre == "[Content_Types].xml":
                contenido = _limpiar_tipos_de_contenido(contenido)
            nuevo.writestr(info, contenido)
    return salida.getvalue()


@lru_cache(maxsize=32)
def _limpio_en_cache(ruta, mtime, tamano):
    return limpiar_docx(ruta)


def plantilla_limpia(origen):
    """Como `limpiar_docx`, pero devuelve un BytesIO listo para DocxTemplate y,
    cuando `origen` es una ruta, guarda el resultado: una corrida genera cientos
    de cartas con la misma plantilla y no hay por qué limpiarla cada vez."""
    if isinstance(origen, (str, os.PathLike)):
        ruta = os.fspath(origen)
        estado = os.stat(ruta)
        return io.BytesIO(_limpio_en_cache(ruta, estado.st_mtime_ns, estado.st_size))
    return io.BytesIO(limpiar_docx(origen))

