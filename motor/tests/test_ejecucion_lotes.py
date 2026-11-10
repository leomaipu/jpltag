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
    ErrorEnLote,
    LoteInterrumpido,
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
        self.assertIsInstance(r.error, ErrorEnLote)
        self.assertIn("ValueError", str(r.error))
        self.assertIn("_fallar", r.error.detalle)

    def test_un_hijo_que_muere_sin_resultado_se_informa_y_no_cuelga(self):
        resultados = {r.clave: r for r in EjecutorEnProcesos(paralelos=2).ejecutar(
            [Tarea(1, _morir, (0,)), Tarea(2, _doble, (5,))]
        )}
        self.assertIsInstance(resultados[1].error, LoteInterrumpido)
        self.assertIn("código 9", str(resultados[1].error))
        self.assertEqual(resultados[2].valor, 10)

    def test_una_tarea_colgada_se_corta_y_las_demas_siguen(self):
        inicio = time.monotonic()
        resultados = {r.clave: r for r in EjecutorEnProcesos(paralelos=2, tiempo_maximo=1).ejecutar(
            [Tarea("colgada", time.sleep, (30,)), Tarea("ok", _doble, (4,))]
        )}
        self.assertLess(time.monotonic() - inicio, 10)
        self.assertIsInstance(resultados["colgada"].error, LoteInterrumpido)
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

    def test_en_paralelo_tarda_menos_que_en_serie(self):
        with tempfile.TemporaryDirectory() as carpeta:
            tareas = [Tarea(i, _marcar_y_dormir, (carpeta, str(i), 0.5)) for i in range(4)]
            inicio = time.monotonic()
            list(EjecutorEnProcesos(paralelos=4).ejecutar(tareas))
            self.assertLess(time.monotonic() - inicio, 1.5)   # en serie serían 2 s

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

    def test_no_deja_procesos_zombis(self):
        list(EjecutorEnProcesos(paralelos=3).ejecutar(Tarea(i, _doble, (i,)) for i in range(9)))
        import multiprocessing
        self.assertEqual(multiprocessing.active_children(), [])


if __name__ == "__main__":
    unittest.main()
