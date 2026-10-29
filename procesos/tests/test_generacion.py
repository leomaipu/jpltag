"""
Los controles de una generación de cartas (procesos/generacion.py) que
salieron de la auditoría del 27-oct-2026: topes de tamaño, una tanda a la
vez, patentes que ya recibieron la misma carta, «Citación y sentencia» sin
cupo, lotes que fallan, patentes de días que ningún juzgado procesa y el
firmante, que ya no se toma del usuario conectado. Datos sintéticos.
"""

import shutil
import tempfile
from datetime import date, time, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd
from django.test import TestCase, override_settings
from django.utils import timezone

from nucleo.models import BloqueHorario, Rol
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
        crear_usuario("sec1", rol=Rol.SECRETARIO, juzgado=self.j1)
        ingresar(self.client, "sec1")
        self.bloque = BloqueHorario.objects.create(
            juzgado=self.j1, fecha=date.today() + timedelta(days=10), hora=time(9, 30), cupos_totales=10,
        )

    def subir(self, patentes=("AAAA11",), tipo="CITACION", dia=3, **extra):
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
        return self.client.post("/subir/", datos, follow=True)

    def cupos_usados(self):
        self.bloque.refresh_from_db()
        return self.bloque.cupos_usados

    def carpetas_de_salida(self):
        raiz = Path(_TMP_SALIDAS) / "jpl1"
        return list(raiz.iterdir()) if raiz.exists() else []


class TopesDeTamanoTest(_Base):
    @override_settings(JPLTAG_MAX_CARTAS_POR_CORRIDA=2)
    def test_una_nomina_con_mas_cartas_que_el_tope_no_se_empieza(self):
        resp = self.subir(("AAAA11", "BBBB22", "CCCC33"))
        self.assertContains(resp, "da 3 cartas para este juzgado y el máximo por generación es 2")
        self.assertFalse(GeneracionCartas.objects.exists())
        self.assertEqual(self.cupos_usados(), 0)
        # no quedan en disco los datos personales de una nómina que no se procesó
        self.assertEqual(self.carpetas_de_salida(), [])

    @override_settings(JPLTAG_MAX_CARTAS_POR_ARCHIVO=2)
    def test_todo_en_un_archivo_solo_hasta_el_tope_por_archivo(self):
        resp = self.subir(("AAAA11", "BBBB22", "CCCC33"), n_cartas_paquete=0)
        self.assertContains(resp, "un solo archivo admite hasta 2")
        self.assertFalse(GeneracionCartas.objects.exists())

    def test_el_formulario_no_acepta_paquetes_mas_grandes_que_el_tope(self):
        resp = self.subir(n_cartas_paquete=5000)
        self.assertContains(resp, "cartas por archivo")
        self.assertFalse(GeneracionCartas.objects.exists())

    def test_la_pantalla_dice_el_tope(self):
        self.assertContains(self.client.get("/subir/"), "Hasta 500 cartas por generación")


class UnaTandaALaVezTest(_Base):
    def _en_curso(self, hace):
        corrida = GeneracionCartas.objects.create(
            juzgado=self.j1, usuario=self.j1.usuarios.first().usuario, nombre_archivo_nomina="otra.xlsx",
            carpeta_salida="jpl1/otra", estado=GeneracionCartas.Estado.EN_CURSO,
        )
        GeneracionCartas.objects.filter(pk=corrida.pk).update(creado=timezone.now() - hace)
        return corrida

    def test_no_se_empieza_otra_mientras_una_sigue_en_marcha(self):
        self._en_curso(timedelta(minutes=1))
        resp = self.subir()
        self.assertContains(resp, "se genera una tanda a la vez")
        self.assertEqual(GeneracionCartas.objects.count(), 1)
        self.assertEqual(self.cupos_usados(), 0)

    def test_tambien_vale_para_la_sentencia(self):
        self._en_curso(timedelta(minutes=1))
        resp = self.subir(tipo="SENTENCIA")
        self.assertContains(resp, "se genera una tanda a la vez")

    def test_una_corrida_interrumpida_no_bloquea(self):
        self._en_curso(timedelta(minutes=30))
        self.subir()
        self.assertEqual(GeneracionCartas.objects.filter(estado=GeneracionCartas.Estado.OK).count(), 1)


class EnvioRepetidoTest(_Base):
    def test_avisa_si_las_patentes_ya_recibieron_la_misma_carta(self):
        self.subir(("AAAA11", "BBBB22"))
        resp = self.subir(("BBBB22", "CCCC33"))
        self.assertContains(resp, "1 de estas 2 patentes ya recibieron carta de citación de septiembre 2026")
        self.assertContains(resp, "BBBB22")
        self.assertContains(resp, 'name="confirmar_repeticion"')
        self.assertEqual(GeneracionCartas.objects.count(), 1)
        self.assertEqual(self.cupos_usados(), 2)

    def test_con_la_confirmacion_genera_igual(self):
        self.subir(("AAAA11",))
        self.subir(("AAAA11",), confirmar_repeticion="on")
        self.assertEqual(GeneracionCartas.objects.filter(estado=GeneracionCartas.Estado.OK).count(), 2)

    def test_otras_patentes_del_mismo_mes_no_avisan(self):
        """Una nómina grande se genera en partes: cada parte trae otras patentes."""
        self.subir(("AAAA11",))
        resp = self.subir(("BBBB22",))
        self.assertNotContains(resp, "ya recibieron carta")
        self.assertEqual(GeneracionCartas.objects.count(), 2)

    def test_otro_tipo_de_carta_no_avisa(self):
        self.subir(("AAAA11",))
        self.subir(("AAAA11",), tipo="SENTENCIA")
        self.assertEqual(GeneracionCartas.objects.count(), 2)

    def test_una_corrida_fallida_no_cuenta(self):
        with mock.patch("procesos.generacion.generar_cartas_certificadas", side_effect=RuntimeError("x")), \
                self.assertLogs("procesos.generacion", level="ERROR"):
            self.subir(("AAAA11",))
        self.subir(("AAAA11",))
        self.assertEqual(GeneracionCartas.objects.filter(estado=GeneracionCartas.Estado.OK).count(), 1)

    def test_la_corrida_guarda_sus_patentes(self):
        self.subir(("BBBB22", "AAAA11"))
        self.assertEqual(GeneracionCartas.objects.get().patentes, ["AAAA11", "BBBB22"])


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


class LoteQueFallaTest(_Base):
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
        # el N° de Carta del lote que falló no se reutiliza
        self.assertEqual(sorted(df["N° de Carta"]), [1, 3])
        self.assertEqual(corrida.archivos.filter(tipo=ArchivoGenerado.Tipo.CARTAS).count(), 2)


class PatentesSinJuzgadoTest(_Base):
    def test_un_dia_que_ningun_juzgado_procesa_queda_en_el_log(self):
        crear_juzgado(slug="jpl3", nombre="3er JPL", dia_desde=21, dia_hasta=30)
        filas = _filas(("AAAA11",), dia=3) + [
            fila_nomina(pat="ZZZZ99", nro_parte="9001", fecha="31/08/2026"),
            fila_nomina(pat="YYYY88", nro_parte="9002", fecha="25/09/2026"),
        ]
        resp = self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION",
            "nombre_firmante": "Ana", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
        }, follow=True)
        corrida = GeneracionCartas.objects.get()
        # el 31/08 es de otro mes; el 25/09 es del 3er JPL: ninguno de los dos cuenta
        self.assertEqual(corrida.avisos["fuera_de_todo_rango"], 0)

        filas.append(fila_nomina(pat="XXXX77", nro_parte="9003", fecha="15/09/2026"))
        resp = self.client.post("/subir/", {
            "nomina": construir_nomina_xlsx(filas), "tipo_carta": "CITACION",
            "nombre_firmante": "Ana", "cargo_firma": "SECRETARIA", "n_cartas_paquete": 50,
            "confirmar_repeticion": "on",
        }, follow=True)
        corrida = GeneracionCartas.objects.latest("creado")
        self.assertEqual(corrida.avisos["fuera_de_todo_rango"], 1)
        self.assertContains(resp, "días que ningún juzgado procesa")
        log = corrida.archivos.get(tipo=ArchivoGenerado.Tipo.LOG_ERRORES)
        texto = (Path(_TMP_SALIDAS) / log.ruta).read_text(encoding="utf-8")
        self.assertIn("FUERA_DE_TODO_RANGO", texto)
        self.assertIn("XXXX77", texto)
        self.assertNotIn("YYYY88", texto)


class FirmanteTest(_Base):
    def test_el_nombre_no_viene_prellenado_con_el_usuario_conectado(self):
        usuario = self.j1.usuarios.first().usuario
        usuario.first_name, usuario.last_name = "Pedro", "Operador"
        usuario.save()
        resp = self.client.get("/subir/")
        self.assertNotContains(resp, 'value="Pedro Operador"')

    def test_nombre_y_cargo_son_obligatorios(self):
        resp = self.subir(nombre_firmante="", cargo_firma="")
        self.assertContains(resp, "Escribe el nombre de quien firma las cartas.")
        self.assertContains(resp, "Elige el cargo de quien firma.")
        self.assertFalse(GeneracionCartas.objects.exists())


class SentenciaQuedaRegistradaTest(_Base):
    def test_la_sentencia_tambien_anota_su_corrida_antes_de_generar(self):
        """Si el proceso muere a la mitad, queda una corrida «en curso» que lo explica."""
        with mock.patch("procesos.generacion.generar_cartas_certificadas", side_effect=SystemExit), \
                self.assertRaises(SystemExit):
            self.subir(("AAAA11",), tipo="SENTENCIA")
        corrida = GeneracionCartas.objects.get()
        self.assertEqual(corrida.estado, GeneracionCartas.Estado.EN_CURSO)
        self.assertEqual(corrida.tipo_carta, "SENTENCIA")
        self.assertEqual(self.cupos_usados(), 0)
