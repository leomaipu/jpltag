"""
Agrupación de infracciones por patente, detección del mes predominante de
la nómina, y filtrado por el rango de días que le corresponde a cada
juzgado. Porteado de generar_notificaciones_JPL1.py.
"""

from collections import Counter

from .columnas import get_col, parsear_fecha
from .multas import buscar_multa


def _rut_de(row):
    """RUT de la fila, comparable (sin puntos, en mayúscula, con DV si va aparte)."""
    rut, dv = get_col(row, "RUT"), get_col(row, "DV")
    texto = f"{rut}-{dv}" if rut and dv else rut
    return texto.replace(".", "").replace(" ", "").upper()


def _claves_por_patente(df):
    """{patente: [RUT en orden de aparición]} para las patentes que figuran
    con más de un RUT: vendida a otra persona = otra carta."""
    ruts = {}
    for _, row in df.iterrows():
        pat = get_col(row, "PATENTE").upper().strip()
        rut = _rut_de(row)
        if pat and rut and rut not in ruts.setdefault(pat, []):
            ruts[pat].append(rut)
    return {pat: lista for pat, lista in ruts.items() if len(lista) > 1}


def construir_grupos(df, tabla_multas, utm_multa_defecto):
    """Devuelve (grupos, patentes_sin_causas_validas, avisos).

    Una patente con más de un RUT en la nómina (el vehículo cambió de dueño)
    forma un grupo por RUT: cada persona recibe su propia carta con sus
    propias causas. La clave del grupo es la patente, o «PATENTE · RUT» en
    ese caso; `grupo["placa"]` siempre trae la patente.

    Una fila se considera CAUSA VÁLIDA solo si trae N° de parte o fecha de
    infracción; si no trae ninguno de los dos, se descarta (no se cuenta ni
    aparece en la carta). Si una patente se queda sin ninguna causa válida,
    se excluye por completo.
    """
    grupos = {}
    errores_patente = []
    causas_vacias = 0
    avisos_no_encontrados = set()
    varios_rut = _claves_por_patente(df)
    filas_sin_rut = 0

    for idx, row in df.iterrows():
        pat = get_col(row, "PATENTE").upper().strip()
        if not pat:
            errores_patente.append(idx + 2)
            continue

        placa = pat
        if pat in varios_rut:
            rut_fila = _rut_de(row)
            if not rut_fila:
                filas_sin_rut += 1
            # Una fila sin RUT en una patente con varios queda con el primero.
            pat = f"{placa} · {rut_fila or varios_rut[placa][0]}"

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
                "placa": placa,
                "digPat": get_col(row, "DIG_PAT"),
                "rol_raw": get_col(row, "ROL"),
                "reg_raw": get_col(row, "REG"),
                "nombre": nombre or "—",
                "rut": f"{rut}-{dv}" if rut and dv else rut or "—",
                "domicilio": get_col(row, "DOMICILIO") or "—",
                "comuna": get_col(row, "COMUNA") or "—",
                # "MARCA, MODELO" — así lo escriben las cartas oficiales de los Juzgados.
                # Sin marca ni modelo, el tipo de vehículo de la nómina.
                "vehiculo": (", ".join(filter(None, [get_col(row, "MARCA"), get_col(row, "MODELO")]))
                             or get_col(row, "TIPO_VEH") or "—"),
                "causas": [],
                # Fechas de citación que trae la nómina (ISO), para elegir la de la carta.
                "citaciones": [],
            }

        cita_row = get_col(row, "FECHA_CITA")
        if cita_row:
            a_c, m_c, d_c = parsear_fecha(cita_row)
            if a_c:
                iso = f"{a_c:04d}-{m_c:02d}-{d_c:02d}"
                if iso not in grupos[pat]["citaciones"]:
                    grupos[pat]["citaciones"].append(iso)
            else:
                grupos[pat]["citacion_ilegible"] = True

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

    for g in grupos.values():
        # Regla provisoria: con fechas distintas en una misma patente, la más
        # temprana; el resultado avisa para que el juzgado decida (ver generacion).
        if g["citaciones"]:
            a_c, m_c, d_c = (int(x) for x in min(g["citaciones"]).split("-"))
            g["citacion"] = f"{d_c:02d}/{m_c:02d}/{a_c:04d}"

    patentes_sin_causas = {pat: g for pat, g in grupos.items() if not g["causas"]}
    for pat in patentes_sin_causas:
        del grupos[pat]

    avisos = {
        "filas_sin_patente": len(errores_patente),
        "causas_vacias": causas_vacias,
        "patentes_sin_causas_validas": len(patentes_sin_causas),
        "patentes_con_varios_rut": len(varios_rut),
        "filas_sin_rut_en_patente_con_varios_rut": filas_sin_rut,
        # Patentes (no filas) sin ninguna fecha legible: no se pueden asignar a un juzgado.
        "patentes_con_fecha_ilegible": sum(1 for g in grupos.values() if "mes" not in g),
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
                # `clave` identifica el grupo (patente, o «PATENTE · RUT»);
                # `patente` es la placa que se imprime.
                "clave": pat, "patente": info.get("placa", pat), "info": info, "chunk": chunk,
                "parte": i, "total_partes": total_partes, "total_causas": total_causas,
            })
    return paginas
