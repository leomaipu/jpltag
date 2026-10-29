"""
El worker de jpltag: toma las generaciones de cartas en cola y las arma,
una a la vez (ver procesos/generacion.py). En el servidor corre en su
propio contenedor, con la misma imagen que la web:

    python manage.py procesar_generaciones

Debe haber uno solo, pero si por error arrancan dos no se pisan: cada
corrida queda a nombre del worker que la tomó y solo él anota su avance y
la cierra. Una corrida cuyo worker murió se retoma cuando su latido tiene
más de generacion.CORRIDA_INTERRUMPIDA_TRAS, desde el último paquete
terminado. Al recibir SIGTERM (docker stop) se detiene después del paquete
en curso y la corrida vuelve a la cola.

Anota un latido en /tmp/jpltag-worker-latido en cada vuelta y después de
cada paquete de cartas (el healthcheck del contenedor lo mira).
"""

import logging
import os
import signal
import socket
import time
import uuid
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from procesos import generacion

logger = logging.getLogger(__name__)

LATIDO = Path("/tmp/jpltag-worker-latido")
LIMPIEZA_CADA = 3600  # s


class Command(BaseCommand):
    help = "Genera las cartas de las corridas en cola (worker de jpltag)."

    def add_arguments(self, parser):
        parser.add_argument("--espera", type=float, default=5, help="Segundos entre revisiones de la cola vacía.")
        parser.add_argument("--una-vez", action="store_true", help="Procesa lo que haya en la cola y termina.")

    def handle(self, *args, espera, una_vez, **opciones):
        self.detener = False
        signal.signal(signal.SIGTERM, self._pedir_detener)
        signal.signal(signal.SIGINT, self._pedir_detener)
        worker = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        logger.info("Worker %s listo", worker)
        ultima_limpieza = 0.0

        while not self.detener:
            LATIDO.touch()
            # Conexión nueva si PgBouncer o PostgreSQL se reiniciaron.
            close_old_connections()
            try:
                if time.monotonic() - ultima_limpieza > LIMPIEZA_CADA:
                    borradas = generacion.limpiar_carpetas_huerfanas()
                    if borradas:
                        logger.info("Se borraron %s nóminas guardadas que nadie confirmó", borradas)
                    ultima_limpieza = time.monotonic()
                corrida = generacion.procesar_siguiente(worker, al_latir=self._latir)
            except Exception:
                logger.exception("Error del worker; se reintenta en %s s", espera)
                close_old_connections()
                corrida = None
            if corrida is not None:
                corrida.refresh_from_db(fields=["estado"])
                logger.info("Generación %s: %s", corrida.pk, corrida.get_estado_display())
                continue
            if una_vez:
                break
            time.sleep(espera)

    def _latir(self):
        LATIDO.touch()
        if self.detener:
            raise generacion.Detenido()

    def _pedir_detener(self, *_):
        self.detener = True
