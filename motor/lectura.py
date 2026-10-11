"""
Lectura del Excel de nómina y control de N° de parte duplicado.

Porteado de generar_notificaciones_JPL1.py. El orden importa: primero se
reemplazan los vacíos (fillna("")) y recién después se detectan duplicados,
para no confundir dos filas sin N° de parte con un duplicado real — ese
orden ya se verificó explícitamente en el diagnóstico del 18-ago-2026.
"""

import pandas as pd

from .columnas import MAPEO


def leer_nomina(ruta_o_archivo) -> pd.DataFrame:
    """Lee el Excel de nómina y reemplaza vacíos por cadena vacía, igual que
    hacen los 6 scripts (`pd.read_excel(ruta_excel).fillna("")`)."""
    return pd.read_excel(ruta_o_archivo).fillna("")


def detectar_duplicados(df: pd.DataFrame):
    """Busca N° de parte repetidos en TODA la nómina (no solo dentro de una
    patente) y los separa antes de agrupar, para que nunca entren dos veces
    a cobro. Devuelve (df_sin_duplicados, df_duplicados)."""
    col_nro_parte = None
    for col in df.columns:
        if any(p in str(col).lower() for p in MAPEO["NRO_PARTE"]):
            col_nro_parte = col
            break
    if col_nro_parte is None:
        return df, df.iloc[0:0]

    valores = df[col_nro_parte].astype(str).str.strip()
    vacios = valores == ""
    duplicado_mask = valores.duplicated(keep=False) & ~vacios
    df_duplicados = df[duplicado_mask].copy()
    df_limpio = df[~duplicado_mask].copy()
    return df_limpio, df_duplicados
