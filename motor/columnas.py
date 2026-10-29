"""
Lectura flexible de columnas del Excel de nómina.

Porteado de generar_notificaciones_JPL1.py (y equivalentes de JPL2/JPL3):
el Excel real trae encabezados con variaciones menores, así que se busca
por patrones en minúscula en vez de un nombre de columna exacto.
"""

import datetime as _dt
import re
from dataclasses import dataclass, field

import pandas as pd

# El ROL de la causa viene en una columna que la nómina de la DAF trae SIN
# encabezado (pandas la lee como "Unnamed: 20"), con valores como
# "3090675-MUN". Se reconoce por su contenido y se renombra a COLUMNA_ROL_MUN
# (ver lectura.leer_nomina), que es lo primero que busca MAPEO["ROL"].
ROL_MUNICIPAL = re.compile(r"\s*-\s*MUN\s*$", re.IGNORECASE)
COLUMNA_ROL_MUN = "rol (-mun)"
# Basta con que casi todos los valores tengan esa forma: un «S/N» suelto no
# debe dejar a toda la nómina sin ROL.
PROPORCION_MINIMA_ROL_MUN = 0.95

MAPEO = {
    "NRO_PARTE": ["número de parte", "nro parte", "numero de parte"],
    "FECHA_INF": ["fecha infracción", "fecha infrac", "fecha inf"],
    "HORA_INF": ["hora infracción", "hora infrac", "hora inf", "hora"],
    "LUGAR": ["lugar de la infracción", "lugar de la", "lugar"],
    "DISPOSICION": ["disposiciones infringidas", "disposicion", "disposición"],
    "PATENTE": ["patente"],
    "DIG_PAT": ["digito patente", "digito pate", "dígito patente"],
    "ROL": [COLUMNA_ROL_MUN, "rol", "folio proceso", "folio_proceso", "nro rol"],
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


@dataclass
class RolMunicipal:
    """La columna del ROL municipal de una nómina y lo que hay que avisar."""

    columna: object
    sin_formato: int = 0                                  # valores que no terminan en -MUN
    otras_candidatas: list = field(default_factory=list)  # otras columnas que también podrían serlo


def _es_de_otro_dato(columna):
    """El encabezado ya corresponde a otro dato del mapeo (p. ej. el N° de
    parte): esa columna nunca se toma como ROL, termine en lo que termine."""
    nombre = str(columna).lower().strip()
    return any(
        patron in nombre
        for clave, patrones in MAPEO.items() if clave != "ROL"
        for patron in patrones
    )


def buscar_rol_municipal(df):
    """La columna cuyos valores no vacíos terminan (casi todos) en -MUN, o
    None. Se reconoce por su contenido, tenga o no encabezado, salvo que el
    encabezado ya sea de otro dato. Si hay varias, gana la primera y las
    demás quedan en `otras_candidatas` para avisar."""
    candidatas = []
    for col in df.columns:
        if _es_de_otro_dato(col):
            continue
        valores = df[col].astype(str).str.strip()
        valores = valores[valores != ""]
        if not len(valores):
            continue
        calzan = int(valores.str.contains(ROL_MUNICIPAL).sum())
        if calzan / len(valores) >= PROPORCION_MINIMA_ROL_MUN:
            candidatas.append(RolMunicipal(columna=col, sin_formato=len(valores) - calzan))
    if not candidatas:
        return None
    elegida = candidatas[0]
    elegida.otras_candidatas = [c.columna for c in candidatas[1:]]
    return elegida


def columna_rol_municipal(df):
    """Solo el nombre de la columna del ROL municipal, o None."""
    encontrada = buscar_rol_municipal(df)
    return encontrada.columna if encontrada else None


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
