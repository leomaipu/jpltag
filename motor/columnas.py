"""
Lectura flexible de columnas del Excel de nómina.

Porteado de generar_notificaciones_JPL1.py (y equivalentes de JPL2/JPL3):
el Excel real trae encabezados con variaciones menores, así que se busca
por patrones en minúscula en vez de un nombre de columna exacto.
"""

import datetime as _dt
import re

import pandas as pd

# El ROL de la causa se lee de la columna «ROL» de la nómina y se imprime tal
# cual (p. ej. «13Y95046/26»). Esa columna se reconoce SOLO por su encabezado
# completo: un patrón por subcadena tomaría «ROLMOP» o «Control» como ROL.
ENCABEZADOS_ROL = ("rol", "rol causa", "rol_causa", "n° rol", "nº rol", "n.º rol", "nro rol")

# El tipo de vehículo («SEDAN», «STATION WAGON»…) también se reconoce SOLO por
# su encabezado completo: «vehiculo» por subcadena tomaría «MARCA DEL VEHICULO».
ENCABEZADOS_TIPO_VEHICULO = (
    "vehiculo", "vehículo", "tipo vehiculo", "tipo vehículo",
    "tipo de vehiculo", "tipo de vehículo",
)

# Claves que se buscan por encabezado completo y no por subcadena.
CLAVES_ENCABEZADO_EXACTO = ("ROL", "TIPO_VEH")

MAPEO = {
    "NRO_PARTE": ["número de parte", "nro parte", "numero de parte"],
    "FECHA_INF": ["fecha infracción", "fecha infrac", "fecha inf"],
    "HORA_INF": ["hora infracción", "hora infrac", "hora inf", "hora"],
    "LUGAR": ["lugar de la infracción", "lugar de la", "lugar"],
    "DISPOSICION": ["disposiciones infringidas", "disposicion", "disposición"],
    "PATENTE": ["patente"],
    "DIG_PAT": ["digito patente", "digito pate", "dígito patente"],
    "TIPO_VEH": list(ENCABEZADOS_TIPO_VEHICULO),
    "ROL": list(ENCABEZADOS_ROL),   # se busca por encabezado exacto (ver get_col)
    "FECHA_CITA": ["fecha citación", "fecha citacion", "fecha de citación", "fecha de citacion"],
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


def _normalizar(encabezado):
    """Encabezado en minúscula, sin espacios de más (« Rol  » -> «rol»)."""
    return " ".join(str(encabezado).lower().split())


def tiene_columna(df, clave):
    """¿Alguna columna del DataFrame calza con los patrones de `clave`?"""
    return any(
        patron in str(col).lower().strip()
        for patron in MAPEO.get(clave, []) for col in df.columns
    )


def columna_rol(df):
    """La columna del ROL de la nómina (encabezado exacto), o None."""
    por_nombre = {_normalizar(c): c for c in df.columns}
    for nombre in ENCABEZADOS_ROL:
        if nombre in por_nombre:
            return por_nombre[nombre]
    return None


def get_col(row, key):
    """Busca en `row` la primera columna cuyo nombre contenga alguno de los
    patrones definidos en MAPEO para `key`, y devuelve su valor como texto
    (ya formateado si era fecha/hora). Cadena vacía si no hay match o el
    valor está vacío."""
    for pattern in MAPEO.get(key, []):
        for col in row.index:
            calza = (_normalizar(col) == pattern) if key in CLAVES_ENCABEZADO_EXACTO else (pattern in str(col).lower().strip())
            if calza:
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
                if isinstance(val, float) and val.is_integer():
                    return str(int(val))
                s = str(val).strip()
                # Solo un número entero leído como decimal («15601565.0») pierde
                # el «.0»; un texto como «SPORTAGE 2.0» o «SANTA RITA .0» queda igual.
                return s[:-2] if re.fullmatch(r"\d+\.0", s) else s
    return ""


def parsear_fecha(fecha_str):
    """Devuelve (año, mes, día) o (None, None, None) si no reconoce el formato."""
    for fmt in ["%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y",
                "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S",
                "%d/%m/%Y %H:%M", "%Y-%m-%d %H:%M"]:
        try:
            d = _dt.datetime.strptime(str(fecha_str).strip(), fmt)
            return d.year, d.month, d.day
        except Exception:
            pass
    return None, None, None
