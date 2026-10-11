"""
Carta de citación (Art. 3° Ley 18.287) en formato .docx oficial, y su
división en lotes para impresión manual. Porteado de
generar_notificaciones_JPL1.py (y equivalente en JPL2/JPL3) — el texto
legal y el layout de la carta no cambian.

Lo que SÍ cambia respecto del script original: ahí NOMBRE_JUZGADO,
UTM_VALOR, el logo, la firma, etc. eran constantes fijas en el archivo —
una copia del script por juzgado. Acá llegan todas en `config`, un dict
con los datos del Juzgado activo (ver nucleo.models.Juzgado), para que un
solo motor sirva a los 3 juzgados. Claves que `config` puede traer (todas
opcionales salvo las marcadas):

    nombre_juzgado       (requerido) ej. "PRIMER JUZGADO DE POLICÍA LOCAL"
    utm_valor             (requerido) valor de la UTM del mes, en pesos
    remitente             por defecto = nombre_juzgado
    direccion_juzgado     por defecto ""
    ciudad                por defecto "Maipú"
    max_causas_por_pagina por defecto 20
    cargo_firma           por defecto "SECRETARIO"
    nombre_firmante       por defecto "" (si falta, se usa el placeholder)
    secretario_placeholder por defecto f"NOMBRE {cargo_firma} — (pendiente)"
    logo_path             ruta de archivo de imagen, o None
    sello_path            ruta de archivo de imagen, o None
    firma_path            ruta de archivo de imagen, o None
    logo_ancho_in         por defecto 1.0
    sello_ancho_in        por defecto 0.9
    firma_ancho_in        por defecto 1.6
    font_family           por defecto "Arial"
    entidad_denunciante   por defecto el texto oficial de DAF Maipú
    motivo_infraccion     por defecto "TRANSITAR SIN DISPOSITIVO ELECTRÓNICO
                           POR AUTOPISTA CONCESIONADA"
    bloque_postal         por defecto "SANTIAGO 16"
"""

import math
import os
import traceback
from datetime import datetime

import pandas as pd
from docx import Document
from docx.enum.table import WD_ALIGN_VERTICAL, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement, parse_xml
from docx.oxml.ns import nsdecls, qn
from docx.shared import Inches, Pt, RGBColor

from .agenda import SLOT_SIN_AGENDA
from .agrupacion import expandir_en_paginas

C_HEADER = "1F3864"
C_ROW_PAR = "F2F2F2"

ENTIDAD_DENUNCIANTE_DEFECTO = (
    "DIRECCIÓN DE ADMINISTRACIÓN Y FINANZAS DE LA ILUSTRE MUNICIPALIDAD DE MAIPÚ"
)
MOTIVO_INFRACCION_DEFECTO = "TRANSITAR SIN DISPOSITIVO ELECTRÓNICO POR AUTOPISTA CONCESIONADA"
BLOQUE_POSTAL_DEFECTO = "SANTIAGO 16"


# ══════════════════════════════════════════════════════════════════════
# FORMATO DE TEXTO
# ══════════════════════════════════════════════════════════════════════

def fecha_hoy():
    meses = ["enero", "febrero", "marzo", "abril", "mayo", "junio",
             "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
    h = datetime.now()
    return f"{h.day} de {meses[h.month - 1]} de {h.year}"


def fmt_pesos(n):
    return "$" + f"{int(round(n)):,}".replace(",", ".")


def fmt_utm(utm, utm_valor):
    return f"{utm} UTM ({fmt_pesos(utm * utm_valor)})"


def patente_completa(pat, dig_pat):
    return f"{pat}-{dig_pat}" if dig_pat else pat


def clausula_audiencia(fecha_aud, hora_txt):
    """Evita dejar '...a las , bajo apercibimiento...' cuando no hay hora
    asignada (caso SLOT_SIN_AGENDA)."""
    if hora_txt:
        return f"el día {fecha_aud}, a las {hora_txt}"
    return "en fecha y hora a coordinar con el Juzgado"


def formatear_rol(rol_raw, anio):
    """Formato oficial: «Folio_Proceso»-T-«Año_Proceso». Si no hay folio
    real, muestra "PENDIENTE" para que quede clarísimo en la carta que
    ese dato falta completar."""
    if rol_raw:
        return f"{rol_raw}-T-{anio}"
    return "PENDIENTE"


# ══════════════════════════════════════════════════════════════════════
# HELPERS DE FORMATO docx
# ══════════════════════════════════════════════════════════════════════

def set_bg(cell, hex_color):
    shd = parse_xml(f'<w:shd {nsdecls("w")} w:fill="{hex_color}"/>')
    cell._tc.get_or_add_tcPr().append(shd)


def set_cell_margins(cell, top=5, bottom=5, left=45, right=45):
    tcPr = cell._tc.get_or_add_tcPr()
    tcMar = OxmlElement("w:tcMar")
    for lado, val in [("top", top), ("bottom", bottom), ("left", left), ("right", right)]:
        nodo = OxmlElement(f"w:{lado}")
        nodo.set(qn("w:w"), str(val))
        nodo.set(qn("w:type"), "dxa")
        tcMar.append(nodo)
    tcPr.append(tcMar)


def add_run(para, texto, bold=False, italic=False, size=10, color="000000", font_family="Arial"):
    r = para.add_run(texto)
    r.font.name = font_family
    r.font.size = Pt(size)
    r.bold = bold
    r.italic = italic
    r.font.color.rgb = RGBColor.from_string(color)
    return r


def set_para(para, align=WD_ALIGN_PARAGRAPH.LEFT, before=0, after=6):
    para.alignment = align
    para.paragraph_format.space_before = Pt(before)
    para.paragraph_format.space_after = Pt(after)


def add_border_bottom(paragraph, size=6, color="000000"):
    pPr = paragraph._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), color)
    pBdr.append(bottom)
    pPr.append(pBdr)


def quitar_bordes_tabla(tabla):
    tbl = tabla._tbl
    tblPr = tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for tag in ("top", "left", "bottom", "right", "insideH", "insideV"):
        el = OxmlElement(f"w:{tag}")
        el.set(qn("w:val"), "none")
        borders.append(el)
    tblPr.append(borders)


def insertar_logo(doc, config):
    """Logo institucional (izquierda) y sello judicial opcional (derecha),
    si `config['logo_path']`/`config['sello_path']` existen."""
    ruta_logo = config.get("logo_path")
    ruta_sello = config.get("sello_path")
    ruta_logo = ruta_logo if ruta_logo and os.path.exists(ruta_logo) else None
    ruta_sello = ruta_sello if ruta_sello and os.path.exists(ruta_sello) else None
    logo_ancho = config.get("logo_ancho_in", 1.0)
    sello_ancho = config.get("sello_ancho_in", 0.9)

    if not ruta_logo and not ruta_sello:
        return

    if ruta_logo and not ruta_sello:
        try:
            doc.add_picture(ruta_logo, width=Inches(logo_ancho))
            doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.LEFT
        except Exception:
            pass
        return

    # Con sello (con o sin logo): tabla de 2 columnas sin bordes, logo a
    # la izquierda y sello a la derecha, en la misma fila.
    tabla = doc.add_table(rows=1, cols=2)
    tabla.autofit = True
    c_izq, c_der = tabla.rows[0].cells
    if ruta_logo:
        try:
            p = c_izq.paragraphs[0]
            set_para(p, WD_ALIGN_PARAGRAPH.LEFT, after=0)
            p.add_run().add_picture(ruta_logo, width=Inches(logo_ancho))
        except Exception:
            pass
    if ruta_sello:
        try:
            p = c_der.paragraphs[0]
            set_para(p, WD_ALIGN_PARAGRAPH.RIGHT, after=0)
            p.add_run().add_picture(ruta_sello, width=Inches(sello_ancho))
        except Exception:
            pass
    quitar_bordes_tabla(tabla)


def insertar_firma(doc, config):
    """Bloque de firma: si `config['firma_path']` existe, inserta esa
    imagen en vez de la línea punteada. El nombre que se muestra debajo
    es `config['nombre_firmante']` — si quedó vacío, usa el placeholder
    para que no se pierda de vista que falta completarlo."""
    font_family = config.get("font_family", "Arial")
    cargo = config.get("cargo_firma", "SECRETARIO")
    placeholder = config.get("secretario_placeholder") or f"NOMBRE {cargo}(A) — PENDIENTE"
    nombre_mostrar = config.get("nombre_firmante") or placeholder
    firma_path = config.get("firma_path")
    firma_ancho = config.get("firma_ancho_in", 1.6)

    if firma_path and os.path.exists(firma_path):
        try:
            p = doc.add_paragraph()
            set_para(p, WD_ALIGN_PARAGRAPH.RIGHT, before=4, after=0)
            p.add_run().add_picture(firma_path, width=Inches(firma_ancho))
        except Exception:
            p = doc.add_paragraph()
            set_para(p, WD_ALIGN_PARAGRAPH.RIGHT, before=4, after=0)
            add_run(p, ".........................................", size=10, font_family=font_family)
    else:
        p = doc.add_paragraph()
        set_para(p, WD_ALIGN_PARAGRAPH.RIGHT, before=4, after=0)
        add_run(p, ".........................................", size=10, font_family=font_family)

    p = doc.add_paragraph()
    set_para(p, WD_ALIGN_PARAGRAPH.RIGHT, after=0)
    add_run(p, nombre_mostrar, bold=True, size=10, font_family=font_family)
    p = doc.add_paragraph()
    set_para(p, WD_ALIGN_PARAGRAPH.RIGHT, after=0)
    add_run(p, cargo, size=9, font_family=font_family)


# ══════════════════════════════════════════════════════════════════════
# TABLA DE CAUSAS (optimizada para 20 filas por página)
# ══════════════════════════════════════════════════════════════════════

def tabla_causas(doc, causas, anio_actual, config):
    """El Rol es una columna de la tabla, asociado a CADA causa. Como
    todavía no hay acceso a los Roles reales, cada celda muestra
    "PENDIENTE" salvo que la fila ya traiga un Rol propio en el Excel."""
    font_family = config.get("font_family", "Arial")
    utm_valor = config["utm_valor"]

    headers = ["N° Parte", "Rol", "Fecha", "Hora", "Lugar de la Infracción", "Disposición", "Monto"]
    anchos = [Inches(0.55), Inches(0.70), Inches(0.55), Inches(0.38), Inches(2.05), Inches(2.13), Inches(0.85)]

    tabla = doc.add_table(rows=1, cols=7, style="Table Grid")
    tabla.alignment = WD_TABLE_ALIGNMENT.CENTER
    tabla.autofit = False
    tabla.allow_autofit = False

    hdr = tabla.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].width = anchos[i]
        hdr[i].text = ""
        ph = hdr[i].paragraphs[0]
        set_para(ph, WD_ALIGN_PARAGRAPH.CENTER, after=0)
        add_run(ph, h, bold=True, size=7.5, color="FFFFFF", font_family=font_family)
        set_bg(hdr[i], C_HEADER)
        set_cell_margins(hdr[i])
        hdr[i].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    alns = [WD_ALIGN_PARAGRAPH.CENTER] * 4 + [WD_ALIGN_PARAGRAPH.LEFT] * 2 + [WD_ALIGN_PARAGRAPH.CENTER]
    for idx, c in enumerate(causas):
        bg = C_ROW_PAR if idx % 2 == 0 else "FFFFFF"
        rol_causa = formatear_rol(c.get("rol_raw"), anio_actual)
        vals = [c["nroParte"], rol_causa, c["fecha"], c["hora"], c["lugar"], c["disposicion"],
                fmt_utm(c["monto"], utm_valor)]
        fila = tabla.add_row().cells
        for i in range(7):
            fila[i].width = anchos[i]
            fila[i].text = ""
            pc = fila[i].paragraphs[0]
            set_para(pc, alns[i], after=0)
            pc.paragraph_format.line_spacing = 1.0
            add_run(pc, vals[i], size=6, bold=(i == 1 and rol_causa == "PENDIENTE"), font_family=font_family)
            set_bg(fila[i], "FFF2CC" if (i == 1 and rol_causa == "PENDIENTE") else bg)
            set_cell_margins(fila[i])
            fila[i].vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    return tabla


def agregar_fila_numero_carta(tabla, numero_carta, config):
    """Fila final de la tabla de causas, fusionada en las 7 columnas,
    mostrando el N° de Carta: correlativo único en TODO el envío (no se
    reinicia por lote) — llave para cruzar con el Excel de seguimiento."""
    font_family = config.get("font_family", "Arial")
    fila = tabla.add_row().cells
    celda_fusionada = fila[0]
    for c in fila[1:]:
        celda_fusionada = celda_fusionada.merge(c)
    celda_fusionada.text = ""
    p = celda_fusionada.paragraphs[0]
    set_para(p, WD_ALIGN_PARAGRAPH.CENTER, after=0)
    add_run(p, f"N° de Carta: {numero_carta}", bold=True, size=8, font_family=font_family)
    set_bg(celda_fusionada, "DDEBF7")
    set_cell_margins(celda_fusionada, top=6, bottom=6)
    celda_fusionada.vertical_alignment = WD_ALIGN_VERTICAL.CENTER


# ══════════════════════════════════════════════════════════════════════
# CARTA DE CITACIÓN — FORMATO OFICIAL (Art. 3° Ley 18.287)
# ══════════════════════════════════════════════════════════════════════

def escribir_carta_citacion(doc, pg, slot_audiencia, numero_carta, config, is_first_page=False):
    if not is_first_page:
        doc.add_page_break()

    font_family = config.get("font_family", "Arial")
    nombre_juzgado = config["nombre_juzgado"]
    ciudad = config.get("ciudad", "Maipú")
    direccion_juzgado = config.get("direccion_juzgado", "")
    utm_valor = config["utm_valor"]
    entidad_denunciante = config.get("entidad_denunciante") or ENTIDAD_DENUNCIANTE_DEFECTO
    motivo_infraccion = config.get("motivo_infraccion") or MOTIVO_INFRACCION_DEFECTO
    bloque_postal = config.get("bloque_postal") or BLOQUE_POSTAL_DEFECTO
    max_causas = config.get("max_causas_por_pagina", 20)

    pat = pg["patente"]
    info = pg["info"]
    chunk = pg["chunk"]
    parte = pg["parte"]
    total_partes = pg["total_partes"]
    total_causas = pg["total_causas"]
    pat_completa = patente_completa(pat, info["digPat"])

    fecha_aud, hora_aud = slot_audiencia
    hora_txt = f"{hora_aud} hrs." if hora_aud else ""
    anio_actual = datetime.now().year
    reg = info.get("reg_raw") or "«folio carta trámite»"

    utm_total = sum(c["monto"] for c in info["causas"])

    # Encabezado
    insertar_logo(doc, config)
    p = doc.add_paragraph()
    set_para(p, WD_ALIGN_PARAGRAPH.CENTER, after=0)
    add_run(p, nombre_juzgado, bold=True, size=11, font_family=font_family)
    p = doc.add_paragraph()
    set_para(p, WD_ALIGN_PARAGRAPH.CENTER, after=3)
    add_run(p, ciudad, bold=True, size=11, font_family=font_family)

    p = doc.add_paragraph()
    set_para(p, after=2)
    add_run(p, f"{ciudad}, {fecha_hoy()}", size=10, font_family=font_family)
    p = doc.add_paragraph()
    set_para(p, after=2)
    add_run(p, "VISTOS:", bold=True, size=10, font_family=font_family)

    # Texto legal: siempre en formato "intro + tabla adjunta", incluso con
    # 1 sola causa, para que la carta se vea consistente y quede un
    # registro tabular claro.
    oportunidad_txt = "1 oportunidad" if total_causas == 1 else f"{total_causas} oportunidades"
    intro = (
        f"De conformidad al Art. 3° de la Ley 18.287, cítese a don (a): {info['nombre']}, "
        f"propietario (a) del vehículo placa patente {pat_completa}, denunciado por la "
        f"{entidad_denunciante}, por {motivo_infraccion} en "
        f"{oportunidad_txt}"
        + (f" — parte {parte} de {total_partes}, infracciones "
           f"{(parte - 1) * max_causas + 1} a {(parte - 1) * max_causas + len(chunk)}"
           if total_partes > 1 else "")
        + f" (detalle en tabla adjunta) — a prestar declaración indagatoria "
          f"{clausula_audiencia(fecha_aud, hora_txt)}, bajo apercibimiento del artículo 170 de la Ley "
          f"18.290, y de dictar sentencia sin más trámite de conformidad al Art. 15 de la "
          f"Ley 18.287."
    )
    p = doc.add_paragraph()
    set_para(p, WD_ALIGN_PARAGRAPH.JUSTIFY, after=2)
    add_run(p, intro, size=10, font_family=font_family)

    tabla = tabla_causas(doc, chunk, anio_actual, config)
    agregar_fila_numero_carta(tabla, numero_carta, config)

    p = doc.add_paragraph()
    set_para(p, after=3)
    add_run(p, "Monto total infracciones: ", bold=True, size=9, font_family=font_family)
    add_run(p, fmt_utm(utm_total, utm_valor), size=9, font_family=font_family)

    # Firma (imagen si existe, o línea punteada + nombre_firmante)
    insertar_firma(doc, config)
    p = doc.add_paragraph()
    set_para(p, WD_ALIGN_PARAGRAPH.RIGHT, after=3)
    add_run(p, f"{ciudad}, {fecha_hoy()}", size=10, font_family=font_family)

    p = doc.add_paragraph()
    set_para(p, after=1)
    add_run(p, "SALIDA DE CARTA", bold=True, size=9, font_family=font_family)

    p = doc.add_paragraph()
    set_para(p, before=1, after=2)
    add_run(p, "RIJA  Ley 19.841", bold=True, size=16, font_family=font_family)

    p = doc.add_paragraph()
    set_para(p, after=2)
    add_border_bottom(p)

    # Bloque postal
    tabla_sobre = doc.add_table(rows=1, cols=2)
    tabla_sobre.autofit = True
    c1, c2 = tabla_sobre.rows[0].cells
    p1 = c1.paragraphs[0]
    set_para(p1, after=0)
    add_run(p1, nombre_juzgado, bold=True, size=9, font_family=font_family)
    p1b = c1.add_paragraph()
    set_para(p1b, after=0)
    add_run(p1b, ciudad, bold=True, size=9, font_family=font_family)
    p1c = c1.add_paragraph()
    set_para(p1c, after=0)
    add_run(p1c, direccion_juzgado, size=9, font_family=font_family)

    p2 = c2.paragraphs[0]
    set_para(p2, WD_ALIGN_PARAGRAPH.RIGHT, after=0)
    add_run(p2, bloque_postal, bold=True, size=8, font_family=font_family)
    p2b = c2.add_paragraph()
    set_para(p2b, WD_ALIGN_PARAGRAPH.RIGHT, after=0)
    add_run(p2b, f"Reg N° {reg}", size=8, font_family=font_family)
    p2c = c2.add_paragraph()
    set_para(p2c, WD_ALIGN_PARAGRAPH.RIGHT, after=0)
    add_run(p2c, "TAG", bold=True, size=8, font_family=font_family)
    quitar_bordes_tabla(tabla_sobre)

    for label, val in [("NOMBRE    : ", info["nombre"]),
                        ("DIRECCIÓN : ", info["domicilio"]),
                        ("COMUNA    : ", info["comuna"])]:
        p = doc.add_paragraph()
        set_para(p, after=1)
        add_run(p, label, bold=True, size=9, font_family=font_family)
        add_run(p, val, size=9, font_family=font_family)


# ══════════════════════════════════════════════════════════════════════
# GENERACIÓN DEL DOCUMENTO Y LOTES
# ══════════════════════════════════════════════════════════════════════

def crear_doc():
    doc = Document()
    sec = doc.sections[0]
    m = 1.2 / 2.54
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.top_margin = sec.bottom_margin = sec.left_margin = sec.right_margin = Inches(m)
    return doc


def crear_carpeta_envio(prefijo="Envio"):
    """Crea una carpeta nueva para este envío (Envio_1, Envio_2, ...) sin
    pisar carpetas de corridas anteriores."""
    n = 1
    while os.path.exists(f"{prefijo}_{n}"):
        n += 1
    carpeta = f"{prefijo}_{n}"
    os.makedirs(carpeta, exist_ok=True)
    return carpeta


def generar_cartas_certificadas(grupos_filtrados, base_lote, slots, limite_x, config, carpeta_salida=None):
    """Genera las cartas de citación en lotes correlativos .docx, todas
    dentro de una carpeta para este envío, junto con el Excel de
    seguimiento (Lote / N° de Carta / N° de Parte / Rol).

    Cada PATENTE recibe un N° de Carta correlativo, único en TODO el
    envío (no se reinicia por lote): si el lote 1 tiene 100 patentes, sus
    cartas son la 1 a la 100; el lote 2 sigue en la 101.

    `carpeta_salida`, si se indica, se usa tal cual (se crea si no
    existe) en vez de la carpeta autoincremental Envio_N — útil cuando
    quien llama (la vista web) ya decidió dónde dejar los archivos de
    esta corrida.

    Devuelve un dict:
        {
          "carpeta": ruta de la carpeta con el envío, o None si no se
                      generó nada,
          "archivos": [ruta de cada ..._Lote_N.docx generado],
          "seguimiento": ruta del Excel de seguimiento, o None,
          "errores": [mensaje de error por cada lote que falló],
        }
    """
    resultado = {"carpeta": None, "archivos": [], "seguimiento": None, "errores": []}

    if not grupos_filtrados:
        resultado["errores"].append(f"Sin datos — no se generaron cartas certificadas para {base_lote}.")
        return resultado

    patentes = list(grupos_filtrados.items())
    limite_efectivo = limite_x if limite_x and limite_x > 0 else len(patentes)
    lotes = [patentes[i:i + limite_efectivo] for i in range(0, len(patentes), limite_efectivo)]
    max_causas = config.get("max_causas_por_pagina", 20)

    if carpeta_salida:
        os.makedirs(carpeta_salida, exist_ok=True)
        carpeta = carpeta_salida
    else:
        carpeta = crear_carpeta_envio(f"{base_lote}_Envio")
    resultado["carpeta"] = carpeta

    seguimiento = []
    numero_carta_global = 0
    anio_actual = datetime.now().year

    for num_lote, lote_patentes in enumerate(lotes, 1):
        grupo_lote = dict(lote_patentes)
        paginas = expandir_en_paginas(grupo_lote, max_causas)
        nombre_archivo = os.path.join(carpeta, f"{base_lote}_Cartas_Certificadas_Lote_{num_lote}.docx")
        try:
            doc = crear_doc()
            primera = True
            pat_vista = None
            for pg in paginas:
                if pg["patente"] != pat_vista:
                    numero_carta_global += 1
                    pat_vista = pg["patente"]
                    for causa in pg["info"]["causas"]:
                        seguimiento.append({
                            "Lote": num_lote,
                            "N° de Carta": numero_carta_global,
                            "Patente": pg["patente"],
                            "N° de Parte": causa["nroParte"],
                            "Rol": formatear_rol(causa.get("rol_raw"), anio_actual),
                        })
                slot = slots.get(pg["patente"], SLOT_SIN_AGENDA)
                escribir_carta_citacion(doc, pg, slot, numero_carta_global, config, is_first_page=primera)
                primera = False
            doc.save(nombre_archivo)
        except PermissionError:
            resultado["errores"].append(f"Archivo abierto: '{nombre_archivo}'. Ciérralo e intenta de nuevo.")
            continue
        except Exception:
            resultado["errores"].append(f"Error generando '{nombre_archivo}': {traceback.format_exc()}")
            continue

        resultado["archivos"].append(nombre_archivo)

    if seguimiento:
        ruta_seguimiento = os.path.join(carpeta, f"{base_lote}_seguimiento_cartas.xlsx")
        pd.DataFrame(seguimiento).to_excel(ruta_seguimiento, index=False)
        resultado["seguimiento"] = ruta_seguimiento

    return resultado
