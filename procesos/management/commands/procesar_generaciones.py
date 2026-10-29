"""
El worker de jpltag: toma las generaciones de cartas en cola y las arma,
una a la vez (ver procesos/generacion.py). En el servidor corre en su
propio contenedor, con la misma imagen que la web:

    python manage.py procesar_generaciones

Debe haber UNO solo: al arrancar devuelve a la cola lo que quedó «en
curso» de un worker anterior. Anota un latido en /tmp/jpltag-worker-latido
en cada vuelta y después de cada paquete de cartas (el healthcheck del
contenedor lo mira: un paquete de 50 cartas tarda a lo más ~1 min).
"""

import logging
import signal
import time
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

        retomadas = generacion.retomar_huerfanas()
        if retomadas:
            logger.warning("Se retoman %s generaciones que quedaron en curso", retomadas)
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
                corrida = generacion.procesar_siguiente(al_latir=LATIDO.touch)
            except Exception:
                logger.exception("Error del worker; se reintenta en %s s", espera)
                close_old_connections()
                corrida = None
            if corrida is not None:
                logger.info("Generación %s terminó: %s", corrida.pk, corrida.estado)
                continue
            if una_vez:
                break
            time.sleep(espera)

    def _pedir_detener(self, *_):
        # Termina la corrida en curso solo si alcanza; si no, al volver se
        # retoma desde el principio (retomar_huerfanas).
        self.detener = True
