"""
Monto de la multa por tipo de causa (Módulo 5 de los scripts originales):
lee tipo_causas.xlsx y busca, por disposición infringida, cuántas UTM
corresponden. Si no hay tabla o no se reconoce una disposición, usa un
valor fijo (utm_multa_defecto) — igual que el comportamiento actual.
"""

import pandas as pd


def cargar_tabla_multas(ruta_o_archivo):
    """Devuelve una lista de (texto_tipo_causa, monto_utm), ordenada de más
    larga a más corta (para preferir el match más específico). Lista vacía
    si el archivo no existe o no se reconocen sus columnas."""
    try:
        df_multas = pd.read_excel(ruta_o_archivo)
    except Exception:
        return []

    col_tipo = next((c for c in df_multas.columns
                      if "tipo" in str(c).lower() or "disposic" in str(c).lower()), None)
    col_monto = next((c for c in df_multas.columns
                       if "multa" in str(c).lower() or "utm" in str(c).lower()), None)
    if not col_tipo or not col_monto:
        return []

    tabla = []
    for _, fila in df_multas.iterrows():
        texto = str(fila[col_tipo]).strip()
        try:
            monto = float(fila[col_monto])
        except Exception:
            continue
        if texto and texto.lower() != "nan":
            tabla.append((texto, monto))
    tabla.sort(key=lambda x: len(x[0]), reverse=True)
    return tabla


def buscar_multa(disposicion_texto, tabla_multas, utm_multa_defecto, avisos_no_encontrados=None):
    """Busca el monto UTM de una disposición: primero coincidencia exacta,
    después parcial. Si no encuentra nada, devuelve utm_multa_defecto y deja
    constancia en `avisos_no_encontrados` (un set, para avisar una sola vez
    por texto distinto en la pantalla de resultados)."""
    if not disposicion_texto or disposicion_texto == "—":
        return utm_multa_defecto
    texto_l = disposicion_texto.lower().strip()
    for tipo_texto, monto in tabla_multas:
        if tipo_texto.lower().strip() == texto_l:
            return monto
    for tipo_texto, monto in tabla_multas:
        tl = tipo_texto.lower().strip()
        if tl in texto_l or texto_l in tl:
            return monto
    if tabla_multas and avisos_no_encontrados is not None:
        avisos_no_encontrados.add(disposicion_texto)
    return utm_multa_defecto
