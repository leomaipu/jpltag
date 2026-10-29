"""
La generación de cartas en dos fases (procesos/generacion.py): la web
prepara y deja la corrida en cola; el worker (manage.py
procesar_generaciones) arma los paquetes. Más los controles que salieron
de la auditoría del 27-oct-2026: patentes que ya recibieron carta (sin
volver a subir la nómina), «Citación y sentencia» sin cupo, tope por
archivo, paquetes que fallan, días sin juzgado, firmante y descarga en
.zip. Datos sintéticos.
"""

import io
import os
import shutil
import tempfile
import time as reloj
import zipfile
from datetime import date, time, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from nucleo.models import BloqueHorario, Rol
from procesos import generacion
from procesos.models import ArchivoGenerado, GeneracionCartas

from .fixtures import construir_nomina_xlsx, crear_juzgado, crear_usuario, fila_nomina, ingresar

_TMP_SALIDAS = tempfile.mkdtemp(prefix="jpltag-pruebas-salidas-generacion-")


def _filas(patentes, dia=3):
    return [
        fila_nomina(pat=p, nro_parte=str(5000 + i), fecha=f"{dia:02d}/09/2026")
        for i, p in enumerate(patentes)
    ]


@override_settings(SALIDAS_ROOT=_TMP_SALIDAS)
class _Base(TestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(_TMP_SALIDAS, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.j1 = crear_juzgado(slug="jpl1", dia_desde=1, dia_hasta=10)
        self.sec1 = crear_usuario("sec1", rol=Rol.SECRETARIO, juzgado=self.j1)
        ingresar(self.client, "sec1")
        self.bloque = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=date.today() + timedelta(days=10), hora=time(9, 30), cupos_totales=10,
        )

    def datos(self, patentes=("AAAA11",), tipo="CITACION", dia=3, **extra):
        datos = {
            "nomina": construir_nomina_xlsx(_filas(patentes, dia)),
            "tipo_carta": tipo,
            "nombre_firmante": "Secretaria de Prueba",
            "cargo_firma": "SECRETARIA",
            "n_cartas_paquete": 50,
        }
        if tipo == "SENTENCIA":
            datos["fecha_audiencia"] = date.today().isoformat()
        datos.update(extra)
        return datos

    def subir(self, patentes=("AAAA11",), tipo="CITACION", dia=3, **extra):
        return self.client.post("/subir/", self.datos(patentes, tipo, dia, **extra), follow=True)

    def cupos_usados(self):
        self.bloque.refresh_from_db()
        return self.bloque.cupos_usados

    def carpetas_de_salida(self):
        raiz = Path(_TMP_SALIDAS) / "jpl1"
        return list(raiz.iterdir()) if raiz.exists() else []


@override_settings(JPLTAG_GENERAR_EN_LA_PETICION=False)
class ColaYWorkerTest(_Base):
    """Con el worker de verdad: la web solo deja la corrida en cola."""

    def test_la_web_deja_la_corrida_en_cola_con_los_cupos_reservados(self):
        resp = self.subir(("AAAA11", "BBBB22"))
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.PENDIENTE)
        self.assertEqual(self.cupos_usados(), 2)
        self.assertContains(resp, "En cola")
        self.assertContains(resp, '<meta http-equiv="refresh" content="10">')
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).exists())

    def test_el_worker_la_genera_y_borra_el_trabajo(self):
        self.subir(("AAAA11", "BBBB22", "CCCC33"), n_cartas_paquete=2)
        self.assertIsNotNone(generacion.procesar_siguiente("w1"))
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual((corrida.lotes_hechos, corrida.lotes_total), (2, 2))
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).count(), 2)
        self.assertFalse((Path(_TMP_SALIDAS) / corrida.carpeta_salida / generacion.TRABAJO).exists())
        self.assertIsNone(generacion.procesar_siguiente("w1"))
        resp = self.client.get(f"/resultado/{corrida.pk}/")
        self.assertContains(resp, "Cartas listas")
        self.assertNotContains(resp, 'http-equiv="refresh"')

    def test_el_comando_procesa_la_cola_en_orden_de_llegada(self):
        self.subir(("AAAA11",))
        self.subir(("BBBB22",))
        with mock.patch("procesos.management.commands.procesar_generaciones.LATIDO",
                        Path(tempfile.gettempdir()) / "jpltag-latido-prueba"):
            call_command("procesar_generaciones", "--una-vez")
        estados = list(GeneracionCartas.objects.order_by("pk").values_list("estado", flat=True))
        self.assertEqual(estados, ["OK", "OK"])

    def test_la_pantalla_dice_cuantas_hay_antes(self):
        self.subir(("AAAA11",))
        self.subir(("BBBB22",))
        segunda = GeneracionCartas.objects.order_by("pk").last()
        self.assertContains(self.client.get(f"/resultado/{segunda.pk}/"), "1 generación antes")

    def test_un_segundo_worker_no_toma_la_corrida_de_otro_con_latido(self):
        self.subir(("AAAA11",))
        self.assertEqual(generacion.tomar_siguiente("w1").worker, "w1")
        self.assertIsNone(generacion.tomar_siguiente("w2"))

    def test_una_en_curso_sin_latido_la_retoma_otro_y_el_primero_se_detiene(self):
        self.subir(("AAAA11", "BBBB22"), n_cartas_paquete=1)
        del_primero = generacion.tomar_siguiente("w1")
        GeneracionCartas.objects.update(actualizado=timezone.now() - timedelta(minutes=30))
        generacion.procesar_siguiente("w2")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual((corrida.estado, corrida.worker), (GeneracionCartas.Estado.OK, "w2"))
        archivos = corrida.archivos.count()
        # el primero «despierta»: su latido ya no es de él, no genera ni cierra nada
        with self.assertLogs("procesos.generacion", level="WARNING"):
            generacion.ejecutar(del_primero)
        corrida.refresh_from_db()
        self.assertEqual((corrida.estado, corrida.archivos.count()), (GeneracionCartas.Estado.OK, archivos))
        self.assertEqual(self.cupos_usados(), 2)

    def test_al_detenerse_vuelve_a_la_cola_y_se_retoma_desde_el_ultimo_paquete(self):
        from motor import plantilla

        self.subir(("AAAA11", "BBBB22", "CCCC33"), n_cartas_paquete=1)
        llamadas = []

        def detener_tras_el_primero():
            raise generacion.Detenido()

        generacion.procesar_siguiente("w1", al_latir=detener_tras_el_primero)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual((corrida.estado, corrida.lotes_hechos), (GeneracionCartas.Estado.PENDIENTE, 1))

        original = plantilla._armar_lote
        with mock.patch.object(plantilla, "_armar_lote",
                               side_effect=lambda *a: llamadas.append(a[1]) or original(*a)):
            with override_settings(), mock.patch.object(plantilla, "_en_proceso_aparte", plantilla._llamar):
                generacion.procesar_siguiente("w2")
        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.OK)
        self.assertEqual(llamadas, [2, 3])   # el paquete 1 no se volvió a armar
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).count(), 3)
        seguimiento = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.SEGUIMIENTO)
        df = pd.read_excel(Path(_TMP_SALIDAS) / seguimiento.ruta)
        self.assertEqual(sorted(df["N° de Carta"]), [1, 2, 3])
        self.assertEqual(self.cupos_usados(), 3)

    def test_si_la_cancelan_mientras_se_genera_el_worker_se_detiene_y_borra_lo_escrito(self):
        self.subir(("AAAA11", "BBBB22"), n_cartas_paquete=1)

        def cancelar_al_primer_paquete():
            GeneracionCartas.objects.update(actualizado=timezone.now() - timedelta(minutes=30))
            generacion.cancelar(GeneracionCartas.objects.get().pk, self.j1, "prueba")

        with self.assertLogs("procesos.generacion", level="WARNING"):
            generacion.procesar_siguiente("w1", al_latir=cancelar_al_primer_paquete)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.FALLIDO)
        self.assertFalse(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).exists())
        self.assertFalse((Path(_TMP_SALIDAS) / corrida.carpeta_salida / "cartas").exists())
        self.assertEqual(self.cupos_usados(), 0)

    def test_si_la_generacion_falla_los_cupos_vuelven(self):
        self.subir(("AAAA11", "BBBB22"))
        with mock.patch("procesos.generacion.generar_cartas_certificadas", side_effect=RuntimeError("x")), \
                self.assertLogs("procesos.generacion", level="ERROR"):
            generacion.procesar_siguiente("w1")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.FALLIDO)
        self.assertIn("error inesperado", corrida.mensaje_error)
        self.assertEqual(self.cupos_usados(), 0)

    def test_una_en_cola_se_puede_cancelar(self):
        self.subir(("AAAA11", "BBBB22"))
        corrida = GeneracionCartas.objects.get()
        with self.assertLogs("procesos.generacion", level="WARNING"):
            resp = self.client.post(f"/resultado/{corrida.pk}/cancelar/", follow=True)
        self.assertContains(resp, "Volvieron 2 cupos a la agenda")
        corrida.refresh_from_db()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.FALLIDO)
        self.assertEqual(self.cupos_usados(), 0)
        self.assertIsNone(generacion.procesar_siguiente("w1"))

    def test_una_que_se_esta_generando_no_se_cancela(self):
        self.subir(("AAAA11",))
        corrida = generacion.tomar_siguiente("w1")
        resp = self.client.post(f"/resultado/{corrida.pk}/cancelar/", follow=True)
        self.assertContains(resp, "ya no se puede cancelar")
        self.assertEqual(self.cupos_usados(), 1)

    def test_no_se_cancela_la_de_otro_juzgado(self):
        self.subir(("AAAA11",))
        corrida = GeneracionCartas.objects.get()
        j2 = crear_juzgado(slug="jpl2", nombre="2do JPL", dia_desde=11, dia_hasta=20)
        crear_usuario("sec2", rol=Rol.SECRETARIO, juzgado=j2)
        self.client.logout()
        ingresar(self.client, "sec2")
        self.assertEqual(self.client.post(f"/resultado/{corrida.pk}/cancelar/").status_code, 403)


class ProcesoDelPaqueteTest(TestCase):
    def test_un_paquete_colgado_se_corta(self):
        from motor import plantilla

        with mock.patch.object(plantilla, "TIEMPO_MAXIMO_LOTE", 1):
            with self.assertRaises(plantilla.LoteInterrumpido):
                plantilla._en_proceso_aparte(reloj.sleep, 30)


class TopePorArchivoTest(_Base):
    def test_el_formulario_no_acepta_paquetes_mas_grandes_que_el_tope(self):
        resp = self.subir(n_cartas_paquete=51)
        self.assertContains(resp, "El máximo es 50 cartas por archivo.")
        self.assertFalse(GeneracionCartas.objects.exists())

    @override_settings(JPLTAG_MAX_CARTAS_POR_ARCHIVO=2)
    def test_todo_en_un_archivo_solo_hasta_el_tope(self):
        resp = self.subir(("AAAA11", "BBBB22", "CCCC33"), n_cartas_paquete=0)
        self.assertContains(resp, "un solo archivo admite hasta 2")
        self.assertFalse(GeneracionCartas.objects.exists())
        # no quedan en disco los datos personales de una nómina que no se procesó
        self.assertEqual(self.carpetas_de_salida(), [])


class EnvioRepetidoTest(_Base):
    def test_pregunta_sin_pedir_de_nuevo_la_nomina(self):
        self.subir(("AAAA11", "BBBB22"))
        resp = self.subir(("BBBB22", "CCCC33"))
        self.assertContains(resp, "1 de las 2 patentes de esta nómina ya recibieron carta de septiembre 2026")
        self.assertContains(resp, "quedó guardada: no hace falta volver a subirla")
        self.assertContains(resp, 'name="nomina_guardada"')
        self.assertNotContains(resp, 'type="file"')
        self.assertEqual(GeneracionCartas.objects.count(), 1)
        self.assertEqual(self.cupos_usados(), 2)

    def _responder(self, resp, repetidas):
        token = resp.context["form"]["nomina_guardada"].value()
        return self.client.post("/subir/", {
            "juzgado": "jpl1", "nomina_guardada": token, "repetidas": repetidas,
            "tipo_carta": "CITACION", "nombre_firmante": "Secretaria de Prueba",
            "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        }, follow=True)

    def test_generar_solo_las_que_faltan(self):
        self.subir(("AAAA11", "BBBB22"))
        resp = self._responder(self.subir(("BBBB22", "CCCC33")), "omitir")
        nueva = GeneracionCartas.objects.latest("pk")
        self.assertEqual(nueva.patentes, ["CCCC33"])
        self.assertEqual(self.cupos_usados(), 3)
        self.assertContains(resp, "Cartas listas")

    def test_generar_todas_de_nuevo(self):
        self.subir(("AAAA11",))
        self._responder(self.subir(("AAAA11",)), "incluir")
        self.assertEqual(GeneracionCartas.objects.filter(estado=GeneracionCartas.Estado.OK).count(), 2)

    def test_si_no_falta_ninguna_lo_dice(self):
        self.subir(("AAAA11",))
        resp = self._responder(self.subir(("AAAA11",)), "omitir")
        self.assertContains(resp, "no falta ninguna")
        self.assertEqual(GeneracionCartas.objects.count(), 1)

    def test_la_nomina_guardada_es_solo_de_quien_la_subio(self):
        self.subir(("AAAA11",))
        resp = self.subir(("AAAA11",))
        crear_usuario("sec1b", rol=Rol.SECRETARIO, juzgado=self.j1)
        self.client.logout()
        ingresar(self.client, "sec1b")
        resp = self._responder(resp, "incluir")
        self.assertContains(resp, "La nómina guardada ya no está disponible")
        self.assertEqual(GeneracionCartas.objects.count(), 1)

    def test_la_misma_nomina_guardada_no_se_usa_dos_veces(self):
        """Doble clic en «Continuar»: el segundo envío no crea otra corrida."""
        self.subir(("AAAA11",))
        pregunta = self.subir(("AAAA11", "BBBB22"))
        token = pregunta.context["form"]["nomina_guardada"].value()
        carpeta, _ = generacion.leer_nomina_guardada(token, self.j1, self.sec1)
        (Path(_TMP_SALIDAS) / carpeta / generacion.RECLAMADA).touch()   # el primer envío la tomó
        resp = self._responder(pregunta, "omitir")
        self.assertContains(resp, "ya se está procesando")
        self.assertEqual(GeneracionCartas.objects.count(), 1)
        self.assertTrue((Path(_TMP_SALIDAS) / carpeta / generacion.NOMINA).exists())

    def test_una_carpeta_la_usa_una_sola_corrida(self):
        from django.db import IntegrityError, transaction
        self.subir(("AAAA11",))
        corrida = GeneracionCartas.objects.get()
        with self.assertRaises(IntegrityError), transaction.atomic():
            GeneracionCartas.objects.create(
                juzgado=self.j1, usuario=self.sec1, nombre_archivo_nomina="x.xlsx",
                carpeta_salida=corrida.carpeta_salida,
            )

    def test_si_falta_cupo_despues_de_confirmar_la_nomina_sigue_guardada(self):
        self.bloque.cupos_totales = 1
        self.bloque.save()
        self.subir(("AAAA11",), tipo="CITACION_SENTENCIA")
        pregunta = self.subir(("AAAA11", "BBBB22"), tipo="CITACION_SENTENCIA")
        token = pregunta.context["form"]["nomina_guardada"].value()
        resp = self.client.post("/subir/", {
            "juzgado": "jpl1", "nomina_guardada": token, "repetidas": "incluir",
            "tipo_carta": "CITACION_SENTENCIA", "nombre_firmante": "Secretaria de Prueba",
            "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        })
        self.assertContains(resp, "Faltan 2 cupos")
        self.assertContains(resp, "quedó guardada")
        self.assertContains(resp, 'name="nomina_guardada"')
        self.assertContains(resp, 'name="repetidas" value="incluir"')
        # se agregan cupos y se continúa con la misma nómina
        self.bloque.cupos_totales = 10
        self.bloque.save()
        nuevo_token = resp.context["form"]["nomina_guardada"].value()
        self.client.post("/subir/", {
            "juzgado": "jpl1", "nomina_guardada": nuevo_token, "repetidas": "incluir",
            "tipo_carta": "CITACION_SENTENCIA", "nombre_firmante": "Secretaria de Prueba",
            "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        })
        self.assertEqual(GeneracionCartas.objects.count(), 2)

    def test_el_token_exige_el_formato_de_carpeta(self):
        token = generacion.firmar_nomina_guardada(f"jpl1/../jpl2/{'a' * 32}", self.j1, self.sec1, "n.xlsx")
        self.assertIsNone(generacion.leer_nomina_guardada(token, self.j1, self.sec1))

    def test_un_token_alterado_no_sirve(self):
        resp = self.client.post("/subir/", {
            "juzgado": "jpl1", "nomina_guardada": "jpl2/otra:alterado", "repetidas": "incluir",
            "tipo_carta": "CITACION", "nombre_firmante": "X", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        })
        self.assertContains(resp, "La nómina guardada ya no está disponible")

    def test_citacion_y_citacion_con_sentencia_son_la_misma_familia(self):
        self.subir(("AAAA11",), tipo="CITACION")
        resp = self.subir(("AAAA11",), tipo="CITACION_SENTENCIA")
        self.assertContains(resp, "ya recibieron carta")

    def test_la_sentencia_despues_de_la_citacion_no_pregunta(self):
        self.subir(("AAAA11",))
        self.subir(("AAAA11",), tipo="SENTENCIA")
        self.assertEqual(GeneracionCartas.objects.count(), 2)

    def test_otras_patentes_del_mismo_mes_no_preguntan(self):
        self.subir(("AAAA11",))
        resp = self.subir(("BBBB22",))
        self.assertNotContains(resp, "ya recibieron carta")
        self.assertEqual(GeneracionCartas.objects.count(), 2)

    def test_una_corrida_fallida_no_cuenta(self):
        with mock.patch("procesos.generacion.generar_cartas_certificadas", side_effect=RuntimeError("x")), \
                self.assertLogs("procesos.generacion", level="ERROR"):
            self.subir(("AAAA11",))
        self.subir(("AAAA11",))
        self.assertEqual(GeneracionCartas.objects.filter(estado=GeneracionCartas.Estado.OK).count(), 1)

    def test_las_nominas_que_nadie_confirmo_se_borran(self):
        propia = tempfile.mkdtemp(prefix="jpltag-pruebas-limpieza-")
        self.addCleanup(shutil.rmtree, propia, ignore_errors=True)
        with override_settings(SALIDAS_ROOT=propia):
            self._limpieza(Path(propia) / "jpl1")

    def _limpieza(self, raiz):
        self.carpetas_de_salida = lambda: list(raiz.iterdir())
        self.subir(("AAAA11",))
        self.subir(("AAAA11",))   # queda guardada esperando respuesta
        self.assertEqual(len(self.carpetas_de_salida()), 2)
        self.assertEqual(generacion.limpiar_carpetas_huerfanas(), 0)   # todavía vigente
        huerfana = next(c for c in self.carpetas_de_salida()
                        if not GeneracionCartas.objects.filter(carpeta_salida=f"jpl1/{c.name}").exists())
        viejo = reloj.time() - 2 * 86400
        os.utime(huerfana, (viejo, viejo))
        self.assertEqual(generacion.limpiar_carpetas_huerfanas(), 1)
        self.assertFalse(huerfana.exists())
        self.assertEqual(len(self.carpetas_de_salida()), 1)


class CitacionYSentenciaSinCupoTest(_Base):
    def test_sin_cupo_para_todas_no_se_genera_ni_se_gastan_cupos(self):
        self.bloque.cupos_totales = 1
        self.bloque.save()
        resp = self.subir(("AAAA11", "BBBB22"), tipo="CITACION_SENTENCIA")
        self.assertContains(resp, "Faltan 1 cupos en la agenda: hay 1 libres para 2 patentes")
        self.assertFalse(GeneracionCartas.objects.exists())
        self.assertEqual(self.cupos_usados(), 0)

    def test_con_cupo_para_todas_genera(self):
        self.subir(("AAAA11", "BBBB22"), tipo="CITACION_SENTENCIA")
        self.assertEqual(GeneracionCartas.objects.get().estado, GeneracionCartas.Estado.OK)
        self.assertEqual(self.cupos_usados(), 2)

    def test_la_citacion_sola_sigue_saliendo_a_coordinar(self):
        self.bloque.cupos_totales = 1
        self.bloque.save()
        self.subir(("AAAA11", "BBBB22"), tipo="CITACION")
        self.assertEqual(GeneracionCartas.objects.get().total_sin_cupo_agenda, 1)


class PaqueteQueFallaTest(_Base):
    def test_sus_cupos_vuelven_y_no_queda_en_el_seguimiento(self):
        from motor import plantilla

        original = plantilla.renderizar_carta

        def falla_con_bbbb(ruta, contexto, config):
            if contexto["patente"].startswith("BBBB22"):
                raise ValueError("dato con RUT 11.111.111-1 que no debe verse")
            return original(ruta, contexto, config)

        with mock.patch("motor.plantilla.renderizar_carta", side_effect=falla_con_bbbb), \
                self.assertLogs("motor.plantilla", level="ERROR"):
            resp = self.subir(("AAAA11", "BBBB22", "CCCC33"), n_cartas_paquete=1)

        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.CON_ERRORES)
        self.assertEqual(corrida.patentes, ["AAAA11", "CCCC33"])
        self.assertEqual(self.cupos_usados(), 2)
        self.assertEqual(corrida.cupos_por_bloque, {str(self.bloque.pk): 2})
        self.assertContains(resp, "El lote 2 (cartas 2 a 2) no se pudo generar")
        self.assertNotContains(resp, "Traceback")
        self.assertNotContains(resp, "11.111.111-1")

        seguimiento = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.SEGUIMIENTO)
        df = pd.read_excel(Path(_TMP_SALIDAS) / seguimiento.ruta)
        self.assertEqual(sorted(df["Patente"]), ["AAAA11", "CCCC33"])
        self.assertEqual(sorted(df["N° de Carta"]), [1, 3])
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).count(), 2)

    def test_un_paquete_cuyo_proceso_muere_no_tumba_al_worker(self):
        """Así se ve que el sistema mate por memoria el proceso de un paquete."""
        from motor import plantilla

        original = plantilla.renderizar_carta

        def muere_con_bbbb(ruta, contexto, config):
            if contexto["patente"].startswith("BBBB22"):
                os._exit(137)
            return original(ruta, contexto, config)

        with mock.patch("motor.plantilla.renderizar_carta", side_effect=muere_con_bbbb), \
                self.assertLogs("motor.plantilla", level="ERROR"):
            self.subir(("AAAA11", "BBBB22", "CCCC33"), n_cartas_paquete=1)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.CON_ERRORES)
        self.assertEqual(corrida.patentes, ["AAAA11", "CCCC33"])


class PatentesSinJuzgadoTest(_Base):
    def test_un_dia_que_ningun_juzgado_procesa_queda_en_el_log(self):
        crear_juzgado(slug="jpl3", nombre="3er JPL", dia_desde=21, dia_hasta=30)
        filas = _filas(("AAAA11",), dia=3) + [
            fila_nomina(pat="ZZZZ99", nro_parte="9001", fecha="31/08/2026"),   # otro mes
            fila_nomina(pat="YYYY88", nro_parte="9002", fecha="25/09/2026"),   # del 3er JPL
            fila_nomina(pat="XXXX77", nro_parte="9003", fecha="15/09/2026"),   # de nadie
        ]
        resp = self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION",
            "nombre_firmante": "Ana", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        }, follow=True)
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.avisos["fuera_de_todo_rango"], 1)
        self.assertContains(resp, "días que ningún juzgado procesa")
        log = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.LOG_ERRORES)
        texto = (Path(_TMP_SALIDAS) / log.ruta).read_text(encoding="utf-8")
        self.assertIn("FUERA_DE_TODO_RANGO", texto)
        self.assertIn("XXXX77", texto)
        self.assertNotIn("YYYY88", texto)


class FirmanteTest(_Base):
    def test_el_nombre_no_viene_prellenado_con_el_usuario_conectado(self):
        self.sec1.first_name, self.sec1.last_name = "Pedro", "Operador"
        self.sec1.save()
        self.assertNotContains(self.client.get("/subir/"), 'value="Pedro Operador"')

    def test_nombre_y_cargo_son_obligatorios(self):
        resp = self.subir(nombre_firmante="", cargo_firma="")
        self.assertContains(resp, "Escribe el nombre de quien firma las cartas.")
        self.assertContains(resp, "Elige el cargo de quien firma.")
        self.assertFalse(GeneracionCartas.objects.exists())


class DescargaZipTest(_Base):
    def test_cada_paquete_se_baja_comprimido(self):
        self.subir(("AAAA11", "BBBB22"), n_cartas_paquete=1)
        paquete = ArchivoGenerado.objects.filter(tipo=ArchivoGenerado.Tipo.CARTAS).order_by("pk").first()
        resp = self.client.get(f"/descargar/{paquete.pk}/zip/")
        self.assertEqual(resp["Content-Type"], "application/zip")
        self.assertIn(f'{Path(paquete.nombre_descarga).stem}.zip', resp["Content-Disposition"])
        with zipfile.ZipFile(io.BytesIO(b"".join(resp.streaming_content))) as zf:
            self.assertEqual(zf.namelist(), [paquete.nombre_descarga])
            self.assertEqual(zf.read(paquete.nombre_descarga)[:2], b"PK")

    def test_no_se_baja_el_de_otro_juzgado(self):
        self.subir(("AAAA11",))
        paquete = ArchivoGenerado.objects.get(tipo=ArchivoGenerado.Tipo.CARTAS)
        j2 = crear_juzgado(slug="jpl2", nombre="2do JPL", dia_desde=11, dia_hasta=20)
        crear_usuario("sec2", rol=Rol.SECRETARIO, juzgado=j2)
        self.client.logout()
        ingresar(self.client, "sec2")
        self.assertEqual(self.client.get(f"/descargar/{paquete.pk}/zip/").status_code, 403)

    def test_la_pantalla_ofrece_zip_y_docx(self):
        self.subir(("AAAA11",))
        resp = self.client.get(f"/resultado/{GeneracionCartas.objects.get().pk}/")
        paquete = ArchivoGenerado.objects.get(tipo=ArchivoGenerado.Tipo.CARTAS)
        self.assertContains(resp, f"/descargar/{paquete.pk}/zip/")
        self.assertContains(resp, f"/descargar/{paquete.pk}/")


class AdminSoloSuperusuariosTest(TestCase):
    def test_una_cuenta_staff_sin_superusuario_no_entra(self):
        get_user_model().objects.create_user("staff", password="x-clave-larga-1", is_staff=True)
        self.client.force_login(get_user_model().objects.get(username="staff"))
        self.assertEqual(self.client.get("/admin/").status_code, 302)

    def test_un_superusuario_si(self):
        get_user_model().objects.create_superuser("root", "r@x.cl", "x-clave-larga-1")
        self.client.force_login(get_user_model().objects.get(username="root"))
        self.assertEqual(self.client.get("/admin/").status_code, 200)
