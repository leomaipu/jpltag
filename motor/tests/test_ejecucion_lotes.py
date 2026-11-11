"""El ejecutor de tareas en procesos hijos: paralelismo acotado, errores,
tiempo límite y cancelación sin dejar procesos huérfanos."""

import os
import signal
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from motor.ejecucion_lotes import (
    EjecutorEnProcesos,
    EjecutorEnSerie,
    ErrorEnTarea,
    TareaInterrumpida,
    Tarea,
)


def _doble(x):
    return x * 2


def _fallar(x):
    raise ValueError(f"no se pudo con {x}")


def _morir(_):
    os._exit(9)


def _marcar_y_dormir(carpeta, nombre, segundos):
    """Deja constancia de cuándo empezó y terminó (para medir solapamiento)."""
    Path(carpeta, f"{nombre}.inicio").write_text(str(time.monotonic()))
    time.sleep(segundos)
    Path(carpeta, f"{nombre}.fin").write_text(str(time.monotonic()))
    return nombre


def _dormir_y_doble(segundos, x):
    time.sleep(segundos)
    return x * 2


def _entregar_y_quedarse():
    """Cierra la tubería por la que entrega (como si hubiera terminado) pero
    el proceso sigue vivo."""
    import gc
    from multiprocessing.connection import Connection

    for objeto in gc.get_objects():
        if isinstance(objeto, Connection) and not objeto.closed and objeto.writable:
            objeto.close()
    time.sleep(60)


def _resultado_no_serializable():
    return lambda: None


class _TextoRoto(Exception):
    def __str__(self):
        raise RuntimeError("ni el mensaje se puede leer")


def _fallar_con_texto_roto():
    raise _TextoRoto()


def _hijos_de_este_proceso():
    """Hijos directos según /proc (vivos o zombis), sin recogerlos."""
    yo, hijos = str(os.getpid()), []
    for entrada in os.listdir("/proc"):
        if entrada.isdigit():
            try:
                campos = Path(f"/proc/{entrada}/stat").read_text().rsplit(") ", 1)[1].split()
            except (OSError, IndexError):
                continue
            if campos[1] == yo:
                hijos.append(int(entrada))
    return hijos


def _lanzar_nieto_y_dormir(ruta_pid):
    nieto = subprocess.Popen(["sleep", "60"])
    Path(ruta_pid).write_text(str(nieto.pid))
    time.sleep(60)


def _vivo(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # Un zombi sigue «existiendo» hasta que lo recogen: no cuenta como vivo.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(") ")[1][0] != "Z"
    except OSError:
        return False


def _esperar_que_muera(pid, segundos=5):
    limite = time.monotonic() + segundos
    while _vivo(pid) and time.monotonic() < limite:
        time.sleep(0.05)
    return not _vivo(pid)


class EjecutoresTest(unittest.TestCase):
    def _ambos(self):
        return (EjecutorEnSerie(), EjecutorEnProcesos(paralelos=1), EjecutorEnProcesos(paralelos=3))

    def test_entregan_el_valor_de_cada_tarea_con_su_clave(self):
        for ejecutor in self._ambos():
            with self.subTest(ejecutor=type(ejecutor).__name__):
                resultados = {r.clave: r for r in ejecutor.ejecutar(Tarea(i, _doble, (i,)) for i in range(7))}
                self.assertEqual({k: r.valor for k, r in resultados.items()}, {i: i * 2 for i in range(7)})
                self.assertTrue(all(r.error is None for r in resultados.values()))

    def test_el_error_de_una_tarea_no_detiene_a_las_demas(self):
        for ejecutor in self._ambos():
            with self.subTest(ejecutor=type(ejecutor).__name__):
                tareas = [Tarea(1, _doble, (1,)), Tarea(2, _fallar, (2,)), Tarea(3, _doble, (3,))]
                resultados = {r.clave: r for r in ejecutor.ejecutar(tareas)}
                self.assertEqual((resultados[1].valor, resultados[3].valor), (2, 6))
                self.assertIn("no se pudo con 2", str(resultados[2].error))

    def test_sin_tareas_no_entrega_nada(self):
        for ejecutor in self._ambos():
            self.assertEqual(list(ejecutor.ejecutar([])), [])


class EjecutorEnProcesosTest(unittest.TestCase):
    def test_el_error_del_hijo_trae_su_traceback(self):
        (r,) = EjecutorEnProcesos().ejecutar([Tarea("a", _fallar, ("x",))])
        self.assertIsInstance(r.error, ErrorEnTarea)
        self.assertIn("ValueError", str(r.error))
        self.assertIn("_fallar", r.error.detalle)

    def test_un_hijo_que_muere_sin_resultado_se_informa_y_no_cuelga(self):
        resultados = {r.clave: r for r in EjecutorEnProcesos(paralelos=2).ejecutar(
            [Tarea(1, _morir, (0,)), Tarea(2, _doble, (5,))]
        )}
        self.assertIsInstance(resultados[1].error, TareaInterrumpida)
        self.assertIn("código 9", str(resultados[1].error))
        self.assertEqual(resultados[2].valor, 10)

    def test_una_tarea_colgada_se_corta_y_las_demas_siguen(self):
        inicio = time.monotonic()
        resultados = {r.clave: r for r in EjecutorEnProcesos(paralelos=2, tiempo_maximo=1).ejecutar(
            [Tarea("colgada", time.sleep, (30,)), Tarea("ok", _doble, (4,))]
        )}
        self.assertLess(time.monotonic() - inicio, 10)
        self.assertIsInstance(resultados["colgada"].error, TareaInterrumpida)
        self.assertIn("no terminó en 1 s", str(resultados["colgada"].error))
        self.assertEqual(resultados["ok"].valor, 8)

    def test_nunca_hay_mas_tareas_a_la_vez_que_el_maximo(self):
        with tempfile.TemporaryDirectory() as carpeta:
            tareas = [Tarea(i, _marcar_y_dormir, (carpeta, str(i), 0.3)) for i in range(6)]
            list(EjecutorEnProcesos(paralelos=2).ejecutar(tareas))
            tramos = sorted(
                (float(Path(carpeta, f"{i}.inicio").read_text()), float(Path(carpeta, f"{i}.fin").read_text()))
                for i in range(6)
            )
        for inicio, _ in tramos:
            a_la_vez = sum(1 for i, f in tramos if i <= inicio < f)
            self.assertLessEqual(a_la_vez, 2)

    def test_con_cuatro_en_paralelo_llegan_a_correr_cuatro_a_la_vez(self):
        with tempfile.TemporaryDirectory() as carpeta:
            tareas = [Tarea(i, _marcar_y_dormir, (carpeta, str(i), 0.5)) for i in range(4)]
            list(EjecutorEnProcesos(paralelos=4).ejecutar(tareas))
            tramos = [(float(Path(carpeta, f"{i}.inicio").read_text()), float(Path(carpeta, f"{i}.fin").read_text()))
                      for i in range(4)]
        ultimo_inicio = max(i for i, _ in tramos)
        primer_fin = min(f for _, f in tramos)
        self.assertLess(ultimo_inicio, primer_fin)      # las cuatro estuvieron en curso a la vez

    def test_si_el_consumidor_abandona_se_cortan_las_tareas_en_curso_y_sus_programas(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta_pid = Path(carpeta, "nieto.pid")
            tareas = [Tarea("rapida", _doble, (1,)), Tarea("lenta", _lanzar_nieto_y_dormir, (str(ruta_pid),)),
                      Tarea("nunca", _marcar_y_dormir, (carpeta, "nunca", 0))]
            resultados = EjecutorEnProcesos(paralelos=2).ejecutar(tareas)
            self.assertEqual(next(resultados).clave, "rapida")
            limite = time.monotonic() + 5
            while not ruta_pid.exists() and time.monotonic() < limite:
                time.sleep(0.05)
            nieto = int(ruta_pid.read_text())
            self.assertTrue(_vivo(nieto))
            resultados.close()    # p. ej. el worker recibió la orden de detenerse
            self.assertTrue(_esperar_que_muera(nieto), "quedó huérfano el programa que lanzó la tarea")
            self.assertFalse(Path(carpeta, "nunca.inicio").exists(), "se lanzó una tarea después de abandonar")

    def test_una_excepcion_del_consumidor_tambien_corta_lo_que_estaba_en_curso(self):
        with tempfile.TemporaryDirectory() as carpeta:
            ruta_pid = Path(carpeta, "nieto.pid")
            tareas = [Tarea("rapida", _doble, (1,)), Tarea("lenta", _lanzar_nieto_y_dormir, (str(ruta_pid),))]
            ejecutor = EjecutorEnProcesos(paralelos=2)
            with self.assertRaises(RuntimeError):
                resultados = ejecutor.ejecutar(tareas)
                try:
                    for r in resultados:
                        limite = time.monotonic() + 5
                        while not ruta_pid.exists() and time.monotonic() < limite:
                            time.sleep(0.05)
                        raise RuntimeError("el consumidor falló")
                finally:
                    resultados.close()
            self.assertTrue(_esperar_que_muera(int(ruta_pid.read_text())))

    def test_no_deja_procesos_hijos_ni_zombis_ni_descriptores_abiertos(self):
        descriptores = len(os.listdir("/proc/self/fd"))
        list(EjecutorEnProcesos(paralelos=3).ejecutar(Tarea(i, _doble, (i,)) for i in range(30)))
        self.assertEqual(_hijos_de_este_proceso(), [])
        self.assertEqual(len(os.listdir("/proc/self/fd")), descriptores)

    def test_una_tarea_que_ya_termino_no_se_da_por_vencida_aunque_el_consumidor_se_demore(self):
        """El latido de la corrida (una escritura en la base) puede demorarse: los
        paquetes que terminaron mientras tanto no deben contarse como colgados."""
        tareas = [Tarea(1, _doble, (1,)), Tarea(2, _dormir_y_doble, (0.3, 2)), Tarea(3, _dormir_y_doble, (0.4, 3))]
        resultados = {}
        for r in EjecutorEnProcesos(paralelos=3, tiempo_maximo=1).ejecutar(tareas):
            resultados[r.clave] = r
            time.sleep(1.5)       # el consumidor tarda más que el plazo
        self.assertEqual({k: (r.valor, r.error) for k, r in resultados.items()}, {1: (2, None), 2: (4, None), 3: (6, None)})

    def test_un_hijo_que_entrega_y_no_termina_de_salir_se_corta_sin_frenar_a_los_demas(self):
        from motor import ejecucion_lotes
        from unittest import mock

        inicio = time.monotonic()
        with mock.patch.object(ejecucion_lotes, "_GRACIA_AL_TERMINAR", 0.5):
            resultados = {r.clave: r for r in EjecutorEnProcesos(paralelos=2, tiempo_maximo=20).ejecutar(
                [Tarea("no sale", _entregar_y_quedarse, ()), Tarea("ok", _doble, (4,))]
            )}
        self.assertLess(time.monotonic() - inicio, 10)
        self.assertEqual(resultados["ok"].valor, 8)
        self.assertIsInstance(resultados["no sale"].error, TareaInterrumpida)
        self.assertEqual(_hijos_de_este_proceso(), [])

    def test_si_el_hijo_muerto_ya_fue_recogido_no_se_corta_ningun_grupo(self):
        """Su identificador pudo pasar a otro proceso: cortar «su» grupo sería cortar a otro."""
        from motor import ejecucion_lotes
        from unittest import mock

        hijo = ejecucion_lotes._Hijo(Tarea(1, _morir, (0,)), 30)
        os.waitpid(hijo.proceso.pid, 0)        # alguien más lo recogió (multiprocessing, al lanzar otro)
        self.assertFalse(hijo._sin_recoger())
        with mock.patch.object(os, "killpg") as killpg:
            resultado = hijo.resultado()
        killpg.assert_not_called()
        self.assertIsInstance(resultado.error, TareaInterrumpida)

    def test_si_no_se_puede_crear_el_proceso_falla_esa_tarea_y_no_toda_la_corrida(self):
        from motor import ejecucion_lotes
        from unittest import mock

        real = ejecucion_lotes._Hijo
        intentos = []

        def sin_cupo_la_segunda(tarea, tiempo):
            intentos.append(tarea.clave)
            if tarea.clave == 2:
                raise OSError(11, "Resource temporarily unavailable")
            return real(tarea, tiempo)

        with mock.patch.object(ejecucion_lotes, "_Hijo", sin_cupo_la_segunda):
            resultados = {r.clave: r for r in EjecutorEnProcesos(paralelos=2).ejecutar(
                Tarea(i, _doble, (i,)) for i in (1, 2, 3)
            )}
        self.assertEqual((resultados[1].valor, resultados[3].valor), (2, 6))
        self.assertIsInstance(resultados[2].error, TareaInterrumpida)
        self.assertIn("no se pudo iniciar", str(resultados[2].error))

    def test_un_resultado_que_no_se_puede_entregar_se_informa_como_error_de_la_tarea(self):
        (r,) = EjecutorEnProcesos().ejecutar([Tarea(1, _resultado_no_serializable, ())])
        self.assertIsInstance(r.error, ErrorEnTarea)
        self.assertIn("no se pudo entregar el resultado", str(r.error))

    def test_una_excepcion_cuyo_texto_falla_igual_se_informa(self):
        (r,) = EjecutorEnProcesos().ejecutar([Tarea(1, _fallar_con_texto_roto, ())])
        self.assertIsInstance(r.error, ErrorEnTarea)
        self.assertIn("_TextoRoto", str(r.error))

if __name__ == "__main__":
    unittest.main()
