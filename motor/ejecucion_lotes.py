"""
Cómo se ejecutan las tareas de una corrida (armar cada paquete de cartas):
en serie dentro del mismo proceso, o cada una en un proceso hijo, varias a
la vez.

Este módulo no sabe nada de cartas: recibe tareas (una función y sus
argumentos) y entrega sus resultados a medida que terminan. Quien genera
las cartas (motor.plantilla) elige el ejecutor y no conoce los detalles de
procesos, tuberías ni tiempos límite.

Por qué procesos hijos: armar un .docx de 50 cartas deja ~2,5 MB por carta
que Python ya liberó pero el proceso no devuelve al sistema (fragmentación
de malloc, medido el 29-oct-2026): miles de cartas seguidas en el mismo
proceso superan 1 GB. Un hijo por tarea devuelve todo al terminar, y si el
sistema lo mata por memoria solo se pierde esa tarea.
"""

import multiprocessing
import os
import signal
import time
import traceback
from dataclasses import dataclass
from multiprocessing import connection

# Tiempo límite de una tarea si quien crea el ejecutor no indica otro.
TIEMPO_MAXIMO_TAREA = 120  # s
# Lo que se espera a que un hijo que ya entregó su resultado termine de salir.
_GRACIA_AL_TERMINAR = 5  # s


def _describir(exc):
    try:
        return f"{type(exc).__name__}: {exc}"
    except Exception:  # noqa: BLE001 — una excepción cuyo texto falla
        return type(exc).__name__


class TareaInterrumpida(Exception):
    """El proceso que ejecutaba una tarea terminó sin entregar el resultado
    (por ejemplo, el sistema lo cerró por falta de memoria) o no terminó a
    tiempo y se cortó."""


class ErrorEnTarea(Exception):
    """La tarea falló dentro del proceso hijo; `detalle` trae su traceback."""

    def __init__(self, mensaje, detalle):
        super().__init__(mensaje)
        self.detalle = detalle


@dataclass(frozen=True)
class Tarea:
    """Una unidad de trabajo: `funcion(*args)`. `clave` la identifica en el
    resultado (por ejemplo, el número de lote)."""

    clave: object
    funcion: object
    args: tuple = ()


@dataclass(frozen=True)
class Resultado:
    """Cómo terminó una tarea: `valor` si salió bien, `error` si no."""

    clave: object
    valor: object = None
    error: Exception | None = None


class EjecutorEnSerie:
    """Ejecuta las tareas una tras otra en este mismo proceso."""

    def ejecutar(self, tareas):
        for tarea in tareas:
            try:
                yield Resultado(tarea.clave, valor=tarea.funcion(*tarea.args))
            except Exception as exc:  # noqa: BLE001 — el error es el resultado de la tarea
                yield Resultado(tarea.clave, error=exc)


class _Hijo:
    """Un proceso hijo ejecutando una tarea, y la tubería por la que
    entrega su resultado."""

    def __init__(self, tarea, tiempo_maximo):
        contexto = multiprocessing.get_context("fork")
        self.tarea = tarea
        self.tiempo_maximo = tiempo_maximo
        self.lectura, escritura = contexto.Pipe(duplex=False)
        self.proceso = contexto.Process(target=self._correr, args=(tarea, escritura), daemon=True)
        self.proceso.start()
        # El padre cierra su copia: así el fin de archivo llega si el hijo
        # muere, y los hijos siguientes no la heredan.
        escritura.close()
        self.vence = time.monotonic() + tiempo_maximo

    @staticmethod
    def _correr(tarea, escritura):
        # Grupo de procesos propio: al cortar la tarea se cortan también los
        # programas que haya lanzado (LibreOffice al convertir a PDF).
        os.setsid()
        try:
            try:
                mensaje = ("ok", tarea.funcion(*tarea.args))
            except BaseException as exc:  # noqa: BLE001 — todo vuelve al padre
                mensaje = ("error", (_describir(exc), traceback.format_exc()))
            try:
                escritura.send(mensaje)
            except Exception as exc:  # noqa: BLE001 — p. ej. un resultado que no se puede serializar
                escritura.send(("error", (f"no se pudo entregar el resultado: {_describir(exc)}",
                                          traceback.format_exc())))
        finally:
            escritura.close()

    def listo(self):
        """¿Ya entregó su resultado (o murió)?"""
        return self.lectura.poll(0)

    def resultado(self):
        """Lee lo que el hijo entregó (solo cuando su tubería está lista)."""
        try:
            estado, valor = self.lectura.recv()
        except EOFError:
            # Murió sin entregar (p. ej. lo cerró el sistema por memoria): lo
            # que hubiera lanzado quedó sin dueño y se corta con su grupo.
            if self._sin_recoger():
                self._cortar()
            self._esperar()
            return Resultado(self.tarea.clave, error=TareaInterrumpida(
                f"el proceso de la tarea terminó sin resultado (código {self.proceso.exitcode})"
            ))
        self._esperar()
        if estado == "error":
            return Resultado(self.tarea.clave, error=ErrorEnTarea(*valor))
        return Resultado(self.tarea.clave, valor=valor)

    def vencido(self):
        """Corta la tarea que no terminó a tiempo (colgada, p. ej. por un
        lock que el fork heredó tomado): sin esto el worker esperaría para
        siempre y la cola entera se detendría."""
        self.matar()
        return Resultado(self.tarea.clave, error=TareaInterrumpida(
            f"la tarea no terminó en {self.tiempo_maximo} s y se cortó"
        ))

    def matar(self):
        if self.proceso.is_alive() and self._sin_recoger():
            self._cortar()
        self.proceso.join()
        self.lectura.close()

    def _esperar(self):
        # Ya entregó (o murió): solo falta que el proceso salga. Si no sale
        # —cerró la tubería y sigue vivo— se corta, para no detener a los demás.
        self.proceso.join(_GRACIA_AL_TERMINAR)
        if self.proceso.is_alive() and self._sin_recoger():
            self._cortar()
            self.proceso.join()
        self.lectura.close()

    def _sin_recoger(self):
        """¿El hijo sigue sin recoger (vivo o terminado)? Mientras sea así su
        identificador no se reutiliza y cortar su grupo no toca a nadie más.
        multiprocessing puede haberlo recogido ya, al lanzar otro proceso."""
        try:
            os.waitid(os.P_PID, self.proceso.pid, os.WEXITED | os.WNOWAIT | os.WNOHANG)
        except ChildProcessError:
            return False
        return True

    def _cortar(self):
        """Corta al hijo y a todo su grupo. Solo si el hijo está sin recoger
        (vivo, o _sin_recoger): su identificador no pudo reutilizarse."""
        try:
            os.killpg(self.proceso.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Todavía no llegó a crear su grupo: se corta solo a él.
            self.proceso.kill()


class EjecutorEnProcesos:
    """Ejecuta cada tarea en un proceso hijo (fork), hasta `paralelos` a la
    vez, y entrega los resultados a medida que terminan. Una tarea que no
    termina en `tiempo_maximo` segundos se corta.

    Si quien consume los resultados deja de pedirlos (cierra el generador o
    lanza una excepción), las tareas en curso se cortan: no quedan procesos
    huérfanos. Las que no habían empezado no se lanzan."""

    def __init__(self, paralelos=1, tiempo_maximo=None):
        self.paralelos = max(1, int(paralelos))
        self.tiempo_maximo = tiempo_maximo

    def ejecutar(self, tareas):
        tiempo_maximo = self.tiempo_maximo if self.tiempo_maximo is not None else TIEMPO_MAXIMO_TAREA
        pendientes = iter(tareas)
        activos = {}   # tubería de lectura -> _Hijo
        try:
            while True:
                while len(activos) < self.paralelos:
                    tarea = next(pendientes, None)
                    if tarea is None:
                        break
                    try:
                        hijo = _Hijo(tarea, tiempo_maximo)
                    except OSError as exc:
                        # No se pudo crear el proceso (sin memoria o sin cupo
                        # de procesos): falla esta tarea, no toda la corrida.
                        yield Resultado(tarea.clave, error=TareaInterrumpida(
                            f"no se pudo iniciar el proceso de la tarea: {exc}"
                        ))
                        continue
                    activos[hijo.lectura] = hijo
                if not activos:
                    return
                espera = max(0.0, min(h.vence for h in activos.values()) - time.monotonic())
                for lectura in connection.wait(list(activos), timeout=espera):
                    yield activos.pop(lectura).resultado()
                # Mientras se entregaban resultados pudo pasar tiempo (quien
                # los consume también trabaja): una tarea que ya terminó no
                # se da por vencida aunque su plazo se haya cumplido.
                ahora = time.monotonic()
                for lectura in [l for l, h in activos.items() if h.vence <= ahora]:
                    hijo = activos.pop(lectura)
                    yield hijo.resultado() if hijo.listo() else hijo.vencido()
        finally:
            for hijo in activos.values():
                hijo.matar()
