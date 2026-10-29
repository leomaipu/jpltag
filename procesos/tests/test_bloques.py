"""
Mantenedor de bloques horarios: editar, cerrar cupos, corregir cupos
ocupados, eliminar y crear en serie, con las reglas que evitan dejar cartas
ya emitidas apuntando a un horario que cambió o desapareció.
Las fechas son relativas a hoy: la agenda no admite fechas pasadas.
"""

import logging
import shutil
import tempfile
from datetime import date, time, timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.utils import timezone

from nucleo.models import BloqueAjuste, BloqueHorario, Rol
from procesos.generacion import (
    CuposInsuficientes, descontar_cupos_usados, devolver_cupos, pool_de_agenda, reservar_agenda,
)

from procesos.models import GeneracionCartas

from .fixtures import construir_nomina_xlsx, crear_juzgado, crear_usuario, fila_nomina, ingresar
from .test_diseno import problemas_de_csp

HOY = timezone.localdate()
MANANA = HOY + timedelta(days=1)
EN_UNA_SEMANA = HOY + timedelta(days=7)


class BloquesBase(TestCase):
    def setUp(self):
        self.j1 = crear_juzgado("jpl1", "1er Juzgado de Policía Local de Maipú")
        self.j2 = crear_juzgado("jpl2", "2do Juzgado de Policía Local de Maipú")
        crear_usuario("sec1", Rol.SECRETARIO, self.j1)
        self.libre = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=MANANA, hora=time(9, 0), cupos_totales=12,
        )
        self.usado = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=MANANA, hora=time(9, 15), cupos_totales=12, cupos_usados=9,
        )
        self.ajeno = BloqueHorario.objects.create(
            juzgado=self.j2, fecha=MANANA, hora=time(10, 0), cupos_totales=8,
        )
        ingresar(self.client, "sec1")

    def editar(self, bloque, **datos):
        """Envía el formulario como lo haría la pantalla recién abierta."""
        actual = BloqueHorario.objects.get(pk=bloque.pk)
        base = {
            "fecha": actual.fecha.isoformat(), "hora": actual.hora.strftime("%H:%M"),
            "cupos_totales": actual.cupos_totales, "cupos_usados": actual.cupos_usados,
            "usados_vistos": actual.cupos_usados, "version": actual.version,
        }
        base.update(datos)
        return self.client.post(f"/agenda/bloque/{bloque.pk}/", base)

    def recargar(self, bloque):
        bloque.refresh_from_db()
        return bloque


class EditarBloqueTest(BloquesBase):
    def test_la_pantalla_trae_los_datos_en_formato_iso(self):
        resp = self.client.get(f"/agenda/bloque/{self.libre.pk}/")
        self.assertContains(resp, f'value="{MANANA.isoformat()}"')
        self.assertContains(resp, 'value="09:00"')

    def test_un_bloque_sin_cartas_cambia_fecha_hora_y_cupos(self):
        resp = self.editar(self.libre, fecha=EN_UNA_SEMANA.isoformat(), hora="11:30", cupos_totales=20)
        self.assertEqual(resp.status_code, 302)
        self.libre = self.recargar(self.libre)
        self.assertEqual((self.libre.fecha, self.libre.hora, self.libre.cupos_totales),
                         (EN_UNA_SEMANA, time(11, 30), 20))

    def test_el_mensaje_confirma_con_el_horario(self):
        resp = self.client.post(f"/agenda/bloque/{self.libre.pk}/", {
            "fecha": MANANA.isoformat(), "hora": "09:00", "cupos_totales": 15, "cupos_usados": 0,
            "usados_vistos": 0, "version": self.libre.version,
        }, follow=True)
        self.assertContains(resp, "actualizado")
        self.assertContains(resp, "09:00")

    def test_con_cartas_no_se_puede_bajar_de_los_cupos_ocupados(self):
        resp = self.editar(self.usado, cupos_totales=5)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ya hay 9 cupos ocupados")
        self.assertEqual(self.recargar(self.usado).cupos_totales, 12)

    def test_con_cartas_si_se_pueden_subir_o_bajar_hasta_lo_ocupado(self):
        self.editar(self.usado, cupos_totales=9)
        self.assertEqual(self.recargar(self.usado).cupos_totales, 9)
        self.editar(self.usado, cupos_totales=30)
        self.assertEqual(self.recargar(self.usado).cupos_totales, 30)

    def test_con_cartas_no_se_mueve_de_fecha_ni_de_hora_aunque_se_manipule_el_formulario(self):
        for cambio in ({"fecha": EN_UNA_SEMANA.isoformat()}, {"hora": "16:00"}):
            resp = self.editar(self.usado, **cambio)
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "9 cartas que citan")
        usado = self.recargar(self.usado)
        self.assertEqual((usado.fecha, usado.hora), (MANANA, time(9, 15)))

    def test_con_cartas_la_pantalla_avisa_y_deja_fecha_y_hora_de_solo_lectura(self):
        resp = self.client.get(f"/agenda/bloque/{self.usado.pk}/")
        self.assertContains(resp, "9 cartas ya citan a este horario")
        self.assertContains(resp, "readonly")

    def test_no_se_puede_repetir_el_horario_de_otro_bloque_del_juzgado(self):
        resp = self.editar(self.libre, hora="09:15")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ya hay un bloque a esa hora ese día")
        self.assertEqual(self.recargar(self.libre).hora, time(9, 0))

    def test_el_mismo_horario_de_otro_juzgado_no_molesta(self):
        resp = self.editar(self.libre, hora="10:00")
        self.assertEqual(resp.status_code, 302)

    def test_no_se_mueve_a_una_fecha_pasada_pero_un_bloque_de_hoy_se_puede_dejar_en_hoy(self):
        resp = self.editar(self.libre, fecha=(HOY - timedelta(days=1)).isoformat())
        self.assertContains(resp, "ya pasó")
        hoy = BloqueHorario.objects.create(juzgado=self.j1, fecha=HOY, hora=time(8, 0), cupos_totales=4)
        self.assertEqual(self.editar(hoy, cupos_totales=6).status_code, 302)

    def test_cupos_invalidos(self):
        for valor, texto in (("0", "al menos 1"), ("501", "máximo"), ("abc", "número entero"), ("", "Indica cuántos")):
            resp = self.editar(self.libre, cupos_totales=valor)
            self.assertEqual(resp.status_code, 200, valor)
            self.assertContains(resp, texto)

    def test_un_bloque_que_ya_paso_no_se_edita(self):
        pasado = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=HOY - timedelta(days=3), hora=time(9, 0), cupos_totales=5,
        )
        self.assertEqual(self.client.get(f"/agenda/bloque/{pasado.pk}/").status_code, 302)
        self.assertEqual(self.editar(pasado, cupos_totales=9).status_code, 302)
        self.assertEqual(self.recargar(pasado).cupos_totales, 5)


class CerrarYCorregirTest(BloquesBase):
    def test_cerrar_deja_los_cupos_en_los_ocupados(self):
        resp = self.client.post(f"/agenda/bloque/{self.usado.pk}/", {"accion": "cerrar"}, follow=True)
        self.assertContains(resp, "cerrado")
        usado = self.recargar(self.usado)
        self.assertEqual((usado.cupos_totales, usado.cupos_usados), (9, 9))
        self.assertEqual(usado.cupos_disponibles, 0)

    def test_cerrar_un_bloque_sin_cartas_no_hace_nada_y_sugiere_eliminar(self):
        resp = self.client.post(f"/agenda/bloque/{self.libre.pk}/", {"accion": "cerrar"}, follow=True)
        self.assertContains(resp, "elimínalo")
        self.assertEqual(self.recargar(self.libre).cupos_totales, 12)

    def test_corregir_cupos_ocupados_queda_registrado(self):
        with self.assertLogs("procesos.views", level=logging.WARNING) as registro:
            self.editar(self.usado, cupos_usados=2, motivo_correccion="Corrida de prueba del lunes")
        self.assertEqual(self.recargar(self.usado).cupos_usados, 2)
        self.assertIn("corregidos a mano", registro.output[0])
        self.assertIn("sec1", registro.output[0])
        ajuste = BloqueAjuste.objects.get(bloque=self.usado)
        self.assertEqual((ajuste.desde, ajuste.hasta, ajuste.nombre_usuario), (9, 2, "sec1"))
        self.assertEqual(ajuste.motivo, "Corrida de prueba del lunes")
        # y la pantalla del bloque lo muestra
        pantalla = self.client.get(f"/agenda/bloque/{self.usado.pk}/")
        self.assertContains(pantalla, "Corrida de prueba del lunes")

    def test_corregir_cupos_ocupados_exige_un_motivo(self):
        for motivo in ("", "  ", "ok"):
            resp = self.editar(self.usado, cupos_usados=2, motivo_correccion=motivo)
            self.assertEqual(resp.status_code, 200, repr(motivo))
            self.assertContains(resp, "Escribe el motivo de la corrección")
            self.assertEqual(self.recargar(self.usado).cupos_usados, 9)
        self.assertFalse(BloqueAjuste.objects.exists())

    def test_sin_tocar_los_ocupados_no_pide_motivo_ni_deja_registro(self):
        self.assertEqual(self.editar(self.usado, cupos_totales=14).status_code, 302)
        self.assertFalse(BloqueAjuste.objects.exists())

    def test_el_registro_de_correcciones_sobrevive_a_eliminar_el_bloque(self):
        self.editar(self.usado, cupos_usados=0, motivo_correccion="Se creó por error")
        self.client.post(f"/agenda/bloque/{self.usado.pk}/eliminar/")
        ajuste = BloqueAjuste.objects.get()
        self.assertIsNone(ajuste.bloque)
        self.assertEqual(ajuste.hora, time(9, 15))

    def test_los_ocupados_no_pueden_superar_el_total(self):
        resp = self.editar(self.libre, cupos_usados=13)
        self.assertContains(resp, "no pueden superar el total")
        self.assertEqual(self.recargar(self.libre).cupos_usados, 0)

    def test_corregir_a_cero_en_el_mismo_envio_libera_la_fecha_y_la_hora(self):
        with self.assertLogs("procesos.views", level=logging.WARNING):
            resp = self.editar(
                self.usado, cupos_usados=0, fecha=EN_UNA_SEMANA.isoformat(), motivo_correccion="Prueba de la secretaría",
            )
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(self.recargar(self.usado).fecha, EN_UNA_SEMANA)


class EliminarBloqueTest(BloquesBase):
    def test_pide_confirmacion_antes_de_eliminar(self):
        resp = self.client.get(f"/agenda/bloque/{self.libre.pk}/eliminar/")
        self.assertContains(resp, "¿Eliminar este bloque?")
        self.assertTrue(BloqueHorario.objects.filter(pk=self.libre.pk).exists())

    def test_elimina_un_bloque_sin_cartas(self):
        resp = self.client.post(f"/agenda/bloque/{self.libre.pk}/eliminar/", follow=True)
        self.assertContains(resp, "eliminado")
        self.assertFalse(BloqueHorario.objects.filter(pk=self.libre.pk).exists())

    def test_no_elimina_un_bloque_con_cartas_ni_por_get_ni_por_post(self):
        resp = self.client.get(f"/agenda/bloque/{self.usado.pk}/eliminar/", follow=True)
        self.assertContains(resp, "No se puede eliminar")
        resp = self.client.post(f"/agenda/bloque/{self.usado.pk}/eliminar/", follow=True)
        self.assertContains(resp, "No se puede eliminar")
        self.assertTrue(BloqueHorario.objects.filter(pk=self.usado.pk).exists())

    def test_la_pantalla_de_edicion_ofrece_eliminar_solo_si_se_puede(self):
        self.assertContains(self.client.get(f"/agenda/bloque/{self.libre.pk}/"), "Eliminar bloque")
        self.assertNotContains(self.client.get(f"/agenda/bloque/{self.usado.pk}/"), "Eliminar bloque")


class AislamientoYPermisosTest(BloquesBase):
    def test_el_bloque_de_otro_juzgado_da_403_en_todo(self):
        for ruta in (f"/agenda/bloque/{self.ajeno.pk}/", f"/agenda/bloque/{self.ajeno.pk}/eliminar/"):
            self.assertEqual(self.client.get(ruta).status_code, 403, ruta)
            self.assertEqual(self.client.post(ruta, {"cupos_totales": 1}).status_code, 403, ruta)
        self.assertEqual(self.recargar(self.ajeno).cupos_totales, 8)
        self.assertTrue(BloqueHorario.objects.filter(pk=self.ajeno.pk).exists())

    def test_un_bloque_inexistente_vuelve_a_la_agenda_con_un_aviso(self):
        for ruta in ("/agenda/bloque/999999/", "/agenda/bloque/999999/eliminar/"):
            resp = self.client.get(ruta, follow=True)
            self.assertContains(resp, "ya no existe")
        resp = self.client.post("/agenda/bloque/999999/eliminar/", follow=True)
        self.assertContains(resp, "ya no existe")

    def test_eliminar_dos_veces_no_da_error(self):
        self.client.post(f"/agenda/bloque/{self.libre.pk}/eliminar/")
        resp = self.client.post(f"/agenda/bloque/{self.libre.pk}/eliminar/", follow=True)
        self.assertContains(resp, "ya no existe")
        self.assertEqual(self.client.get(f"/agenda/bloque/{self.libre.pk}/").status_code, 302)

    def test_un_juzgado_con_caracteres_raros_no_rompe(self):
        for slug in ("%00", "a" * 80, "jpl1;drop"):
            self.assertEqual(self.client.get("/agenda/", {"juzgado": slug}).status_code, 404, slug)

    def test_sin_sesion_se_va_al_ingreso(self):
        self.client.logout()
        resp = self.client.get(f"/agenda/bloque/{self.libre.pk}/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/ingresar/", resp["Location"])

    def test_el_administrador_global_edita_bloques_de_cualquier_juzgado(self):
        crear_usuario("admin", Rol.ADMINISTRADOR, None)
        self.client.logout()
        ingresar(self.client, "admin")
        self.assertEqual(self.editar(self.ajeno, cupos_totales=20).status_code, 302)
        self.assertEqual(self.recargar(self.ajeno).cupos_totales, 20)

    def test_la_jueza_tambien_puede_editar(self):
        crear_usuario("jueza", Rol.JUEZ, self.j1)
        self.client.logout()
        ingresar(self.client, "jueza")
        self.assertEqual(self.editar(self.libre, cupos_totales=14).status_code, 302)


class AgregarBloquesTest(BloquesBase):
    def test_un_horario_repetido_avisa_en_el_formulario(self):
        resp = self.client.post("/agenda/", {
            "accion": "uno", "fecha": MANANA.isoformat(), "hora": "09:00", "cupos_totales": 5,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ya hay un bloque a esa hora ese día")
        self.assertEqual(BloqueHorario.objects.filter(juzgado=self.j1, fecha=MANANA, hora=time(9, 0)).count(), 1)

    def test_no_se_agrega_en_una_fecha_pasada(self):
        resp = self.client.post("/agenda/", {
            "accion": "uno", "fecha": (HOY - timedelta(days=2)).isoformat(), "hora": "09:00", "cupos_totales": 5,
        })
        self.assertContains(resp, "ya pasó")

    def test_la_serie_crea_los_bloques_del_rango(self):
        resp = self.client.post("/agenda/", {
            "accion": "serie", "fecha": EN_UNA_SEMANA.isoformat(), "desde": "09:00", "hasta": "10:00",
            "cada": 15, "cupos_totales": 12,
        }, follow=True)
        horas = list(BloqueHorario.objects.filter(juzgado=self.j1, fecha=EN_UNA_SEMANA)
                     .values_list("hora", flat=True))
        self.assertEqual(horas, [time(9, 0), time(9, 15), time(9, 30), time(9, 45), time(10, 0)])
        self.assertContains(resp, "Se agregaron 5 bloques")

    def test_la_serie_no_toca_los_que_ya_existen(self):
        self.client.post("/agenda/", {
            "accion": "serie", "fecha": MANANA.isoformat(), "desde": "09:00", "hasta": "09:30",
            "cada": 15, "cupos_totales": 3,
        })
        self.assertEqual(self.recargar(self.usado).cupos_usados, 9)
        self.assertEqual(self.recargar(self.usado).cupos_totales, 12)
        self.assertEqual(self.recargar(self.libre).cupos_totales, 12)
        self.assertEqual(BloqueHorario.objects.filter(juzgado=self.j1, fecha=MANANA).count(), 3)

    def test_la_serie_valida_el_rango_y_el_largo(self):
        base = {"accion": "serie", "fecha": EN_UNA_SEMANA.isoformat(), "cada": 15, "cupos_totales": 12}
        resp = self.client.post("/agenda/", {**base, "desde": "11:00", "hasta": "09:00"})
        self.assertContains(resp, "no puede ser antes que la primera")
        resp = self.client.post("/agenda/", {**base, "desde": "06:00", "hasta": "23:00"})
        self.assertContains(resp, "el máximo por vez es 48")
        self.assertFalse(BloqueHorario.objects.filter(fecha=EN_UNA_SEMANA).exists())

    def test_la_serie_de_un_solo_horario_funciona(self):
        self.client.post("/agenda/", {
            "accion": "serie", "fecha": EN_UNA_SEMANA.isoformat(), "desde": "09:00", "hasta": "09:00",
            "cada": 15, "cupos_totales": 4,
        })
        self.assertEqual(BloqueHorario.objects.filter(fecha=EN_UNA_SEMANA).count(), 1)


class AgendaListadoTest(BloquesBase):
    def test_los_bloques_pasados_van_al_historial_sin_enlace_de_edicion(self):
        pasado = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=HOY - timedelta(days=5), hora=time(9, 0), cupos_totales=5, cupos_usados=5,
        )
        html = self.client.get("/agenda/").content.decode()
        self.assertIn("Historial", html)
        self.assertNotIn(f"/agenda/bloque/{pasado.pk}/", html)
        self.assertIn(f"/agenda/bloque/{self.libre.pk}/", html)

    def test_un_bloque_lleno_se_marca(self):
        BloqueHorario.objects.filter(pk=self.usado.pk).update(cupos_usados=12)
        self.assertContains(self.client.get("/agenda/"), "Lleno")

    def test_las_pantallas_nuevas_no_usan_nada_que_la_csp_bloquee(self):
        for ruta in ("/agenda/", f"/agenda/bloque/{self.libre.pk}/", f"/agenda/bloque/{self.usado.pk}/",
                     f"/agenda/bloque/{self.libre.pk}/eliminar/"):
            self.assertEqual(problemas_de_csp(self.client.get(ruta).content.decode()), [], ruta)


class GeneracionYEdicionConcurrenteTest(BloquesBase):
    def test_descontar_cupos_no_pisa_una_edicion_hecha_mientras_corria(self):
        pool, bloque_por_slot = pool_de_agenda(self.j1)
        # alguien edita el bloque después de que la generación leyó la agenda
        BloqueHorario.objects.filter(pk=self.libre.pk).update(cupos_totales=20)
        slot = (self.libre.fecha.strftime("%d/%m/%Y"), "09:00")
        descontar_cupos_usados({"AAAA11": slot, "BBBB22": slot}, bloque_por_slot)
        libre = self.recargar(self.libre)
        self.assertEqual((libre.cupos_usados, libre.cupos_totales), (2, 20))

    def test_descontar_cupos_no_recorta_en_silencio_una_sobreventa(self):
        pool, bloque_por_slot = pool_de_agenda(self.j1)
        BloqueHorario.objects.filter(pk=self.libre.pk).update(cupos_totales=2)
        slot = (self.libre.fecha.strftime("%d/%m/%Y"), "09:00")
        with self.assertRaises(CuposInsuficientes):
            descontar_cupos_usados({f"P{i}": slot for i in range(5)}, bloque_por_slot)
        self.assertEqual(self.recargar(self.libre).cupos_usados, 0)

    def test_descontar_cupos_falla_si_el_bloque_cambio_de_hora(self):
        pool, bloque_por_slot = pool_de_agenda(self.j1)
        BloqueHorario.objects.filter(pk=self.libre.pk).update(hora=time(17, 0))
        slot = (self.libre.fecha.strftime("%d/%m/%Y"), "09:00")
        with self.assertRaises(CuposInsuficientes):
            descontar_cupos_usados({"AAAA11": slot}, bloque_por_slot)

    def test_la_base_rechaza_cupos_ocupados_sobre_el_total(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            BloqueHorario.objects.filter(pk=self.libre.pk).update(cupos_usados=13)


class EdicionConCartasGeneradasMientrasTantoTest(BloquesBase):
    """La persona abre la pantalla con 9 cupos ocupados; antes de guardar, una
    generación de cartas toma 3 más (12). Guardar no puede devolver el
    contador a 9: el bloque quedaría sobrevendido."""

    def _enviar(self, **extra):
        datos = {
            "fecha": MANANA.isoformat(), "hora": "09:15", "cupos_totales": 15,
            "cupos_usados": 9, "usados_vistos": 9, "version": self.usado.version,
        }
        datos.update(extra)
        return self.client.post(f"/agenda/bloque/{self.usado.pk}/", datos)

    def test_la_pantalla_manda_los_ocupados_que_mostro(self):
        self.assertContains(self.client.get(f"/agenda/bloque/{self.usado.pk}/"), 'name="usados_vistos" value="9"')

    def test_si_no_toco_los_ocupados_se_conserva_lo_que_hay_ahora(self):
        BloqueHorario.objects.filter(pk=self.usado.pk).update(cupos_usados=12)
        resp = self._enviar()
        self.assertEqual(resp.status_code, 302)
        usado = self.recargar(self.usado)
        self.assertEqual((usado.cupos_usados, usado.cupos_totales), (12, 15))

    def test_si_los_corrigio_y_cambiaron_mientras_tanto_no_se_guarda_nada(self):
        BloqueHorario.objects.filter(pk=self.usado.pk).update(cupos_usados=12)
        resp = self._enviar(cupos_usados=2, cupos_totales=20, motivo_correccion="Prueba")
        self.assertEqual(resp.status_code, 302)
        usado = self.recargar(self.usado)
        self.assertEqual((usado.cupos_usados, usado.cupos_totales), (12, 12))
        seguimiento = self.client.get(f"/agenda/bloque/{self.usado.pk}/")
        self.assertContains(seguimiento, "cambiaron mientras editabas")

    def test_el_total_nuevo_se_valida_contra_los_ocupados_de_ahora(self):
        BloqueHorario.objects.filter(pk=self.usado.pk).update(cupos_usados=12)
        resp = self._enviar(cupos_totales=10)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Ya hay 12 cupos ocupados")


def _a_las(hora, minuto=0, dia=None):
    """Un «ahora» fijo en la zona horaria de Chile, para probar horas de hoy."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    dia = dia or HOY
    return datetime(dia.year, dia.month, dia.day, hora, minuto, tzinfo=ZoneInfo("America/Santiago"))


class _RelojFijo:
    """Fija la hora «de ahora» en todos los módulos que la consultan."""

    def __init__(self, ahora):
        self.parches = [
            mock.patch(f"{m}.ahora_local", return_value=ahora)
            for m in ("nucleo.models", "procesos.forms", "procesos.views")
        ]

    def __enter__(self):
        for p in self.parches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in self.parches:
            p.stop()


class HorasDeHoyTest(BloquesBase):
    def setUp(self):
        super().setUp()
        self.manana_temprano = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=HOY, hora=time(9, 0), cupos_totales=4,
        )
        self.hoy_tarde = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=HOY, hora=time(16, 0), cupos_totales=4,
        )

    def test_la_zona_horaria_de_la_aplicacion_es_la_de_chile(self):
        # Si la imagen no trajera tzdata, esto fallaría en el despliegue
        # (y la agenda compararía contra una hora equivocada).
        self.assertEqual(str(timezone.get_current_timezone()), "America/Santiago")
        self.assertIsNotNone(timezone.localtime().tzinfo)

    def test_un_bloque_de_hoy_cuya_hora_paso_no_se_ofrece_a_las_cartas(self):
        with _RelojFijo(_a_las(12)):
            pool, por_slot = pool_de_agenda(self.j1)
        horas = {hora for _, hora in por_slot}
        self.assertNotIn("09:00", {h for f, h in por_slot if f == HOY.strftime("%d/%m/%Y")})
        self.assertIn("16:00", horas)

    def test_un_bloque_de_hoy_cuya_hora_paso_queda_en_el_historial_sin_edicion(self):
        with _RelojFijo(_a_las(12)):
            resp = self.client.get(f"/agenda/?juzgado=jpl1")
            self.assertNotContains(resp, f'href="/agenda/bloque/{self.manana_temprano.pk}/"')
            self.assertContains(resp, f'href="/agenda/bloque/{self.hoy_tarde.pk}/"')
            self.assertEqual(self.client.get(f"/agenda/bloque/{self.manana_temprano.pk}/").status_code, 302)
            self.assertEqual(self.client.get(f"/agenda/bloque/{self.manana_temprano.pk}/eliminar/").status_code, 302)

    def test_no_se_crea_un_bloque_hoy_a_una_hora_que_ya_paso(self):
        with _RelojFijo(_a_las(12)):
            resp = self.client.post("/agenda/", {
                "juzgado": "jpl1", "accion": "uno", "fecha": HOY.isoformat(), "hora": "11:00", "cupos_totales": 5,
            })
            self.assertEqual(resp.status_code, 200)
            self.assertContains(resp, "Esa hora ya pasó hoy")
            resp = self.client.post("/agenda/", {
                "juzgado": "jpl1", "accion": "uno", "fecha": HOY.isoformat(), "hora": "13:00", "cupos_totales": 5,
            })
            self.assertEqual(resp.status_code, 302)

    def test_no_se_mueve_un_bloque_a_una_hora_de_hoy_que_ya_paso(self):
        with _RelojFijo(_a_las(12)):
            resp = self.editar(self.hoy_tarde, hora="10:30")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Esa hora ya pasó hoy")

    def test_la_serie_de_hoy_no_puede_empezar_en_una_hora_pasada(self):
        with _RelojFijo(_a_las(12)):
            resp = self.client.post("/agenda/", {
                "juzgado": "jpl1", "accion": "serie", "fecha": HOY.isoformat(),
                "desde": "10:00", "hasta": "14:00", "cada": 30, "cupos_totales": 5,
            })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Esa hora ya pasó hoy")

    def test_fechas_muy_lejanas_se_rechazan(self):
        resp = self.client.post("/agenda/", {
            "juzgado": "jpl1", "accion": "uno", "fecha": "9999-12-31", "hora": "10:00", "cupos_totales": 5,
        })
        self.assertContains(resp, "revisa el año")


class DosPestanasTest(BloquesBase):
    def test_guardar_un_bloque_que_otra_persona_ya_cambio_no_lo_pisa(self):
        viejo = self.libre.version
        # otra pestaña cambia los cupos
        self.editar(self.libre, cupos_totales=20)
        resp = self.client.post(f"/agenda/bloque/{self.libre.pk}/", {
            "fecha": MANANA.isoformat(), "hora": "09:00", "cupos_totales": 7,
            "cupos_usados": 0, "usados_vistos": 0, "version": viejo,
        }, follow=True)
        self.assertContains(resp, "Otra persona cambió este bloque")
        self.assertEqual(self.recargar(self.libre).cupos_totales, 20)

    def test_un_envio_sin_la_huella_se_trata_como_pantalla_vieja(self):
        resp = self.client.post(f"/agenda/bloque/{self.libre.pk}/", {
            "fecha": MANANA.isoformat(), "hora": "09:00", "cupos_totales": 7, "cupos_usados": 0,
        }, follow=True)
        self.assertContains(resp, "Otra persona cambió este bloque")
        self.assertEqual(self.recargar(self.libre).cupos_totales, 12)

    def test_la_huella_cambia_con_fecha_hora_y_cupos_pero_no_con_los_ocupados(self):
        base = self.libre.version
        BloqueHorario.objects.filter(pk=self.libre.pk).update(cupos_usados=3)
        self.assertEqual(self.recargar(self.libre).version, base)
        BloqueHorario.objects.filter(pk=self.libre.pk).update(cupos_totales=13)
        self.assertNotEqual(self.recargar(self.libre).version, base)


_SALIDAS_BLOQUES = tempfile.mkdtemp(prefix="jpltag-pruebas-bloques-")


@override_settings(SALIDAS_ROOT=_SALIDAS_BLOQUES)
class _GeneracionBase(BloquesBase):

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_SALIDAS_BLOQUES, ignore_errors=True)
        super().tearDownClass()

    def subir(self, patentes=("AAAA11",)):
        filas = [fila_nomina(pat=p, nro_parte=str(3000 + i), fecha="03/09/2026") for i, p in enumerate(patentes)]
        return self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION",
            "nombre_firmante": "Secretaria de Prueba", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 0,
        }, follow=True)

    def setUp(self):
        super().setUp()
        # un solo bloque ofrecido, de 2 cupos
        BloqueHorario.objects.filter(juzgado=self.j1).delete()
        self.bloque = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=MANANA, hora=time(9, 0), cupos_totales=2,
        )



@override_settings(SALIDAS_ROOT=_SALIDAS_BLOQUES)
class GeneracionDeCartasYAgendaTest(_GeneracionBase):
    """Lo que la generación de cartas le hace a la agenda: toma cupos de a
    uno, deja anotado qué corrida los tomó y los devuelve si falla."""

    def test_la_corrida_toma_cupos_y_queda_anotada_en_el_bloque(self):
        self.subir(("AAAA11", "BBBB22"))
        bloque = self.recargar(self.bloque)
        self.assertEqual(bloque.cupos_usados, 2)
        generacion = GeneracionCartas.objects.get(juzgado=self.j1)
        self.assertEqual(generacion.cupos_por_bloque, {str(bloque.pk): 2})
        pantalla = self.client.get(f"/agenda/bloque/{bloque.pk}/")
        self.assertContains(pantalla, "De dónde salen los cupos ocupados")
        self.assertContains(pantalla, "2 cartas")
        self.assertContains(pantalla, "sec1")

    def test_dos_corridas_seguidas_no_se_reparten_el_mismo_cupo(self):
        self.subir(("AAAA11", "BBBB22"))
        self.subir(("CCCC33",))
        bloque = self.recargar(self.bloque)
        self.assertEqual((bloque.cupos_usados, bloque.cupos_totales), (2, 2))
        segunda = GeneracionCartas.objects.order_by("creado").last()
        self.assertEqual(segunda.total_sin_cupo_agenda, 1)
        self.assertEqual(segunda.cupos_por_bloque, {})

    def test_reservar_agenda_reparte_y_descuenta_en_un_solo_paso(self):
        grupos = {f"P{i}": {"nro_parte_min": i} for i in range(3)}
        with mock.patch("procesos.generacion.asignar_agenda") as asignar:
            slot = (MANANA.strftime("%d/%m/%Y"), "09:00")
            asignar.return_value = ({"P0": slot, "P1": slot, "P2": "SIN_AGENDA"}, ["P2"])
            reserva, corrida = reservar_agenda(self.j1, grupos)
        self.assertIsNone(corrida)
        self.assertEqual(reserva.tomados, {self.bloque.pk: 2})
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 2)

    def test_si_la_generacion_falla_los_cupos_vuelven_a_la_agenda(self):
        with mock.patch("procesos.generacion.generar_cartas_certificadas", side_effect=RuntimeError("se cayó")), \
                self.assertLogs("procesos.generacion", level="ERROR"):
            self.subir(("AAAA11", "BBBB22"))
        generacion = GeneracionCartas.objects.get(juzgado=self.j1)
        self.assertEqual(generacion.estado, GeneracionCartas.Estado.FALLIDO)
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 0)

    def test_devolver_cupos_nunca_deja_el_contador_bajo_cero(self):
        devolver_cupos({self.bloque.pk: 5})
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 0)

    def test_una_sentencia_no_gasta_cupos(self):
        self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx([fila_nomina(pat="AAAA11", fecha="03/09/2026")]),
            "tipo_carta": "SENTENCIA", "fecha_audiencia": MANANA.isoformat(),
            "nombre_firmante": "Secretaria de Prueba", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 0,
        })
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 0)


class AgendaPantallaTest(BloquesBase):
    def test_los_ids_de_los_dos_formularios_no_se_repiten(self):
        import re
        html = self.client.get("/agenda/?juzgado=jpl1").content.decode()
        ids = re.findall(r'\bid="([^"]+)"', html)
        repetidos = {i for i in ids if ids.count(i) > 1}
        self.assertEqual(repetidos, set())

    def test_errores_de_campo_en_la_edicion_traen_resumen_con_enlaces(self):
        resp = self.editar(self.libre, cupos_totales="0")
        self.assertContains(resp, "Revisa estos datos antes de continuar")
        self.assertContains(resp, 'href="#id_cupos_totales"')
        self.assertContains(resp, "data-enfocar")

    def test_errores_de_la_serie_traen_resumen(self):
        resp = self.client.post("/agenda/", {
            "juzgado": "jpl1", "accion": "serie", "fecha": MANANA.isoformat(),
            "desde": "08:00", "hasta": "20:00", "cada": 5, "cupos_totales": 5,
        })
        self.assertContains(resp, "Revisa estos datos antes de continuar")
        self.assertContains(resp, 'href="#serie_cada"')

    def test_el_menu_marca_agenda_tambien_al_editar(self):
        resp = self.client.get(f"/agenda/bloque/{self.libre.pk}/")
        self.assertRegex(resp.content.decode(), r'href="/agenda/"\s+aria-current="page"')

    def test_el_historial_avisa_cuando_recorta(self):
        for i in range(65):
            BloqueHorario.objects.create(
                juzgado=self.j1, fecha=HOY - timedelta(days=i + 1), hora=time(9, 0), cupos_totales=3,
            )
        resp = self.client.get("/agenda/?juzgado=jpl1")
        self.assertContains(resp, "Se muestran los 60 más recientes de 65")
        completo = self.client.get("/agenda/?juzgado=jpl1&historial=todo")
        self.assertNotContains(completo, "Se muestran los")

    def test_el_enlace_para_crear_uno_nuevo_trae_los_cupos_que_quedan(self):
        resp = self.client.get(f"/agenda/bloque/{self.usado.pk}/")
        self.assertContains(resp, "cupos=3&amp;abrir=uno")
        resp = self.client.get("/agenda/?juzgado=jpl1&cupos=3&abrir=uno")
        self.assertContains(resp, 'name="cupos_totales" value="3"')
        self.assertContains(resp, "<details class=\"avanzado\" id=\"agregar\" open>")

    def test_los_mensajes_de_cupos_invalidos_no_son_los_de_django(self):
        resp = self.editar(self.libre, cupos_totales="-1")
        self.assertContains(resp, "al menos 1 cupo")
        self.assertNotContains(resp, "Asegúrese")



@override_settings(SALIDAS_ROOT=_SALIDAS_BLOQUES)
class CorridaInterrumpidaTest(_GeneracionBase):
    """El proceso muere a mitad de una generación (tiempo agotado, falta de
    memoria, reinicio): nada se ejecuta después, así que los cupos reservados
    tienen que quedar explicados por una corrida «en curso»."""

    def _interrumpir(self):
        with mock.patch("procesos.generacion.generar_cartas_certificadas", side_effect=SystemExit), \
                self.assertRaises(SystemExit):
            self.subir(("AAAA11", "BBBB22"))
        return GeneracionCartas.objects.get(juzgado=self.j1)

    def test_los_cupos_reservados_quedan_explicados_por_una_corrida_en_curso(self):
        corrida = self._interrumpir()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.EN_CURSO)
        self.assertEqual(corrida.cupos_por_bloque, {str(self.bloque.pk): 2})
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 2)

    def test_una_corrida_reciente_no_se_puede_devolver_porque_puede_seguir_en_marcha(self):
        corrida = self._interrumpir()
        resp = self.client.post(
            f"/agenda/bloque/{self.bloque.pk}/", {"accion": "devolver_corrida", "corrida": corrida.pk}, follow=True,
        )
        self.assertContains(resp, "sigue en marcha")
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 2)

    def test_una_corrida_vieja_se_devuelve_desde_la_pantalla_del_bloque(self):
        corrida = self._interrumpir()
        GeneracionCartas.objects.filter(pk=corrida.pk).update(actualizado=timezone.now() - timedelta(minutes=30))
        pantalla = self.client.get(f"/agenda/bloque/{self.bloque.pk}/")
        self.assertContains(pantalla, "Se interrumpió antes de terminar")
        self.assertContains(pantalla, "Devolver los cupos de esa generación")
        with self.assertLogs("procesos.views", level=logging.WARNING):
            resp = self.client.post(
                f"/agenda/bloque/{self.bloque.pk}/", {"accion": "devolver_corrida", "corrida": corrida.pk}, follow=True,
            )
        self.assertContains(resp, "Se devolvieron 2 cupos")
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 0)
        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.FALLIDO)
        # y no se puede devolver dos veces
        resp = self.client.post(
            f"/agenda/bloque/{self.bloque.pk}/", {"accion": "devolver_corrida", "corrida": corrida.pk}, follow=True,
        )
        self.assertContains(resp, "ya terminó")
        self.assertEqual(self.recargar(self.bloque).cupos_usados, 0)

    def test_no_se_devuelve_la_corrida_de_otro_juzgado(self):
        ajena = GeneracionCartas.objects.create(
            juzgado=self.j2, usuario=get_user_model().objects.get(username="sec1"),
            nombre_archivo_nomina="x.xlsx", carpeta_salida="jpl2/x", estado=GeneracionCartas.Estado.EN_CURSO,
            cupos_por_bloque={str(self.ajeno.pk): 3},
        )
        BloqueHorario.objects.filter(pk=self.ajeno.pk).update(cupos_usados=3)
        GeneracionCartas.objects.filter(pk=ajena.pk).update(actualizado=timezone.now() - timedelta(hours=2))
        self.client.post(f"/agenda/bloque/{self.bloque.pk}/", {"accion": "devolver_corrida", "corrida": ajena.pk})
        self.assertEqual(self.recargar(self.ajeno).cupos_usados, 3)


class AdminBloquesTest(BloquesBase):
    def test_el_admin_no_borra_bloques_con_cartas(self):
        from django.contrib.admin.sites import site
        from django.test import RequestFactory
        admin_bloque = site._registry[BloqueHorario]
        req = RequestFactory().get("/")
        req.user = get_user_model().objects.create_superuser("root", "r@x.cl", "x")
        self.assertFalse(admin_bloque.has_delete_permission(req, self.usado))
        self.assertTrue(admin_bloque.has_delete_permission(req, self.libre))
        admin_bloque.delete_queryset(req, BloqueHorario.objects.filter(pk__in=[self.usado.pk, self.libre.pk]))
        self.assertTrue(BloqueHorario.objects.filter(pk=self.usado.pk).exists())
        self.assertFalse(BloqueHorario.objects.filter(pk=self.libre.pk).exists())
        self.assertIn("fecha", admin_bloque.get_readonly_fields(req, self.usado))
        self.assertNotIn("fecha", admin_bloque.get_readonly_fields(req, None))


class FechaDeLasCartasTest(TestCase):
    def test_la_fecha_impresa_es_la_de_chile_aunque_el_servidor_ande_en_utc(self):
        from motor.cartas import ahora_chile
        self.assertEqual(str(ahora_chile().tzinfo), "America/Santiago")
