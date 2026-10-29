"""
Que una carta con el máximo de infracciones por página
(MAX_CAUSAS_POR_PAGINA_DEFECTO) quepa en UNA hoja con las plantillas por
defecto, con timbre y firma apilados y con imágenes de las proporciones que
usan los juzgados: la firma horizontal + timbre del 2do JPL y la imagen
vertical con firma y timbre juntos del 3er JPL.

Necesita LibreOffice para paginar el .docx (`soffice`): sin él la prueba se
salta. La imagen de desarrollo con LibreOffice se arma con
`FROM jpltag:base` + `apt-get install libreoffice-writer-nogui poppler-utils`.
Las imágenes son sintéticas, del mismo tamaño en píxeles que las reales.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from PIL import Image, ImageDraw

from motor.plantilla import (
    MAX_CAUSAS_POR_PAGINA_DEFECTO,
    TIPOS_CARTA,
    construir_contexto,
    renderizar_carta,
    ruta_plantilla_defecto,
)

SOFFICE = shutil.which("soffice")

# (ancho, alto) en píxeles de las imágenes que hoy usan los juzgados
FIRMA_HORIZONTAL = (327, 170)
TIMBRE = (84, 96)
FIRMA_Y_TIMBRE_VERTICAL = (120, 146)


def _imagen(carpeta, nombre, tamano):
    ruta = os.path.join(carpeta, nombre)
    imagen = Image.new("RGB", tamano, "white")
    ImageDraw.Draw(imagen).ellipse((2, 2, tamano[0] - 3, tamano[1] - 3), outline="navy", width=3)
    imagen.save(ruta, dpi=(96, 96))
    return ruta


def _pagina_llena(n):
    """Una patente con `n` infracciones de textos largos (2 líneas en la tabla)."""
    causas = [
        {
            "nroParte": f"12558{i:02d}-M78", "rol_raw": f"30906{i:02d}-MUN",
            "fecha": "03/09/2026", "hora": "08:30",
            "lugar": "AUTOPISTA CENTRAL, PORTICO PA-12 SENTIDO NORTE SUR KM 14,5",
            "disposicion": "ARTICULO 114 LEY 18.290 TRANSITAR SIN DISPOSITIVO TAG HABILITADO",
            "monto": 1,
        }
        for i in range(n)
    ]
    info = {
        "digPat": "1", "rol_raw": "", "reg_raw": "", "nombre": "MARIA JOSEFINA PEREZ DE LA FUENTE SOTOMAYOR",
        "rut": "11111111-1", "domicilio": "PASAJE LOS AROMOS 1234, VILLA LAS ARAUCARIAS DEPTO 1203",
        "comuna": "MAIPU", "vehiculo": "CHEVROLET, SPARK", "causas": causas,
    }
    return {"patente": "AAAA11", "info": info, "chunk": causas, "parte": 1, "total_partes": 1, "total_causas": n}


def _paginas_pdf(docx, carpeta):
    subprocess.run(
        [SOFFICE, "--headless", f"-env:UserInstallation=file://{carpeta}/perfil",
         "--convert-to", "pdf", "--outdir", carpeta, docx],
        check=True, capture_output=True, timeout=120,
    )
    pdf = os.path.splitext(docx)[0] + ".pdf"
    salida = subprocess.run(["pdfinfo", pdf], check=True, capture_output=True, text=True).stdout
    return int(next(linea.split()[-1] for linea in salida.splitlines() if linea.startswith("Pages:")))


@unittest.skipUnless(SOFFICE and shutil.which("pdfinfo"), "sin LibreOffice/pdfinfo para paginar")
class CartaLlenaEnUnaHojaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.carpeta = tempfile.mkdtemp(prefix="jpltag-paginas-")
        cls.imagenes = {
            "firma horizontal + timbre": {
                "firma_path": _imagen(cls.carpeta, "firma.png", FIRMA_HORIZONTAL),
                "sello_path": _imagen(cls.carpeta, "timbre.png", TIMBRE),
            },
            "firma y timbre en una imagen vertical": {
                "firma_path": _imagen(cls.carpeta, "firma_timbre.jpg", FIRMA_Y_TIMBRE_VERTICAL),
            },
            "sin imágenes": {},
        }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.carpeta, ignore_errors=True)

    def test_el_maximo_de_infracciones_cabe_en_una_hoja(self):
        pagina = _pagina_llena(MAX_CAUSAS_POR_PAGINA_DEFECTO)
        for n in (1, 2, 3):
            for tipo in TIPOS_CARTA:
                for caso, imagenes in self.imagenes.items():
                    with self.subTest(juzgado=n, tipo=tipo, imagenes=caso):
                        config = {
                            "tipo_carta": tipo, "slug_juzgado": f"jpl{n}", "nombre_juzgado": f"JPL {n}",
                            "utm_valor": 72151, "max_causas_por_pagina": MAX_CAUSAS_POR_PAGINA_DEFECTO,
                            "nombre_firmante": "CAROLINA ANDREA MUÑOZ RIQUELME", "cargo_firma": "SECRETARIA ABOGADA",
                            "fecha_audiencia": "29/10/2026", **imagenes,
                        }
                        contexto = construir_contexto(pagina, ("A COORDINAR CON EL JUZGADO", ""), 1, config)
                        doc = renderizar_carta(ruta_plantilla_defecto(f"jpl{n}", "", tipo), contexto, config)
                        ruta = os.path.join(self.carpeta, f"jpl{n}_{tipo}_{len(caso)}.docx")
                        doc.save(ruta)
                        self.assertEqual(_paginas_pdf(ruta, self.carpeta), 1)


if __name__ == "__main__":
    unittest.main()
