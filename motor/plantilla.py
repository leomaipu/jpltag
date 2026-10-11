"""
Carta de citación (Art. 3° Ley 18.287) generada a partir de una
plantilla Word editable por cada Juzgado — Módulo 1 de la propuesta.

Hasta la Fase 3, el diseño de la carta (logo, tabla de causas, firma)
estaba escrito a mano en motor/cartas.py con python-docx — una copia por
Juzgado, embebida en el código. Ahora cada Juzgado sube su propio .docx
(`Juzgado.plantilla_word`, ver nucleo/models.py) con marcadores estilo
Jinja2 (biblioteca docxtpl) que este módulo rellena con los datos reales
de cada carta. Si un Juzgado no subió plantilla propia, se usa la de
PLANTILLA_DEFECTO (motor/plantillas_defecto/citacion_tag.docx) — mismo
mecanismo, nada especial.

Lo que la plantilla SÍ controla: logo/sello/firma, colores, tipografía,
orden de los bloques, cualquier texto propio del Juzgado.
Lo que la plantilla NO controla (sigue fijo acá): el texto legal de la
citación en sí — ver texto_citacion más abajo —, para que no se pueda
introducir sin querer un error de redacción legal en un documento
judicial con solo editar un Word.

══════════════════════════════════════════════════════════════════════
MARCADORES DISPONIBLES EN LA PLANTILLA (contexto de una carta)
══════════════════════════════════════════════════════════════════════

Texto simple — se usan con {{ nombre_del_marcador }}:

    juzgado_nombre        "PRIMER JUZGADO DE POLICÍA LOCAL"
    ciudad                "Maipú"
    direccion_juzgado     la de nucleo.models.Juzgado.direccion
    fecha_hoy             "11 de octubre de 2026"
    nombre_infractor
    domicilio_infractor
    comuna_infractor
    rut_infractor
    vehiculo
    patente                con dígito verificador si lo hay, ej. "AAAA11-1"
    texto_citacion         el párrafo legal completo, ya armado (Art. 3°
                            Ley 18.287) — no se recomienda editar su
                            redacción desde la plantilla, solo dónde va
                            ubicado en la página
    numero_carta           correlativo único en todo el envío
    reg                    folio/Reg N°, o "«folio carta trámite»" si no hay
    monto_total_txt         ej. "3 UTM ($195.000)"
    nombre_firmante
    cargo_firma             "SECRETARIO" / "SECRETARIA"
    bloque_postal           ej. "SANTIAGO 16" (franqueo/zona de Correos)

Imágenes — se usan igual, con {{ }}, pero son opcionales: si el Juzgado
no cargó esa imagen (ver Juzgado.logo/sello_judicial/firma), quedan
vacías (logo_img/sello_img) o con una línea punteada en vez de firma:

    logo_img
    sello_img
    firma_img

Tabla de causas — una fila por infracción de esta carta. Para que
docxtpl repita la fila de la tabla hacen falta 3 filas en la plantilla
(ver motor/plantillas_defecto/citacion_tag.docx como ejemplo): una fila
que solo diga "{%tr for causa in causas %}", la fila con los datos reales
({{ causa.nro_parte }}, etc.) y una fila que solo diga "{%tr endfor %}".

    causas[].nro_parte
    causas[].rol
    causas[].fecha
    causas[].hora
    causas[].lugar
    causas[].disposicion
    causas[].monto_txt
"""

import os
import traceback
from datetime import datetime

import pandas as pd
from docx.shared import Inches
from docxcompose.composer import Composer
from docxtpl import DocxTemplate, InlineImage

from .agenda import SLOT_SIN_AGENDA
from .agrupacion import expandir_en_paginas
from .cartas import (
    ENTIDAD_DENUNCIANTE_DEFECTO,
    MOTIVO_INFRACCION_DEFECTO,
    BLOQUE_POSTAL_DEFECTO,
    clausula_audiencia,
    fecha_hoy,
    fmt_utm,
    formatear_rol,
    patente_completa,
)

PLANTILLA_DEFECTO = os.path.join(
    os.path.dirname(__file__), "plantillas_defecto", "citacion_tag.docx",
)

# Anchos de imagen: igual que los valores por defecto del motor anterior
# (ver historial de motor/cartas.py). No son ajustables desde la
# plantilla — InlineImage necesita el ancho en el momento de renderizar,
# no se puede fijar solo con Word.
ANCHO_LOGO_IN = 1.0
ANCHO_SELLO_IN = 0.9
ANCHO_FIRMA_IN = 1.6

LINEA_FIRMA_DEFECTO = "........................................."


def construir_contexto(pg, slot_audiencia, numero_carta, config):
    """Arma el dict de datos de UNA carta (una patente/página) — lo que
    la plantilla puede leer con {{ }}. Las imágenes se agregan aparte,
    en renderizar_carta(), porque InlineImage necesita la instancia de
    DocxTemplate que las va a insertar."""
    ciudad = config.get("ciudad", "Maipú")
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

    oportunidad_txt = "1 oportunidad" if total_causas == 1 else f"{total_causas} oportunidades"
    texto_citacion = (
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

    cargo = config.get("cargo_firma", "SECRETARIO")
    placeholder_firmante = config.get("secretario_placeholder") or f"NOMBRE {cargo}(A) — PENDIENTE"
    nombre_firmante = config.get("nombre_firmante") or placeholder_firmante

    return {
        "juzgado_nombre": config["nombre_juzgado"],
        "ciudad": ciudad,
        "direccion_juzgado": config.get("direccion_juzgado", ""),
        "fecha_hoy": fecha_hoy(),
        "nombre_infractor": info["nombre"],
        "domicilio_infractor": info["domicilio"],
        "comuna_infractor": info["comuna"],
        "rut_infractor": info.get("rut", "—"),
        "vehiculo": info.get("vehiculo", "—"),
        "patente": pat_completa,
        "texto_citacion": texto_citacion,
        "numero_carta": numero_carta,
        "reg": reg,
        "monto_total_txt": fmt_utm(utm_total, utm_valor),
        "nombre_firmante": nombre_firmante,
        "cargo_firma": cargo,
        "bloque_postal": bloque_postal,
        "causas": [
            {
                "nro_parte": c["nroParte"],
                "rol": formatear_rol(c.get("rol_raw"), anio_actual),
                "fecha": c["fecha"],
                "hora": c["hora"],
                "lugar": c["lugar"],
                "disposicion": c["disposicion"],
                "monto_txt": fmt_utm(c["monto"], utm_valor),
            }
            for c in chunk
        ],
    }


def renderizar_carta(ruta_plantilla, contexto, config):
    """Renderiza UNA carta con docxtpl y devuelve el Document en memoria
    (python-docx), sin guardar a disco todavía."""
    tpl = DocxTemplate(ruta_plantilla)
    ctx = dict(contexto)
    for clave, clave_ruta, ancho, valor_vacio in [
        ("logo_img", "logo_path", ANCHO_LOGO_IN, ""),
        ("sello_img", "sello_path", ANCHO_SELLO_IN, ""),
        ("firma_img", "firma_path", ANCHO_FIRMA_IN, LINEA_FIRMA_DEFECTO),
    ]:
        ruta = config.get(clave_ruta)
        if ruta and os.path.exists(ruta):
            ctx[clave] = InlineImage(tpl, ruta, width=Inches(ancho))
        else:
            ctx[clave] = valor_vacio
    tpl.render(ctx)
    # ¡Nunca tpl.get_docx() después de render()! get_docx() llama a
    # init_docx(reload=True), que como is_rendered ya quedó en True acá
    # vuelve a LEER la plantilla original sin renderizar, pisando el
    # resultado. El documento ya renderizado vive en el atributo .docx.
    return tpl.docx


def combinar_cartas(documentos):
    """Une varios Document (uno por carta) en 1 solo .docx, con un salto
    de página entre cada uno — ninguno antes del primero, igual que
    is_first_page en el motor anterior."""
    maestro = documentos[0]
    composer = Composer(maestro)
    for doc in documentos[1:]:
        composer.doc.add_page_break()
        composer.append(doc)
    return maestro


def contexto_de_ejemplo():
    """Contexto con datos ficticios — para validar que una plantilla
    recién subida al menos renderiza sin errores de sintaxis en sus
    marcadores antes de guardarla (ver procesos.forms.validar_plantilla
    más abajo), y como referencia rápida de qué forma tienen los datos
    reales de construir_contexto()."""
    return {
        "juzgado_nombre": "PRIMER JUZGADO DE POLICÍA LOCAL (EJEMPLO)",
        "ciudad": "Maipú",
        "direccion_juzgado": "Av. Pajaritos 1234",
        "fecha_hoy": fecha_hoy(),
        "nombre_infractor": "JUAN EJEMPLO PÉREZ",
        "domicilio_infractor": "Calle Ejemplo 123",
        "comuna_infractor": "Maipú",
        "rut_infractor": "11.111.111-1",
        "vehiculo": "Chevrolet Spark",
        "patente": "AAAA11-1",
        "texto_citacion": "(texto de ejemplo del párrafo de citación, en la carta real "
                           "acá va el texto legal completo)",
        "numero_carta": 1,
        "reg": "R-0000",
        "monto_total_txt": "2 UTM ($130.000)",
        "nombre_firmante": "NOMBRE DE EJEMPLO",
        "cargo_firma": "SECRETARIO",
        "bloque_postal": BLOQUE_POSTAL_DEFECTO,
        "causas": [
            {"nro_parte": "1001", "rol": "1-T-2026", "fecha": "01/09/2026", "hora": "08:00",
             "lugar": "Autopista Ejemplo", "disposicion": "TAG no vigente", "monto_txt": "1 UTM ($65.000)"},
            {"nro_parte": "1002", "rol": "PENDIENTE", "fecha": "02/09/2026", "hora": "09:00",
             "lugar": "Autopista Ejemplo 2", "disposicion": "TAG no vigente", "monto_txt": "1 UTM ($65.000)"},
        ],
    }


def validar_plantilla(archivo):
    """Intenta renderizar `archivo` (ruta o archivo subido) con
    contexto_de_ejemplo(), sin imágenes reales, para detectar errores de
    sintaxis de marcadores (ej. un {%tr for %} sin su {%tr endfor %})
    ANTES de guardar la plantilla — mejor que recién enterarse la
    próxima vez que alguien suba una nómina y se rompan todas las
    cartas. Devuelve (es_valida, mensaje_error)."""
    try:
        tpl = DocxTemplate(archivo)
        ctx = contexto_de_ejemplo()
        ctx["logo_img"] = ""
        ctx["sello_img"] = ""
        ctx["firma_img"] = LINEA_FIRMA_DEFECTO
        tpl.render(ctx)
    except Exception as exc:
        return False, str(exc)
    return True, ""


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

    `config["plantilla_word_path"]`, si se indica, es la plantilla .docx
    propia del Juzgado; si falta o no existe, se usa PLANTILLA_DEFECTO.

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

    ruta_plantilla = config.get("plantilla_word_path")
    if not ruta_plantilla or not os.path.exists(ruta_plantilla):
        ruta_plantilla = PLANTILLA_DEFECTO

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
            documentos_carta = []
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
                contexto = construir_contexto(pg, slot, numero_carta_global, config)
                documentos_carta.append(renderizar_carta(ruta_plantilla, contexto, config))
            doc_final = combinar_cartas(documentos_carta)
            doc_final.save(nombre_archivo)
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
