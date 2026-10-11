"""
El control de acceso que más importa de toda la app: un usuario de un
Juzgado NUNCA debe poder ver ni descargar los resultados de otro — ahí
están el RUT, el domicilio y el nombre del infractor (ver
nucleo.models.PerfilUsuario.tiene_acceso_a, y procesos/views.py
ResultadoView/DescargarArchivoView). Hasta ahora esto solo estaba
verificado a mano.
"""

from django.test import TestCase

from nucleo.models import Rol
from procesos.models import ArchivoGenerado, GeneracionCartas

from .fixtures import crear_juzgado, crear_usuario, ingresar


class AislamientoEntreJuzgadosTest(TestCase):
    def setUp(self):
        self.jpl1 = crear_juzgado(slug="jpl1", nombre="1er JPL de Maipú")
        self.jpl2 = crear_juzgado(slug="jpl2", nombre="2do JPL de Maipú")

        usuario_jpl2 = crear_usuario("secretaria_jpl2", rol=Rol.SECRETARIO, juzgado=self.jpl2)
        self.generacion_jpl2 = GeneracionCartas.objects.create(
            juzgado=self.jpl2, usuario=usuario_jpl2, nombre_archivo_nomina="nomina_jpl2.xlsx",
            carpeta_salida="jpl2/alguna-carpeta", total_patentes=1,
        )
        self.archivo_jpl2 = ArchivoGenerado.objects.create(
            generacion=self.generacion_jpl2, tipo=ArchivoGenerado.Tipo.CARTAS,
            ruta="jpl2/alguna-carpeta/cartas/lote_1.docx", nombre_descarga="lote_1.docx",
        )

        crear_usuario("secretario_jpl1", rol=Rol.SECRETARIO, juzgado=self.jpl1)
        ingresar(self.client, "secretario_jpl1")

    def test_no_puede_ver_el_resultado_de_otro_juzgado(self):
        resp = self.client.get(f"/resultado/{self.generacion_jpl2.pk}/")
        self.assertEqual(resp.status_code, 403)

    def test_no_puede_descargar_un_archivo_de_otro_juzgado(self):
        """El corazón de esta prueba: los datos personales (RUT, domicilio,
        nombre) de JPL2 no deben llegar nunca a una respuesta HTTP para
        alguien de JPL1 — ni siquiera un 404 que insinúe que el archivo
        existe; debe ser un 403 explícito de permisos."""
        resp = self.client.get(f"/descargar/{self.archivo_jpl2.pk}/")
        self.assertEqual(resp.status_code, 403)
        # Nada de contenido del archivo debe venir en la respuesta de error.
        self.assertNotIn(b"lote_1.docx", resp.content)

    def test_si_puede_ver_y_descargar_lo_de_su_propio_juzgado(self):
        """Control positivo: que el 403 de arriba sea por el juzgado
        equivocado, no porque la vista esté rota para todos. Se prueba
        contra un registro del propio jpl1, creado con ese mismo usuario."""
        from django.contrib.auth import get_user_model

        usuario_jpl1 = get_user_model().objects.get(username="secretario_jpl1")
        generacion_jpl1 = GeneracionCartas.objects.create(
            juzgado=self.jpl1, usuario=usuario_jpl1, nombre_archivo_nomina="nomina_jpl1.xlsx",
            carpeta_salida="jpl1/otra-carpeta", total_patentes=1,
        )
        archivo_jpl1 = ArchivoGenerado.objects.create(
            generacion=generacion_jpl1, tipo=ArchivoGenerado.Tipo.SEGUIMIENTO,
            ruta="jpl1/otra-carpeta/reporte_seguimiento.xlsx", nombre_descarga="reporte_seguimiento.xlsx",
        )
        self.assertEqual(self.client.get(f"/resultado/{generacion_jpl1.pk}/").status_code, 200)
        # Sin el archivo físico en SALIDAS_ROOT esto da 404 (no 403): el
        # permiso ya se otorgó, solo falta el archivo — también es una
        # señal correcta, distinta del caso de arriba.
        self.assertEqual(self.client.get(f"/descargar/{archivo_jpl1.pk}/").status_code, 404)

    def test_administrador_sin_juzgado_fijo_si_puede_ver_cualquier_juzgado(self):
        """Caso especial de nucleo.models.PerfilUsuario.tiene_acceso_a:
        Administrador con juzgado=None ve los 3 — no es un bug que
        'cuele', es la regla para ese rol."""
        crear_usuario("admin_global", rol=Rol.ADMINISTRADOR, juzgado=None)
        client_admin = self.client_class()
        ingresar(client_admin, "admin_global")
        resp = client_admin.get(f"/resultado/{self.generacion_jpl2.pk}/")
        self.assertEqual(resp.status_code, 200)
