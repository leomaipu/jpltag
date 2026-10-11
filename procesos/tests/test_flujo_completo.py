"""
El flujo real de punta a punta: subir una nómina → se generan las cartas
→ se ve el resultado → se descarga un archivo — con una nómina sintética
de 2 infracciones de LA MISMA patente, para confirmar lo que de verdad
pedía el proyecto: que ambas queden agrupadas en 1 sola carta, no en 2
cartas separadas (ver la descripción del proyecto en README.md).

SALIDAS_ROOT se redirige a una carpeta temporal: si no, estas pruebas
escribirían cartas/Excel reales dentro de salidas/ del repositorio.
"""

import shutil
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from procesos.models import ArchivoGenerado, GeneracionCartas

from .fixtures import construir_nomina_xlsx, crear_juzgado, crear_usuario, fila_nomina, ingresar

_TMP_SALIDAS = tempfile.mkdtemp(prefix="jpltag-pruebas-salidas-")


@override_settings(SALIDAS_ROOT=_TMP_SALIDAS)
class FlujoCompletoTest(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.juzgado = crear_juzgado()
        self.usuario = crear_usuario("operador_flujo", juzgado=self.juzgado)
        ingresar(self.client, "operador_flujo")

    def _subir_nomina_de_dos_infracciones_misma_patente(self):
        nomina = construir_nomina_xlsx([
            fila_nomina(pat="AAAA11", nro_parte="2001", fecha="03/09/2026",
                        nombre="Juan", ap1="Pérez", ap2="Soto"),
            fila_nomina(pat="AAAA11", nro_parte="2002", fecha="05/09/2026",
                        nombre="Juan", ap1="Pérez", ap2="Soto"),
        ])
        return self.client.post("/subir/", {
            "nomina": nomina,
            "tipo_carta": "CITACION",
            "nombre_firmante": "Secretaria de Prueba",
            "cargo_firma": "SECRETARIA",
            "n_cartas_paquete": 0,
        }, follow=True)

    def test_sube_genera_y_termina_en_la_pagina_de_resultado(self):
        resp = self._subir_nomina_de_dos_infracciones_misma_patente()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.redirect_chain[-1][0].rstrip("/").split("/")[-2], "resultado")

        generacion = GeneracionCartas.objects.get(juzgado=self.juzgado)
        self.assertEqual(generacion.estado, GeneracionCartas.Estado.OK)
        # El punto central del proyecto: 2 infracciones de LA MISMA
        # patente -> 1 sola patente agrupada, no 2.
        self.assertEqual(generacion.total_patentes, 1)

    def test_el_resultado_muestra_un_archivo_de_cartas_para_descargar(self):
        self._subir_nomina_de_dos_infracciones_misma_patente()
        generacion = GeneracionCartas.objects.get(juzgado=self.juzgado)
        archivo_cartas = generacion.archivos.get(tipo=ArchivoGenerado.Tipo.CARTAS)
        self.assertTrue(Path(generacion.carpeta_salida))
        self.assertIsNotNone(archivo_cartas)

    def test_descarga_el_docx_y_trae_la_carta_agrupada(self):
        self._subir_nomina_de_dos_infracciones_misma_patente()
        generacion = GeneracionCartas.objects.get(juzgado=self.juzgado)
        archivo_cartas = generacion.archivos.get(tipo=ArchivoGenerado.Tipo.CARTAS)

        resp = self.client.get(f"/descargar/{archivo_cartas.pk}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Disposition"].count("attachment"), 1)

        contenido = b"".join(resp.streaming_content)
        self.assertGreater(len(contenido), 0, "el .docx descargado no debería venir vacío")

    def test_nomina_que_no_es_excel_no_pasa_de_la_validacion_del_formulario(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        archivo_malo = SimpleUploadedFile("nomina.txt", b"esto no es un excel", content_type="text/plain")
        resp = self.client.post("/subir/", {"nomina": archivo_malo}, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(GeneracionCartas.objects.filter(juzgado=self.juzgado).count(), 0)
