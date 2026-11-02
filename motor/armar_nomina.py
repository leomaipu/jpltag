"""
«Arma tu nómina»: cruza los dos reportes que entrega SMC y deja la nómina
que usa el motor para generar las cartas.

  · LISTADO DE INGRESOS DIARIOS  → causa, N° de parte, descripción, fecha de
    citación, estado, PLACA, fecha de infracción, tipo de vehículo.
  · LISTADO DE CAUSAS POR ESTADO → nombre, RUT, dirección, comuna, lugar del
    hecho, estado.

La llave del cruce es el N° de parte («1794374-MVN»). Todo lo que el cruce no
puede dejar en la nómina queda listado, con su motivo, en el informe: nada
se descarta en silencio y ningún dato se inventa. Lo que los reportes no
traen (hora de la infracción, marca, modelo, «Televía inhabilitado») sale
vacío.

Las columnas de salida replican el orden y los encabezados de la nómina del
3er JPL (NOMINAJPL3.xlsx), que el motor ya lee sin adaptadores.
"""

import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

COLUMNAS_NOMINA = [
    "ROL", "NRO PARTE", "OFICINA REMISORA", "DESCRIPCIÓN CAUSA", "disposicion",
    "lugar", "FECHA PROCESO", "FECHA CITACIÓN", "ESTADO CAUSA", "FECHA TRÁMITE",
    "TRÁMITE", "rut_infractor", "nombre_infractor", "FECHA INFRACCIÓN",
    "HORA INFRACCIÓN", "patente", "vehiculo", "MARCA DEL VEHÍCULO",
    "MODELO DEL VEHÍCULO", "domicilio_infractor", "comuna_infractor",
]

# Encabezados que cada reporte debe traer (se comparan sin mayúsculas ni
# espacios de más). Si falta alguno, no se arma nada.
REQUERIDAS_INGRESOS = [
    "CAUSA", "NRO PARTE", "DESCRIPCIÓN CAUSA", "FECHA CITACIÓN", "ESTADO CAUSA",
    "RUT", "FECHA INFRACCIÓN", "PLACA", "TIPO VEHÍCULO",
]
REQUERIDAS_CAUSAS = [
    "causarol", "parte", "nombre", "rut", "direccion", "comuna",
    "lugarhechogen", "estado",
]

ESTADO_NOTIFICABLE = "PENDIENTE"


class ReporteInvalido(ValueError):
    """Un reporte no trae las columnas que el cruce necesita."""


def limpiar_texto(valor):
    """Quita los saltos de línea que SMC deja pegados a algunas celdas
    («_x000D_» al leer el .xlsx) y los espacios de más. Vacío → ""."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return ""
    if isinstance(valor, (datetime, date)):
        return valor
    texto = re.sub(r"_x000D_|[\r\n]+", " ", str(valor))
    return re.sub(r"\s+", " ", texto).strip()


def _norm(encabezado):
    return " ".join(str(encabezado).lower().split())


def _leer(ruta, requeridas, nombre):
    df = pd.read_excel(ruta, dtype=object)
    df = df.loc[:, [c for c in df.columns if not str(c).startswith("Unnamed")]]
    df.columns = [" ".join(str(c).split()) for c in df.columns]
    por_norm = {_norm(c): c for c in df.columns}
    faltan = [r for r in requeridas if _norm(r) not in por_norm]
    if faltan:
        raise ReporteInvalido(
            f"Al reporte «{nombre}» le faltan las columnas: {', '.join(faltan)}. "
            f"Trae: {', '.join(str(c).strip() for c in df.columns)}."
        )
    df = df.rename(columns={por_norm[_norm(r)]: r for r in requeridas})
    return df.map(limpiar_texto)


def _col(df, nombre):
    """Columna opcional del reporte: si no viene, serie vacía."""
    return df[nombre] if nombre in df.columns else pd.Series([""] * len(df), index=df.index)


def _fecha(valor):
    """datetime/«dd/mm/aaaa»/«aaaa-mm-dd»/«m/d/aaaa hh:mm:ss AM» → date o None."""
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    texto = str(valor or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(texto, fmt).date()
        except ValueError:
            pass
    return None


def _correlativo(rol):
    """«2026-Y-127795» → 127795 (None si no termina en número)."""
    m = re.search(r"(\d+)\s*$", str(rol))
    return int(m.group(1)) if m else None


def _sin_id(descripcion):
    """«Infraccion al Art. 114 ... - ID:16381469» → sin el « - ID:…» final."""
    return re.sub(r"\s*-\s*ID:\s*\d+\s*$", "", str(descripcion)).strip()


@dataclass
class Informe:
    resumen: dict = field(default_factory=dict)
    excluidas: pd.DataFrame = None     # CAUSA, NRO PARTE, motivo
    alertas: list = field(default_factory=list)   # (severidad, texto)
    revisar: dict = field(default_factory=dict)   # {nombre de hoja: DataFrame}


def armar_nomina(ruta_ingresos, ruta_causas, citaciones_por_bloque=None):
    """Devuelve (nomina: DataFrame, informe: Informe).

    `citaciones_por_bloque`: [(rol_desde, rol_hasta, date)], con el correlativo
    final del ROL («2026-Y-127795» → 127795). Es la fecha que fija el Juzgado
    por bloque de folios; reemplaza la «FECHA CITACIÓN» del reporte en esos
    ROL. Los ROL fuera de todo bloque conservan la fecha del reporte."""
    ing = _leer(ruta_ingresos, REQUERIDAS_INGRESOS, "Listado de ingresos diarios")
    cau = _leer(ruta_causas, REQUERIDAS_CAUSAS, "Listado de causas por estado")
    informe = Informe()
    excl = []   # (CAUSA, NRO PARTE, motivo)

    def excluir(filas, motivo):
        for _, f in filas.iterrows():
            excl.append((f["CAUSA"], f["NRO PARTE"], motivo))

    n_ing, n_cau = len(ing), len(cau)

    # 1. Partes repetidos: como en el motor, se excluyen todas las copias.
    rep_ing = ing["NRO PARTE"].duplicated(keep=False) & (ing["NRO PARTE"] != "")
    excluir(ing[rep_ing], "N° de parte repetido en Ingresos diarios")
    ing = ing[~rep_ing]
    rep_cau = cau["parte"].duplicated(keep=False) & (cau["parte"] != "")
    partes_rep_cau = set(cau.loc[rep_cau, "parte"])
    cau = cau[~rep_cau]

    # 2. Solo se notifica lo PENDIENTE.
    no_pend = ing["ESTADO CAUSA"].str.upper() != ESTADO_NOTIFICABLE
    for estado, filas in ing[no_pend].groupby("ESTADO CAUSA"):
        excluir(filas, f"Estado en Ingresos diarios: {estado or '(vacío)'}")
    ing = ing[~no_pend]

    # 3. Cruce por N° de parte.
    m = ing.merge(cau, left_on="NRO PARTE", right_on="parte", how="left",
                  suffixes=("", "_c"), indicator=True)
    sin_cruce = m["_merge"] == "left_only"
    for _, f in m[sin_cruce].iterrows():
        motivo = ("N° de parte repetido en Causas por estado" if f["NRO PARTE"] in partes_rep_cau
                  else "No está en Causas por estado: sin nombre ni domicilio")
        excl.append((f["CAUSA"], f["NRO PARTE"], motivo))
    m = m[~sin_cruce].copy()

    # 4. Contradicciones entre los dos reportes.
    malo_rol = m["CAUSA"] != m["causarol"]
    malo_rut = m["RUT"].str.upper() != m["rut"].str.upper()
    malo_estado = m["estado"].str.upper() != ESTADO_NOTIFICABLE
    for mask, motivo in (
        (malo_rol, "La causa (ROL) no coincide entre los dos reportes"),
        (malo_rut & ~malo_rol, "El RUT no coincide entre los dos reportes"),
        (malo_estado & ~malo_rol & ~malo_rut, "Causas por estado la tiene en otro estado"),
    ):
        for _, f in m[mask].iterrows():
            excl.append((f["CAUSA"], f["NRO PARTE"], f"{motivo} ({f['estado']})" if "estado" in motivo else motivo))
    m = m[~(malo_rol | malo_rut | malo_estado)].copy()

    # 5. Domicilio: si falta en una causa y el mismo RUT lo trae en otra, se
    #    completa con ese (misma persona). Si no, queda vacío y se avisa.
    m["_rutk"] = m["rut"].str.upper()
    por_rut = (m[m["direccion"] != ""].groupby("_rutk")[["direccion", "comuna"]].first())
    completadas = 0
    for i in m.index[m["direccion"] == ""]:
        k = m.at[i, "_rutk"]
        if k in por_rut.index:
            m.at[i, "direccion"] = por_rut.at[k, "direccion"]
            if m.at[i, "comuna"] == "":
                m.at[i, "comuna"] = por_rut.at[k, "comuna"]
            completadas += 1
    m = m.reset_index(drop=True)

    # 6. Fecha de citación fijada por el Juzgado, por bloque de folios.
    citacion = m["FECHA CITACIÓN"].map(_fecha)
    fuera_de_bloque = 0
    if citaciones_por_bloque:
        nueva = []
        for rol, original in zip(m["CAUSA"], citacion):
            n = _correlativo(rol)
            hallada = next((f for d, h, f in citaciones_por_bloque if n is not None and d <= n <= h), None)
            if hallada is None:
                fuera_de_bloque += 1
            nueva.append(hallada or original)
        citacion = pd.Series(nueva, index=m.index)

    nomina = pd.DataFrame({
        "ROL": m["CAUSA"],
        "NRO PARTE": m["NRO PARTE"],
        "OFICINA REMISORA": _col(m, "OFICINA REMISORA"),
        "DESCRIPCIÓN CAUSA": m["DESCRIPCIÓN CAUSA"],
        "disposicion": m["DESCRIPCIÓN CAUSA"].map(_sin_id),
        "lugar": m["lugarhechogen"],
        "FECHA PROCESO": _col(m, "FECHA PROCESO").map(_fecha),
        "FECHA CITACIÓN": citacion,
        "ESTADO CAUSA": m["ESTADO CAUSA"],
        "FECHA TRÁMITE": _col(m, "FECHA TRÁMITE").map(_fecha),
        "TRÁMITE": _col(m, "TRÁMITE"),
        "rut_infractor": m["rut"],
        "nombre_infractor": m["nombre"],
        "FECHA INFRACCIÓN": m["FECHA INFRACCIÓN"].map(_fecha),
        "HORA INFRACCIÓN": "",
        "patente": m["PLACA"].str.upper(),
        "vehiculo": m["TIPO VEHÍCULO"],
        "MARCA DEL VEHÍCULO": "",
        "MODELO DEL VEHÍCULO": "",
        "domicilio_infractor": m["direccion"],
        "comuna_infractor": m["comuna"],
    })[COLUMNAS_NOMINA]
    nomina["ID MOP"] = m["DESCRIPCIÓN CAUSA"].str.extract(r"ID:\s*(\d+)\s*$")[0].fillna("")

    # ── Informe ──────────────────────────────────────────────────────────
    informe.excluidas = pd.DataFrame(excl, columns=["ROL (causa)", "N° de parte", "Motivo"])
    hoy = date.today()
    pas = int(sum(1 for d in nomina["FECHA CITACIÓN"] if isinstance(d, date) and d < hoy))
    sin_cit = int(sum(1 for d in nomina["FECHA CITACIÓN"] if not isinstance(d, date)))
    patentes = nomina["patente"]
    g = nomina.assign(_pat=patentes).groupby("_pat")
    pat_multi_rut = g["rut_infractor"].nunique()
    pat_multi_dom = g["domicilio_infractor"].nunique()
    sin_dom = nomina[nomina["domicilio_infractor"] == ""]
    sin_com = nomina[nomina["comuna_infractor"] == ""]
    fi = nomina["FECHA INFRACCIÓN"]
    meses = pd.Series([f"{d.year}-{d.month:02d}" for d in fi if isinstance(d, date)]).value_counts()
    dias = pd.Series([("1–10" if d.day <= 10 else "11–20" if d.day <= 20 else "21–31")
                      for d in fi if isinstance(d, date)]).value_counts()

    # Causas del listado por estado que no llegan a la nómina: sin PLACA ni
    # fecha de infracción (esos datos solo están en Ingresos diarios).
    usadas = set(nomina["NRO PARTE"])
    todas_ing = set(ing["NRO PARTE"]) | {f[1] for f in excl}
    cau_ant = cau[~cau["parte"].isin(todas_ing)]
    fallada = cau_ant[cau_ant["estado"].str.upper() != ESTADO_NOTIFICABLE]
    pend_sin_ing = cau_ant[cau_ant["estado"].str.upper() == ESTADO_NOTIFICABLE]
    if len(pend_sin_ing):
        informe.alertas.append(("Media", f"{len(pend_sin_ing)} causas PENDIENTES del listado por estado no están en Ingresos diarios: "
                                "sin patente no se pueden notificar (hoja «Pendientes sin ingreso»)."))
        informe.revisar["Pendientes sin ingreso"] = pend_sin_ing[["causarol", "parte", "nombre", "rut", "estado"]]
    if len(fallada):
        informe.alertas.append(("Info", f"{len(fallada)} causas del listado por estado ya están en otro estado "
                                f"({', '.join(sorted(fallada['estado'].unique()))}) y no entran a la nómina."))

    informe.resumen = {
        "Filas en Ingresos diarios": n_ing,
        "Filas en Causas por estado": n_cau,
        "Filas en la nómina": len(nomina),
        "Patentes distintas": int(patentes.nunique()),
        "Filas excluidas (ver hoja)": len(informe.excluidas),
        "Domicilios completados desde otra causa del mismo RUT": completadas,
        "Causas del listado por estado que no entran (otro estado o sin ingreso)": len(fallada) + len(pend_sin_ing),
        "Fecha de citación reemplazada por bloque de folios": (len(nomina) - fuera_de_bloque) if citaciones_por_bloque else 0,
    }
    informe.alertas = []
    if pas:
        informe.alertas.append(("Alta", f"{pas} filas con FECHA CITACIÓN anterior a hoy "
                                f"({', '.join(sorted({d.strftime('%d/%m/%Y') for d in nomina['FECHA CITACIÓN'] if isinstance(d, date) and d < hoy}))}): "
                                "la carta diría que la audiencia ya pasó. Entregar la fecha fijada por el Juzgado."))
    if sin_cit:
        informe.alertas.append(("Alta", f"{sin_cit} filas sin FECHA CITACIÓN legible: la carta sale «a coordinar»."))
    if fuera_de_bloque:
        informe.alertas.append(("Media", f"{fuera_de_bloque} ROL no caen en ningún bloque de folios: conservan la fecha del reporte."))
    if len(sin_dom):
        informe.alertas.append(("Alta", f"{len(sin_dom)} filas sin domicilio ({sin_dom['patente'].nunique()} patentes): la carta no se puede despachar."))
        informe.revisar["Sin domicilio"] = sin_dom[["ROL", "NRO PARTE", "patente", "rut_infractor", "nombre_infractor", "comuna_infractor"]]
    if len(sin_com):
        informe.alertas.append(("Media", f"{len(sin_com)} filas sin comuna."))
    multi = pat_multi_rut[pat_multi_rut > 1]
    if len(multi):
        informe.alertas.append(("Media", f"{len(multi)} patentes con más de un RUT: la carta usa los datos de la primera fila de la patente."))
        informe.revisar["Patente con varios RUT"] = nomina[nomina["patente"].isin(multi.index)][
            ["patente", "ROL", "NRO PARTE", "rut_infractor", "nombre_infractor", "domicilio_infractor"]].sort_values(["patente", "ROL"])
    multi_d = pat_multi_dom[(pat_multi_dom > 1) & ~pat_multi_dom.index.isin(multi.index)]
    if len(multi_d):
        informe.alertas.append(("Baja", f"{len(multi_d)} patentes con un solo RUT pero más de un domicilio: la carta usa el de la primera fila."))
    if len(meses) > 1:
        informe.alertas.append(("Alta", "Infracciones de más de un mes (" + ", ".join(f"{k}: {v}" for k, v in meses.items()) +
                                "): el motor solo procesa el mes predominante; el resto sale como «otro mes»."))
    if len(dias) == 1:
        informe.alertas.append(("Alta", f"Todas las fechas de infracción caen en los días {dias.index[0]}: con la regla por día del motor, "
                                "todas las patentes irían a un solo juzgado."))
    for col in ("HORA INFRACCIÓN", "MARCA DEL VEHÍCULO", "MODELO DEL VEHÍCULO"):
        informe.alertas.append(("Info", f"{col}: los reportes no la traen; sale vacía."))
    informe.revisar["Distribución"] = pd.concat([
        pd.DataFrame({"Mes de la infracción": meses.index, "Filas": meses.values}),
        pd.DataFrame({"Días del mes": dias.index, "Filas ": dias.values}),
    ], axis=1)
    return nomina, informe


def escribir_nomina(nomina, ruta):
    """Nómina en .xlsx lista para el motor: una sola hoja, fechas como fechas."""
    with pd.ExcelWriter(ruta, engine="openpyxl", datetime_format="DD/MM/YYYY", date_format="DD/MM/YYYY") as w:
        nomina.to_excel(w, index=False, sheet_name="Nomina")
        ws = w.sheets["Nomina"]
        ws.freeze_panes = "A2"
        for col in ws.columns:
            ancho = max(len(str(c.value)) if c.value is not None else 0 for c in list(col)[:200])
            ws.column_dimensions[col[0].column_letter].width = min(max(ancho + 2, 12), 60)


def escribir_informe(informe, ruta):
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        pd.DataFrame(list(informe.resumen.items()), columns=["Concepto", "Valor"]).to_excel(w, index=False, sheet_name="Resumen")
        pd.DataFrame(informe.alertas, columns=["Severidad", "Alerta"]).to_excel(w, index=False, sheet_name="Alertas")
        informe.excluidas.to_excel(w, index=False, sheet_name="Excluidas")
        for nombre, df in informe.revisar.items():
            df.to_excel(w, index=False, sheet_name=nombre[:31])
        for ws in w.book.worksheets:
            ws.freeze_panes = "A2"
            for col in ws.columns:
                ancho = max(len(str(c.value)) if c.value is not None else 0 for c in list(col)[:200])
                ws.column_dimensions[col[0].column_letter].width = min(max(ancho + 2, 10), 90)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        sys.exit("Uso: python -m motor.armar_nomina INGRESOS.xlsx CAUSAS_POR_ESTADO.xlsx CARPETA_SALIDA")
    from pathlib import Path
    sal = Path(sys.argv[3]); sal.mkdir(parents=True, exist_ok=True)
    nom, inf = armar_nomina(sys.argv[1], sys.argv[2])
    escribir_nomina(nom, sal / "Nomina_consolidada.xlsx")
    escribir_informe(inf, sal / "Informe_consolidacion.xlsx")
    for k, v in inf.resumen.items():
        print(f"{k}: {v}")
    for s, t in inf.alertas:
        print(f"[{s}] {t}")
