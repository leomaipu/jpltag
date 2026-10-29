"""
Validaciones de la configuración de un Juzgado (hallazgo H de la
auditoría del 27-oct-2026): un rango de días mal cargado o un máximo de
causas por página en 0 se aceptaban y fallaban recién al generar, o
perdían patentes en silencio.
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from nucleo.models import Juzgado


def juzgado(**campos):
    datos = {"slug": "jpl9", "nombre": "Juzgado de prueba", "dia_desde": 1, "dia_hasta": 10}
    datos.update(campos)
    return Juzgado(**datos)


class RangoDeDiasTest(TestCase):
    def test_el_ultimo_dia_no_puede_ser_anterior_al_primero(self):
        with self.assertRaises(ValidationError) as error:
            juzgado(dia_desde=20, dia_hasta=11).full_clean()
        self.assertIn("dia_hasta", error.exception.message_dict)

    def test_los_dias_van_de_1_a_31(self):
        for desde, hasta in ((0, 10), (21, 32)):
            with self.assertRaises(ValidationError):
                juzgado(dia_desde=desde, dia_hasta=hasta).full_clean()

    def test_no_se_traslapa_con_otro_juzgado_activo(self):
        Juzgado.objects.create(slug="jpl1", nombre="1er JPL", dia_desde=1, dia_hasta=10)
        with self.assertRaisesMessage(ValidationError, "se cruzan con jpl1 (1–10)"):
            juzgado(dia_desde=10, dia_hasta=20).full_clean()
        juzgado(dia_desde=11, dia_hasta=20).full_clean()

    def test_un_juzgado_inactivo_no_cuenta_para_el_traslape(self):
        Juzgado.objects.create(slug="jpl1", nombre="1er JPL", dia_desde=1, dia_hasta=10, activo=False)
        juzgado(dia_desde=1, dia_hasta=10).full_clean()

    def test_editar_un_juzgado_no_choca_consigo_mismo(self):
        propio = Juzgado.objects.create(slug="jpl1", nombre="1er JPL", dia_desde=1, dia_hasta=10)
        propio.dia_hasta = 9
        propio.full_clean()


class MaxCausasPorPaginaTest(TestCase):
    def test_cero_no_se_acepta(self):
        with self.assertRaises(ValidationError) as error:
            juzgado(max_causas_por_pagina=0).full_clean()
        self.assertIn("max_causas_por_pagina", error.exception.message_dict)
