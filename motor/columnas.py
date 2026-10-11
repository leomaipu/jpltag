"""
Lectura flexible de columnas del Excel de nómina.

Porteado de generar_notificaciones_JPL1.py (y equivalentes de JPL2/JPL3):
el Excel real trae encabezados con variaciones menores, así que se busca
por patrones en minúscula en vez de un nombre de columna exacto.
"""

import datetime as _dt

import pandas as pd

MAPEO = {
    "NRO_PARTE": ["número de parte", "nro parte", "numero de parte"],
    "FECHA_INF": ["fecha infracción", "fecha infrac", "fecha inf"],
    "HORA_INF": ["hora infracción", "hora infrac", "hora inf", "hora"],
    "LUGAR": ["lugar de la infracción", "lugar de la", "lugar"],
    "DISPOSICION": ["disposiciones infringidas", "disposicion", "disposición"],
    "PATENTE": ["patente"],
    "DIG_PAT": ["digito patente", "digito pate", "dígito patente"],
    "ROL": ["rol", "folio proceso", "folio_proceso", "nro rol"],
    "REG": ["folio carta tramite", "reg n", "registro"],
    "RUT": ["cedula de identidad", "cedula", "rut"],
    "DV": ["digito verifcador", "digito verif", "dígito verif", "dv"],
    "NOMBRE": ["nombre infractor", "nombre inf", "nombre"],
    "AP1": ["apellido 1", "ap1"],
    "AP2": ["apellido 2", "ap2"],
    "DOMICILIO": ["domicilio infractor", "domicilio inf", "domicilio"],
    "COMUNA": ["comuna"],
    "MARCA": ["marca del vehiculo", "marca del", "marca"],
    "MODELO": ["modelo del vehiculo", "modelo del", "modelo"],
}


def get_col(row, key):
    """Busca en `row` la primera columna cuyo nombre contenga alguno de los
    patrones definidos en MAPEO para `key`, y devuelve su valor como texto
    (ya formateado si era fecha/hora). Cadena vacía si no hay match o el
    valor está vacío."""
    for pattern in MAPEO.get(key, []):
        for col in row.index:
            if pattern in str(col).lower().strip():
                val = row[col]
                if pd.isna(val) or str(val).strip() == "":
                    return ""
                if isinstance(val, pd.Timestamp):
                    return val.strftime("%d/%m/%Y")
                if isinstance(val, _dt.datetime):
                    return val.strftime("%d/%m/%Y")
                if isinstance(val, _dt.date):
                    return val.strftime("%d/%m/%Y")
                if isinstance(val, _dt.time):
                    return val.strftime("%H:%M")
                s = str(val).strip()
                return s[:-2] if s.endswith(".0") else s
    return ""


def parsear_fecha(fecha_str):
    """Devuelve (año, mes, día) o (None, None, None) si no reconoce el formato."""
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"]:
        try:
            d = _dt.datetime.strptime(str(fecha_str).strip(), fmt)
            return d.year, d.month, d.day
        except Exception:
            pass
    return None, None, None
