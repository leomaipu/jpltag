"""
Cartas del Juzgado generadas a partir de plantillas Word editables — una
por Juzgado y por TIPO de carta.

Tipos de carta (los tres existen en los 3 Juzgados):

    CITACION             cita a la audiencia (Art. 3° Ley 18.287)
    CITACION_SENTENCIA   cita y avisa que, de no concurrir, queda condenado
    SENTENCIA            notifica la sentencia ya dictada (Art. 18° Ley 18.287)

El texto legal de cada carta vive en el propio .docx de cada Juzgado
(motor/plantillas_defecto/jplN/<tipo>.docx, adaptadas de los ejemplos que
entregó cada Juzgado; cada Juzgado puede subir las suyas desde /plantilla/,
ver nucleo.models.Juzgado). Este módulo solo rellena los marcadores
docxtpl (estilo Jinja2) con los datos reales de cada carta.

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
    vehiculo               "MARCA, MODELO"
    patente                con dígito verificador si lo hay, ej. "AAAA11-1"
    clausula_audiencia     "el día 06/10/2026, a las 09:30 hrs." (o "en fecha
                            y hora a coordinar con el Juzgado" si no hay cupo
                            en la agenda) — solo en cartas con citación
    fecha_audiencia        "06/10/2026": la fecha de la audiencia (citación)
                            o de la sentencia (carta de sentencia)
    hora_audiencia         "09:30" (vacío en sentencia o sin agenda)
    condena_monto          "1 UTM" — monto de la condena por infracción
                            (si las infracciones de la patente tienen multas
                            distintas, "la multa indicada")
    parte, total_partes    si la patente tiene más infracciones que caben en
                            una página, la carta sale en varias partes
    causa_desde, causa_hasta, total_causas
                            "infracciones 21 a 25 de 25"
    numero_carta           correlativo único en todo el envío
    reg                    folio/Reg N°, o "«folio carta trámite»" si no hay
    monto_total_txt        ej. "3 UTM ($195.000)"
    nombre_firmante
    cargo_firma            "SECRETARIO" / "SECRETARIA" / ...
    bloque_postal          ej. "SANTIAGO 16" (franqueo/zona de Correos)
    texto_citacion         (compatibilidad) párrafo de citación ya armado

Imágenes — se usan igual, con {{ }}, pero son opcionales: si el Juzgado
no cargó esa imagen (ver Juzgado.logo/sello_judicial/firma), quedan
vacías (sello_img, firma_foto) o con una línea punteada en vez de firma
(linea_firma, firma_img); sin logo propio se usa el logo municipal (LOGO_DEFECTO):

    logo_img
    sello_img
    firma_foto      solo la imagen de la firma ("" si no hay)
    linea_firma     solo la línea punteada ("" si hay firma cargada)
    firma_img       lo uno o lo otro: la imagen, o la línea punteada

Tabla de causas — una fila por infracción de esta carta. Para que
docxtpl repita la fila de la tabla hacen falta 3 filas en la plantilla
(ver motor/plantillas_defecto/): una fila que solo diga
"{%tr for causa in causas %}", la fila con los datos reales
({{ causa.rol }}, etc.) y una fila que solo diga "{%tr endfor %}".

    causas[].nro_parte
    causas[].rol
    causas[].fecha
    causas[].hora
    causas[].lugar
    causas[].disposicion
    causas[].monto_utm     "1 UTM"
    causas[].monto_txt     "1 UTM ($65.000)"
"""

import os
import re
import traceback

import pandas as pd
from docx.shared import Inches
from docxcompose.composer import Composer
from docxtpl import DocxTemplate, InlineImage

from .agenda import SLOT_SIN_AGENDA
from .agrupacion import expandir_en_paginas
from .revisiones import plantilla_limpia
from .cartas import (
    BLOQUE_POSTAL_DEFECTO,
    ENTIDAD_DENUNCIANTE_DEFECTO,
    MOTIVO_INFRACCION_DEFECTO,
    ahora_chile,
    clausula_audiencia,
    condena_monto_txt,
    fecha_hoy,
    fmt_utm,
    fmt_utm_simple,
    formatear_rol,
    patente_completa,
)

TIPO_CITACION = "CITACION"
TIPO_CITACION_SENTENCIA = "CITACION_SENTENCIA"
TIPO_SENTENCIA = "SENTENCIA"
TIPOS_CARTA = (TIPO_CITACION, TIPO_CITACION_SENTENCIA, TIPO_SENTENCIA)

DIR_DEFECTO = os.path.join(os.path.dirname(__file__), "plantillas_defecto")
LOGO_DEFECTO = os.path.join(DIR_DEFECTO, "logo_maipu.png")

# Anchos de imagen. No son ajustables desde la plantilla: InlineImage
# necesita el ancho en el momento de renderizar, no se puede fijar solo
# con Word.
ANCHO_LOGO_IN = 0.5      # el logo es vertical (~0.69 de ancho/alto): ~0.73 in de alto
ANCHO_SELLO_IN = 0.6
ANCHO_FIRMA_IN = 1.2

# Infracciones que caben en una página con las plantillas por defecto (ver
# el comentario de nucleo.models.Juzgado.max_causas_por_pagina).
MAX_CAUSAS_POR_PAGINA_DEFECTO = 12

LINEA_FIRMA_DEFECTO = "........................................."

_ORDINALES = {"PRIMER": 1, "SEGUNDO": 2, "TERCER": 3}


class PlantillaNoDisponible(Exception):
    """No hay plantilla utilizable para ese Juzgado y tipo de carta."""


def numero_juzgado(slug, nombre=""):
    """1, 2 o 3 según el slug ('jpl2') o, si el slug no sigue ese patrón,
    el nombre ('SEGUNDO JUZGADO DE POLICÍA LOCAL'). None si no se puede
    deducir."""
    m = re.search(r"(\d+)", slug or "")
    if m:
        return int(m.group(1))
    for palabra, n in _ORDINALES.items():
        if palabra in (nombre or "").upper():
            return n
    return None


def ruta_plantilla_defecto(slug, nombre, tipo):
    """Ruta de la plantilla del repo para ese Juzgado y tipo, o None."""
    n = numero_juzgado(slug, nombre)
    if n is None:
        return None
    ruta = os.path.join(DIR_DEFECTO, f"jpl{n}", f"{tipo.lower()}.docx")
    return ruta if os.path.exists(ruta) else None


def resolver_plantilla(config, tipo):
    """La plantilla propia del Juzgado para `tipo` si la subió
    (config["plantillas_word"][tipo]); si no, la del repo. Si no hay
    ninguna, FALLA con un mensaje claro: usar a ciegas el texto legal de
    otro Juzgado sería peor que no generar."""
    if tipo not in TIPOS_CARTA:
        raise PlantillaNoDisponible(f"Tipo de carta desconocido: {tipo!r}.")
    propia = (config.get("plantillas_word") or {}).get(tipo)
    if propia and os.path.exists(propia):
        return propia
    defecto = ruta_plantilla_defecto(config.get("slug_juzgado"), config.get("nombre_juzgado"), tipo)
    if defecto:
        return defecto
    raise PlantillaNoDisponible(
        f"No hay plantilla de {tipo} para el juzgado "
        f"{config.get('nombre_juzgado') or config.get('slug_juzgado')!r}: "
        "súbela en la pantalla Plantilla."
    )


def construir_contexto(pg, slot_audiencia, numero_carta, config):
    """Arma el dict de datos de UNA carta (una patente/página) — lo que
    la plantilla puede leer con {{ }}. Las imágenes se agregan aparte,
    en renderizar_carta(), porque InlineImage necesita la instancia de
    DocxTemplate que las va a insertar."""
    tipo = config.get("tipo_carta", TIPO_CITACION)
    ciudad = config.get("ciudad", "Maipú")
    utm_valor = config["utm_valor"]
    entidad_denunciante = config.get("entidad_denunciante") or ENTIDAD_DENUNCIANTE_DEFECTO
    motivo_infraccion = config.get("motivo_infraccion") or MOTIVO_INFRACCION_DEFECTO
    bloque_postal = config.get("bloque_postal") or BLOQUE_POSTAL_DEFECTO
    max_causas = config.get("max_causas_por_pagina", MAX_CAUSAS_POR_PAGINA_DEFECTO)

    pat = pg["patente"]
    info = pg["info"]
    chunk = pg["chunk"]
    parte = pg["parte"]
    total_partes = pg["total_partes"]
    total_causas = pg["total_causas"]
    pat_completa = patente_completa(pat, info["digPat"])
    causa_desde = (parte - 1) * max_causas + 1
    causa_hasta = (parte - 1) * max_causas + len(chunk)

    if tipo == TIPO_SENTENCIA:
        fecha_aud, hora_aud = config.get("fecha_audiencia") or "", ""
    else:
        fecha_aud, hora_aud = slot_audiencia
    hora_txt = f"{hora_aud} hrs." if hora_aud else ""
    anio_actual = ahora_chile().year
    reg = info.get("reg_raw") or "«folio carta trámite»"
    utm_total = sum(c["monto"] for c in info["causas"])

    oportunidad_txt = "1 oportunidad" if total_causas == 1 else f"{total_causas} oportunidades"
    texto_citacion = (
        f"De conformidad al Art. 3° de la Ley 18.287, cítese a don (a): {info['nombre']}, "
        f"propietario (a) del vehículo placa patente {pat_completa}, denunciado por la "
        f"{entidad_denunciante}, por {motivo_infraccion} en {oportunidad_txt}"
        + (f" — parte {parte} de {total_partes}, infracciones {causa_desde} a {causa_hasta}"
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
        "tipo_carta": tipo,
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
        "clausula_audiencia": "" if tipo == TIPO_SENTENCIA else clausula_audiencia(fecha_aud, hora_txt),
        "fecha_audiencia": fecha_aud,
        "hora_audiencia": hora_aud,
        "condena_monto": condena_monto_txt(info["causas"]),
        "parte": parte,
        "total_partes": total_partes,
        "causa_desde": causa_desde,
        "causa_hasta": causa_hasta,
        "total_causas": total_causas,
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
                "monto_utm": fmt_utm_simple(c["monto"]),
                "monto_txt": fmt_utm(c["monto"], utm_valor),
            }
            for c in chunk
        ],
    }


def renderizar_carta(ruta_plantilla, contexto, config):
    """Renderiza UNA carta con docxtpl y devuelve el Document en memoria
    (python-docx), sin guardar a disco todavía."""
    # Sin control de cambios ni comentarios: si no, saldrían impresos.
    tpl = DocxTemplate(plantilla_limpia(ruta_plantilla))
    ctx = dict(contexto)
    for clave, clave_ruta, ancho, valor_vacio in [
        ("logo_img", "logo_path", ANCHO_LOGO_IN, ""),
        ("sello_img", "sello_path", ANCHO_SELLO_IN, ""),
        ("firma_foto", "firma_path", ANCHO_FIRMA_IN, ""),
    ]:
        ruta = config.get(clave_ruta)
        if clave == "logo_img" and not (ruta and os.path.exists(ruta)):
            ruta = LOGO_DEFECTO
        if ruta and os.path.exists(ruta):
            ctx[clave] = InlineImage(tpl, ruta, width=Inches(ancho))
        else:
            ctx[clave] = valor_vacio
    # Firma: la imagen si el Juzgado la cargó; si no, una línea punteada
    # sobre el nombre (cartas que se firman a mano). `firma_img` es lo uno
    # o lo otro (compatibilidad); las plantillas por defecto usan
    # firma_foto (solo la imagen) y linea_firma (solo la línea).
    hay_firma = bool(ctx["firma_foto"])
    ctx["linea_firma"] = "" if hay_firma else LINEA_FIRMA_DEFECTO
    ctx["firma_img"] = ctx["firma_foto"] if hay_firma else LINEA_FIRMA_DEFECTO
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


def contexto_de_ejemplo(tipo=TIPO_CITACION):
    """Contexto con datos ficticios — para validar que una plantilla
    recién subida renderiza sin errores y trae los datos esenciales
    (ver validar_plantilla), y como referencia rápida de qué forma tienen
    los datos reales de construir_contexto()."""
    ctx = {
        "tipo_carta": tipo,
        "juzgado_nombre": "PRIMER JUZGADO DE POLICÍA LOCAL (EJEMPLO)",
        "ciudad": "Maipú",
        "direccion_juzgado": "Av. Pajaritos 1234",
        "fecha_hoy": fecha_hoy(),
        "nombre_infractor": "JUAN EJEMPLO PÉREZ",
        "domicilio_infractor": "Calle Ejemplo 123",
        "comuna_infractor": "Maipú",
        "rut_infractor": "11.111.111-1",
        "vehiculo": "Chevrolet, Spark",
        "patente": "ZZZZ99-9",
        "clausula_audiencia": "el día 15/10/2026, a las 09:30 hrs.",
        "fecha_audiencia": "15/10/2026",
        "hora_audiencia": "09:30",
        "condena_monto": "3 UTM",
        "parte": 1,
        "total_partes": 1,
        "causa_desde": 1,
        "causa_hasta": 2,
        "total_causas": 2,
        "texto_citacion": "(texto de ejemplo del párrafo de citación)",
        "numero_carta": 1,
        "reg": "R-0000",
        "monto_total_txt": "2 UTM ($130.000)",
        "nombre_firmante": "NOMBRE DE EJEMPLO",
        "cargo_firma": "SECRETARIO",
        "bloque_postal": BLOQUE_POSTAL_DEFECTO,
        "causas": [
            {"nro_parte": "1001", "rol": "1-T-2026", "fecha": "01/09/2026", "hora": "08:00",
             "lugar": "Autopista Ejemplo Uno", "disposicion": "TAG no vigente",
             "monto_utm": "1 UTM", "monto_txt": "1 UTM ($65.000)"},
            {"nro_parte": "1002", "rol": "PENDIENTE", "fecha": "02/09/2026", "hora": "09:00",
             "lugar": "Autopista Ejemplo Dos", "disposicion": "TAG no vigente",
             "monto_utm": "1 UTM", "monto_txt": "1 UTM ($65.000)"},
        ],
    }
    if tipo == TIPO_SENTENCIA:
        ctx["clausula_audiencia"] = ""
        ctx["hora_audiencia"] = ""
    return ctx


# Qué debe aparecer, como mínimo, en el texto de una carta renderizada con
# contexto_de_ejemplo(): (valor de ejemplo, marcador, descripción). Si una
# plantilla subida no los trae, está incompleta — p. ej. un Word nuevo
# sin los marcadores — y es mejor rechazarla al subirla que descubrirlo
# imprimiendo cartas sin el nombre ni la patente del infractor.
_OBLIGATORIOS_COMUNES = [
    ("JUAN EJEMPLO PÉREZ", "nombre_infractor", "el nombre del infractor"),
    ("11.111.111-1", "rut_infractor", "el RUT del infractor"),
    ("ZZZZ99-9", "patente", "la patente"),
    ("Autopista Ejemplo Dos", "causas (fila repetida {%tr for causa in causas %})",
     "la tabla con una fila por infracción"),
]
_OBLIGATORIOS_POR_TIPO = {
    TIPO_CITACION: [("el día 15/10/2026, a las 09:30 hrs.", "clausula_audiencia", "la fecha y hora de la audiencia")],
    TIPO_CITACION_SENTENCIA: [
        ("el día 15/10/2026, a las 09:30 hrs.", "clausula_audiencia", "la fecha y hora de la audiencia"),
        ("3 UTM", "condena_monto", "el monto de la condena"),
    ],
    TIPO_SENTENCIA: [
        ("15/10/2026", "fecha_audiencia", "la fecha de la sentencia"),
        ("3 UTM", "condena_monto", "el monto de la condena"),
    ],
}


def texto_de(doc):
    """Texto plano de un Document (párrafos y celdas de tablas, en orden),
    uniendo los runs de cada párrafo para que un dato partido en varios
    runs por el formato igual se encuentre."""
    from docx.oxml.ns import qn
    return "\n".join(
        "".join(t.text or "" for t in p.iter(qn("w:t")))
        for p in doc.element.body.iter(qn("w:p"))
    )


def validar_plantilla(archivo, tipo=None):
    """Intenta renderizar `archivo` (ruta o archivo subido) con
    contexto_de_ejemplo(), sin imágenes reales, para detectar errores de
    sintaxis de marcadores (ej. un {%tr for %} sin su {%tr endfor %}) ANTES
    de guardar la plantilla — mejor que recién enterarse la próxima vez
    que alguien suba una nómina y se rompan todas las cartas.

    Si se indica el `tipo` de carta, además comprueba que la carta
    renderizada contenga los datos esenciales de ese tipo (nombre, RUT,
    patente, tabla de infracciones y la fecha de la audiencia/sentencia).
    Devuelve (es_valida, mensaje_error)."""
    try:
        tpl = DocxTemplate(plantilla_limpia(archivo))
        ctx = contexto_de_ejemplo(tipo or TIPO_CITACION)
        ctx["logo_img"] = ""
        ctx["sello_img"] = ""
        ctx["firma_foto"] = ""
        ctx["linea_firma"] = LINEA_FIRMA_DEFECTO
        ctx["firma_img"] = LINEA_FIRMA_DEFECTO
        tpl.render(ctx)
        texto = texto_de(tpl.docx)
    except Exception as exc:
        return False, str(exc)
    if tipo is not None:
        if tipo not in TIPOS_CARTA:
            return False, f"Tipo de carta desconocido: {tipo!r}."
        faltan = [
            f"{desc} ({{{{ {marcador} }}}})" if "{" not in marcador else f"{desc} ({marcador})"
            for valor, marcador, desc in _OBLIGATORIOS_COMUNES + _OBLIGATORIOS_POR_TIPO[tipo]
            if valor not in texto
        ]
        if faltan:
            return False, "A la plantilla le falta: " + "; ".join(faltan) + "."
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

    `config["tipo_carta"]` elige el tipo de carta (CITACION por defecto,
    CITACION_SENTENCIA o SENTENCIA). La plantilla sale de
    resolver_plantilla(): la propia del Juzgado (config["plantillas_word"],
    un dict tipo -> ruta) o, si no subió, la del repo para su número de
    juzgado; si no hay ninguna, PlantillaNoDisponible. En SENTENCIA,
    config["fecha_audiencia"] es la fecha de la sentencia que se escribe
    en la carta (los `slots` de agenda no se usan).

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

    tipo_carta = config.get("tipo_carta", TIPO_CITACION)
    ruta_plantilla = resolver_plantilla(config, tipo_carta)

    patentes = list(grupos_filtrados.items())
    limite_efectivo = limite_x if limite_x and limite_x > 0 else len(patentes)
    lotes = [patentes[i:i + limite_efectivo] for i in range(0, len(patentes), limite_efectivo)]
    max_causas = config.get("max_causas_por_pagina", MAX_CAUSAS_POR_PAGINA_DEFECTO)

    if carpeta_salida:
        os.makedirs(carpeta_salida, exist_ok=True)
        carpeta = carpeta_salida
    else:
        carpeta = crear_carpeta_envio(f"{base_lote}_Envio")
    resultado["carpeta"] = carpeta

    seguimiento = []
    numero_carta_global = 0
    anio_actual = ahora_chile().year

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
                            "Tipo de carta": tipo_carta,
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
