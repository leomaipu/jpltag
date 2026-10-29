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


# Los textos más largos de la nómina real de feb-2026 (en caracteres).
NOMBRE_MAS_LARGO = 71
DOMICILIO_MAS_LARGO = 83
LUGAR_MAS_LARGO = 121


def _texto(base, largo):
    return (base * (largo // len(base) + 1))[:largo]


def _pagina_llena(n, *, de_lo_mas_largo=False):
    """Una patente con `n` infracciones de textos largos (2 líneas en la
    tabla). `de_lo_mas_largo`: el peor caso realista de la nómina real —
    nombre y domicilio del largo máximo, una infracción con el lugar más
    largo (3 líneas) y la página «parte 2 de 3», que suma su aviso.

    El extremo sintético (las 12 filas con el lugar más largo de toda la
    nómina) no cabe en el 2do JPL ni con las plantillas anteriores al bloque
    de firma apilado: si llegara a pasar, ese juzgado baja
    max_causas_por_pagina desde /admin/."""
    lugar_normal = "AUTOPISTA CENTRAL, PORTICO PA-12 SENTIDO NORTE SUR KM 14,5"
    lugar_largo = _texto("AUTOPISTA CENTRAL PORTICO PA-12 ", LUGAR_MAS_LARGO)
    causas = [
        {
            "nroParte": f"12558{i:02d}-M78", "rol_raw": f"30906{i:02d}-MUN",
            "fecha": "03/09/2026", "hora": "08:30",
            "lugar": lugar_largo if de_lo_mas_largo and i == 0 else lugar_normal,
            "disposicion": "ARTICULO 114 LEY 18.290 TRANSITAR SIN DISPOSITIVO TAG HABILITADO",
            "monto": 1,
        }
        for i in range(n)
    ]
    info = {
        "digPat": "1", "rol_raw": "", "reg_raw": "",
        "nombre": _texto("MARIA JOSEFINA PEREZ ", NOMBRE_MAS_LARGO) if de_lo_mas_largo
        else "MARIA JOSEFINA PEREZ DE LA FUENTE SOTOMAYOR",
        "rut": "11111111-1",
        "domicilio": _texto("PASAJE LOS AROMOS 1234 ", DOMICILIO_MAS_LARGO) if de_lo_mas_largo
        else "PASAJE LOS AROMOS 1234, VILLA LAS ARAUCARIAS DEPTO 1203",
        "comuna": "MAIPU", "vehiculo": "CHEVROLET, SPARK", "causas": causas,
    }
    parte, total = (2, 3) if de_lo_mas_largo else (1, 1)
    return {"patente": "AAAA11", "info": info, "chunk": causas, "parte": parte, "total_partes": total,
            "total_causas": n * total}


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
        self._cabe(_pagina_llena(MAX_CAUSAS_POR_PAGINA_DEFECTO), "normal")

    def test_cabe_tambien_con_los_textos_mas_largos_en_una_pagina_de_continuacion(self):
        self._cabe(_pagina_llena(MAX_CAUSAS_POR_PAGINA_DEFECTO, de_lo_mas_largo=True), "largo")

    def test_la_prueba_detecta_un_desborde(self):
        """Control: con más infracciones de las que caben, sí se pasa a 2 hojas."""
        pagina = _pagina_llena(MAX_CAUSAS_POR_PAGINA_DEFECTO + 4)
        config = self._config(2, "CITACION_SENTENCIA", self.imagenes["firma horizontal + timbre"])
        self.assertGreater(self._paginas(pagina, config, "control"), 1)

    def _config(self, n, tipo, imagenes):
        return {
            "tipo_carta": tipo, "slug_juzgado": f"jpl{n}", "nombre_juzgado": f"JPL {n}",
            "utm_valor": 72151, "max_causas_por_pagina": MAX_CAUSAS_POR_PAGINA_DEFECTO,
            "nombre_firmante": "CAROLINA ANDREA MUÑOZ RIQUELME", "cargo_firma": "SECRETARIA ABOGADA",
            "fecha_audiencia": "29/10/2026", **imagenes,
        }

    def _paginas(self, pagina, config, etiqueta):
        contexto = construir_contexto(pagina, ("A COORDINAR CON EL JUZGADO", ""), 1, config)
        doc = renderizar_carta(
            ruta_plantilla_defecto(config["slug_juzgado"], "", config["tipo_carta"]), contexto, config,
        )
        ruta = os.path.join(
            self.carpeta, f"{config['slug_juzgado']}_{config['tipo_carta']}_{len(config)}_{etiqueta}.docx",
        )
        doc.save(ruta)
        return _paginas_pdf(ruta, self.carpeta)

    def _cabe(self, pagina, etiqueta):
        for n in (1, 2, 3):
            for tipo in TIPOS_CARTA:
                for caso, imagenes in self.imagenes.items():
                    with self.subTest(juzgado=n, tipo=tipo, imagenes=caso):
                        config = self._config(n, tipo, imagenes)
                        self.assertEqual(self._paginas(pagina, config, f"{etiqueta}_{len(caso)}"), 1)


if __name__ == "__main__":
    unittest.main()
