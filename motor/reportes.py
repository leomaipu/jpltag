"""
Reportes auxiliares del proceso de generación de cartas (Módulos 3.1 y 6
de los scripts originales): exportar las filas con N° de parte duplicado
para auditoría, y dejar un log de las causas/patentes que no terminaron
en ninguna carta (y por qué). Porteado de generar_notificaciones_JPL1.py.
"""

import csv


def exportar_duplicados(base, df_duplicados):
    """Exporta a un Excel aparte las filas con N° de parte duplicado (ya
    separadas por motor.lectura.detectar_duplicados), para auditoría —
    nunca entran a construir_grupos, así que nunca generan carta ni se
    cobran dos veces. Devuelve la ruta del archivo, o None si no había
    duplicados."""
    if df_duplicados is None or len(df_duplicados) == 0:
        return None
    nombre_archivo = f"{base}_duplicados_reporte.xlsx"
    df_duplicados.to_excel(nombre_archivo, index=False)
    return nombre_archivo


def escribir_log_errores(base, sin_fecha, otro_mes, sin_causas, patentes_sin_cupo, grupos_ref,
                         fuera_de_rango=None):
    """Log CSV [ID_Causa, Numero_Parte, Motivo_Error, Detalle_Error] de las
    causas/patentes que no terminaron en ninguna carta de este juzgado, y
    por qué: fecha ilegible, infracción de otro mes, patente sin ninguna
    causa válida, sin cupo de agenda, o de un día que ningún juzgado
    procesa (`fuera_de_rango`). Las patentes del rango de OTRO juzgado no
    se anotan: las recibe ese juzgado. Devuelve la ruta del archivo, o
    None si no hubo nada que registrar."""
    filas = []
    id_causa = 0

    for pat, g in sin_fecha.items():
        for c in g["causas"]:
            id_causa += 1
            filas.append({
                "ID_Causa": id_causa, "Numero_Parte": c["nroParte"],
                "Motivo_Error": "FECHA_ILEGIBLE",
                "Detalle_Error": f"Patente {pat}: fecha ilegible ('{c['fecha']}')",
            })

    for pat, g in otro_mes.items():
        for c in g["causas"]:
            id_causa += 1
            filas.append({
                "ID_Causa": id_causa, "Numero_Parte": c["nroParte"],
                "Motivo_Error": "DATOS_INCOMPLETOS",
                "Detalle_Error": f"Patente {pat}: infracción de otro mes ({g.get('mes')}/{g.get('anio')})",
            })

    for pat, g in (sin_causas or {}).items():
        id_causa += 1
        filas.append({
            "ID_Causa": id_causa, "Numero_Parte": "—",
            "Motivo_Error": "DATOS_INCOMPLETOS",
            "Detalle_Error": f"Patente {pat}: todas sus filas venían sin N° de parte ni fecha",
        })

    for pat, g in (fuera_de_rango or {}).items():
        for c in g["causas"]:
            id_causa += 1
            filas.append({
                "ID_Causa": id_causa, "Numero_Parte": c["nroParte"],
                "Motivo_Error": "FUERA_DE_TODO_RANGO",
                "Detalle_Error": f"Patente {pat}: el día {g['dia']} no lo procesa ningún juzgado activo",
            })

    for pat in (patentes_sin_cupo or []):
        g = grupos_ref.get(pat, {})
        nro = g["causas"][0]["nroParte"] if g.get("causas") else "—"
        id_causa += 1
        filas.append({
            "ID_Causa": id_causa, "Numero_Parte": nro,
            "Motivo_Error": "SIN_CUPOS_CALENDARIO",
            "Detalle_Error": f"Patente {pat}: no había horario disponible en la agenda/calendario",
        })

    if not filas:
        return None
    nombre_archivo = f"{base}_log_errores_procesamiento.csv"
    with open(nombre_archivo, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["ID_Causa", "Numero_Parte", "Motivo_Error", "Detalle_Error"]
        )
        writer.writeheader()
        for fila in filas:
            writer.writerow(fila)
    return nombre_archivo
