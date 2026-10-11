# jpltag — imagen de producción.
#
# Sigue las lecciones ya documentadas del servidor (ver "Informe de
# implementación — Servidor maiproject.cl", sección de Soporte
# Existencias/MaiReservas):
#   - El contenedor corre con el filesystem en modo read_only (ver
#     docker-compose.yml) — por eso HOME=/tmp (tmpfs) y por eso gunicorn
#     se arranca con --no-control-socket (gunicorn 25+ crea un socket de
#     control en $HOME por defecto, y en un filesystem de solo lectura
#     eso falla si no se desactiva).
#   - WHITENOISE_MANIFEST_STRICT se deja en False en config/settings.py
#     (si no, /admin/ puede devolver 500 con ciertos temas).
#   - Nada de CDN: los únicos archivos estáticos hoy son los que trae
#     Django/WhiteNoise — no hay fuentes ni JS de terceros que traer.
#
# psycopg[binary], pandas, numpy y lxml traen sus propias librerías C en
# el wheel manylinux — no se necesita instalar nada vía apt para que
# corran, así que la imagen no lleva apt-get.

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    DJANGO_SETTINGS_MODULE=config.settings

WORKDIR /app

# Dependencias primero (capa cacheable): solo se reinstalan si cambia
# requirements.txt, no en cada cambio de código.
COPY requirements.txt .
RUN pip install --require-hashes -r requirements.txt

COPY . .

# Usuario de sistema sin privilegios — nunca corre como root. Esto, junto
# con cap_drop: [ALL] y no-new-privileges:true en docker-compose.yml, es
# lo que de verdad limita qué puede hacer el contenedor si algo sale mal.
#
# /app/media y /app/salidas se crean y se dejan de su dueño ACÁ (antes de
# montar los volúmenes nombrados en docker-compose.yml): Docker copia el
# contenido y los permisos de esta carpeta hacia el volumen vacío la
# primera vez que se crea, así que el usuario jpltag ya puede escribir
# ahí sin pasos adicionales.
RUN useradd --system --no-create-home --uid 10001 jpltag \
    && mkdir -p /app/media /app/salidas /app/staticfiles \
    && DJANGO_SECRET_KEY=solo-para-construir-la-imagen \
       python manage.py collectstatic --noinput \
    && chown -R jpltag:jpltag /app/media /app/salidas /app/staticfiles

USER jpltag

EXPOSE 8000

# El script de despliegue del servidor (13-app-jpltag.sh) espera a que el
# contenedor quede "healthy" antes de tocar HAProxy/DNS. En producción el
# chequeo real vive en el compose.yaml que genera ese script (en Python,
# sin curl); este HEALTHCHECK queda para `docker run`/el compose de
# desarrollo de este repo. /ingresar/ no requiere sesión y responde 200
# sin tocar la base de datos en el camino crítico.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ingresar/', timeout=3)" || exit 1

# Forma shell (no exec) a propósito: así ${GUNICORN_WORKERS} se puede
# ajustar desde el .env del servidor sin reconstruir la imagen (mismo
# mecanismo que usa MaiReservas para crecer sin un despliegue nuevo).
CMD gunicorn config.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers "${GUNICORN_WORKERS:-3}" \
    --no-control-socket
