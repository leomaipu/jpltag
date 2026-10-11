"""
Agrupación de infracciones por patente, detección del mes predominante de
la nómina, y filtrado por el rango de días que le corresponde a cada
juzgado. Porteado de generar_notificaciones_JPL1.py.
"""

from collections import Counter

from .columnas import get_col, parsear_fecha
from .multas import buscar_multa


def construir_grupos(df, tabla_multas, utm_multa_defecto):
    """Devuelve (grupos, patentes_sin_causas_validas, avisos).

    Una fila se considera CAUSA VÁLIDA solo si trae N° de parte o fecha de
    infracción; si no trae ninguno de los dos, se descarta (no se cuenta ni
    aparece en la carta). Si una patente se queda sin ninguna causa válida,
    se excluye por completo.
    """
    grupos = {}
    errores_fecha = []
    errores_patente = []
    causas_vacias = 0
    avisos_no_encontrados = set()

    for idx, row in df.iterrows():
        pat = get_col(row, "PATENTE").upper().strip()
        if not pat:
            errores_patente.append(idx + 2)
            continue

        nro_parte_row = get_col(row, "NRO_PARTE")
        fecha_row = get_col(row, "FECHA_INF")
        if not nro_parte_row and not fecha_row:
            causas_vacias += 1
            continue

        if pat not in grupos:
            nombre = " ".join(filter(None, [
                get_col(row, "NOMBRE"), get_col(row, "AP1"), get_col(row, "AP2"),
            ])).strip()
            rut = get_col(row, "RUT")
            dv = get_col(row, "DV")
            grupos[pat] = {
                "digPat": get_col(row, "DIG_PAT"),
                "rol_raw": get_col(row, "ROL"),
                "reg_raw": get_col(row, "REG"),
                "nombre": nombre or "—",
                "rut": f"{rut}-{dv}" if rut and dv else rut or "—",
                "domicilio": get_col(row, "DOMICILIO") or "—",
                "comuna": get_col(row, "COMUNA") or "—",
                "vehiculo": f"{get_col(row, 'MARCA')} {get_col(row, 'MODELO')}".strip() or "—",
                "causas": [],
            }

        disposicion_row = get_col(row, "DISPOSICION") or "—"
        grupos[pat]["causas"].append({
            "nroParte": nro_parte_row or "—",
            "rol_raw": get_col(row, "ROL"),
            "fecha": fecha_row or "—",
            "hora": get_col(row, "HORA_INF") or "—",
            "lugar": get_col(row, "LUGAR") or "—",
            "disposicion": disposicion_row,
            "monto": buscar_multa(disposicion_row, tabla_multas, utm_multa_defecto, avisos_no_encontrados),
        })

        if "mes" not in grupos[pat]:
            anio, mes, dia = parsear_fecha(fecha_row)
            if mes and dia:
                grupos[pat]["anio"] = anio
                grupos[pat]["mes"] = mes
                grupos[pat]["dia"] = dia
            else:
                errores_fecha.append((idx + 2, pat, fecha_row))

    patentes_sin_causas = {pat: g for pat, g in grupos.items() if not g["causas"]}
    for pat in patentes_sin_causas:
        del grupos[pat]

    avisos = {
        "filas_sin_patente": len(errores_patente),
        "causas_vacias": causas_vacias,
        "patentes_sin_causas_validas": len(patentes_sin_causas),
        "patentes_con_fecha_ilegible": len(errores_fecha),
        "disposiciones_sin_multa_definida": sorted(avisos_no_encontrados),
    }
    return grupos, patentes_sin_causas, avisos


def detectar_mes_predominante(grupos):
    conteo_mes = Counter()
    for g in grupos.values():
        if "mes" in g:
            conteo_mes[(g["anio"], g["mes"])] += len(g["causas"])
    if not conteo_mes:
        return None, None
    (anio_pred, mes_pred), _ = conteo_mes.most_common(1)[0]
    return anio_pred, mes_pred


def filtrar_por_rango_dias(grupos, anio_pred, mes_pred, dia_desde, dia_hasta):
    """Devuelve (incluidos, sin_fecha, otro_mes, otro_rango)."""
    incluidos, sin_fecha, otro_mes, otro_rango = {}, {}, {}, {}
    for pat, g in grupos.items():
        if "mes" not in g:
            sin_fecha[pat] = g
            continue
        if g["anio"] != anio_pred or g["mes"] != mes_pred:
            otro_mes[pat] = g
            continue
        if dia_desde <= g["dia"] <= dia_hasta:
            incluidos[pat] = g
        else:
            otro_rango[pat] = g
    return incluidos, sin_fecha, otro_mes, otro_rango


def expandir_en_paginas(grupos, max_causas_por_pagina):
    """Divide las causas de cada patente en páginas de a lo más
    `max_causas_por_pagina`, para no desbordar el layout de la carta."""
    paginas = []
    for pat, info in grupos.items():
        causas = info["causas"]
        chunks = [causas[i:i + max_causas_por_pagina] for i in range(0, len(causas), max_causas_por_pagina)]
        total_partes = len(chunks)
        total_causas = len(causas)
        for i, chunk in enumerate(chunks, 1):
            paginas.append({
                "patente": pat, "info": info, "chunk": chunk,
                "parte": i, "total_partes": total_partes, "total_causas": total_causas,
            })
    return paginas
