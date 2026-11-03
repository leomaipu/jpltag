"""Excel de salida sin fórmulas accidentales: openpyxl vuelve fórmula todo texto
que empiece con «=». Los datos de los reportes (nombre, dirección, patente…) son
texto; se guardan como texto, sin perder nada y sin que Excel ejecute lo que
venga en ellos."""

import pandas as pd


def todo_como_texto(libro):
    for ws in libro.worksheets:
        for fila in ws.iter_rows():
            for c in fila:
                if c.data_type == "f" and isinstance(c.value, str):
                    c.data_type = "s"


def df_a_xlsx(df, ruta):
    with pd.ExcelWriter(ruta, engine="openpyxl") as w:
        df.to_excel(w, index=False)
        todo_como_texto(w.book)
