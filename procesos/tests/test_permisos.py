"""
Permisos por rol (nucleo.models.PERMISOS_POR_ROL, confirmada con el
usuario el 10-oct-2026): quién puede entrar a /subir/, /agenda/, y qué
pasa con una cuenta sin PerfilUsuario todavía.

El control de acceso POR JUZGADO (que un Secretario de JPL1 no vea los
resultados de JPL2) se prueba aparte, en test_aislamiento_juzgados.py —
depende del objeto pedido, no solo del rol.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase

from nucleo.models import Rol

from .fixtures import crear_juzgado, crear_usuario, ingresar


FECHA_FUTURA = date.today() + timedelta(days=30)  # relativa: la agenda no admite fechas pasadas


class SinPerfilTest(TestCase):
    """Un superusuario recién creado por `createsuperuser` (ver
    DESPLIEGUE.md, paso 3) todavía no tiene PerfilUsuario: no debe poder
    usar nada de `procesos` hasta que un Administrador le asigne uno."""

    def setUp(self):
        get_user_model().objects.create_user("sin_perfil", password="x" * 12)
        self.client.post("/ingresar/", {"username": "sin_perfil", "password": "x" * 12})

    def test_sin_perfil_no_puede_ver_el_panel_ni_subir_ni_la_agenda(self):
        for ruta in ("/", "/subir/", "/agenda/"):
            resp = self.client.get(ruta)
            self.assertEqual(resp.status_code, 403, f"{ruta} debería dar 403 sin perfil")


class PermisoGenerarCartasTest(TestCase):
    """Operador, Secretario(a) y Administrador pueden generar cartas;
    Jueza/Juez no (ver la tabla de README.md)."""

    def setUp(self):
        self.juzgado = crear_juzgado()

    def _puede_ver_subir(self, rol):
        crear_usuario(f"user_{rol}", rol=rol, juzgado=self.juzgado)
        ingresar(self.client, f"user_{rol}")
        return self.client.get("/subir/").status_code

    def test_operador_puede_entrar_a_subir(self):
        self.assertEqual(self._puede_ver_subir(Rol.OPERADOR), 200)

    def test_secretario_puede_entrar_a_subir(self):
        self.assertEqual(self._puede_ver_subir(Rol.SECRETARIO), 200)

    def test_administrador_puede_entrar_a_subir(self):
        self.assertEqual(self._puede_ver_subir(Rol.ADMINISTRADOR), 200)

    def test_juez_NO_puede_entrar_a_subir(self):
        self.assertEqual(self._puede_ver_subir(Rol.JUEZ), 403)

    def test_juez_NO_puede_postear_a_subir(self):
        crear_usuario("jueza1", rol=Rol.JUEZ, juzgado=self.juzgado)
        ingresar(self.client, "jueza1")
        resp = self.client.post("/subir/", {})
        self.assertEqual(resp.status_code, 403)


class PermisoAgendaTest(TestCase):
    """Los 4 roles pueden VER y editar la agenda (agenda:editar está en
    True para todos en la matriz actual) — a diferencia de generar
    cartas, donde Jueza/Juez queda afuera."""

    def setUp(self):
        self.juzgado = crear_juzgado()

    def test_los_4_roles_ven_la_agenda(self):
        for rol in (Rol.OPERADOR, Rol.SECRETARIO, Rol.JUEZ, Rol.ADMINISTRADOR):
            with self.subTest(rol=rol):
                client = self.client_class()
                crear_usuario(f"agenda_{rol}", rol=rol, juzgado=self.juzgado)
                ingresar(client, f"agenda_{rol}")
                self.assertEqual(client.get("/agenda/").status_code, 200)

    def test_juez_SI_puede_agregar_un_bloque_de_horario(self):
        """A diferencia de generar cartas: agenda:editar es True para
        Jueza/Juez en la matriz vigente (nucleo.models.PERMISOS_POR_ROL).
        Si esto cambia algún día, esta prueba debe fallar y avisarlo —
        no silenciarlo."""
        crear_usuario("jueza2", rol=Rol.JUEZ, juzgado=self.juzgado)
        ingresar(self.client, "jueza2")
        resp = self.client.post("/agenda/", {
            "fecha": FECHA_FUTURA.isoformat(), "hora": "09:00", "cupos_totales": 10,
        }, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(self.juzgado.bloques_horario.filter(fecha=FECHA_FUTURA).exists())
