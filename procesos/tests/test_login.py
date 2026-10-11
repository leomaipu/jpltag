"""
Login: credenciales correctas/incorrectas, bloqueo por intentos (django-axes,
AXES_FAILURE_LIMIT=5 en config/settings.py) y que las vistas protegidas
redirijan a /ingresar/ sin sesión.
"""

from django.test import TestCase

from .fixtures import CONTRASENA_PRUEBA, crear_juzgado, crear_usuario, ingresar


class LoginTest(TestCase):
    def setUp(self):
        self.juzgado = crear_juzgado()
        self.usuario = crear_usuario("operador1", juzgado=self.juzgado)

    def test_credenciales_correctas_entra_al_panel(self):
        resp = ingresar(self.client, "operador1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.wsgi_request.user.is_authenticated)

    def test_credenciales_incorrectas_no_entra(self):
        resp = ingresar(self.client, "operador1", password="clave-equivocada")
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_usuario_inexistente_no_entra(self):
        resp = self.client.post(
            "/ingresar/", {"username": "no-existe", "password": "lo-que-sea"}, follow=True,
        )
        self.assertFalse(resp.wsgi_request.user.is_authenticated)

    def test_sin_sesion_las_vistas_protegidas_redirigen_a_ingresar(self):
        for ruta in ("/", "/subir/", "/agenda/"):
            resp = self.client.get(ruta, follow=True)
            self.assertEqual(resp.status_code, 200)
            self.assertIn("/ingresar/", resp.redirect_chain[-1][0])

    def test_axes_bloquea_tras_5_intentos_fallidos_aunque_la_clave_sea_correcta_despues(self):
        for _ in range(5):
            ingresar(self.client, "operador1", password="clave-equivocada")
        # AXES_FAILURE_LIMIT=5: el 6° intento, aunque sea con la clave
        # correcta, no debe autenticar — es la protección contra fuerza
        # bruta que hoy solo estaba verificada a mano.
        resp = ingresar(self.client, "operador1", password=CONTRASENA_PRUEBA)
        self.assertFalse(resp.wsgi_request.user.is_authenticated)
