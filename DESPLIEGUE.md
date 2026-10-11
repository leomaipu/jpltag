# Despliegue de jpltag en maiproject.cl

Sigue el mismo procedimiento ya en uso para publicar una app Django nueva
en el servidor (el mismo usado para Soporte Existencias y MaiReservas):
un script numerado, idempotente, que arma la imagen, migra la base,
conecta HAProxy/DNS y deja un informe de pruebas — no un `docker compose
build/up` manual. Ver `11-app-jpltag.sh` (entregado aparte, no vive en
este repositorio — ver la sección 0 de abajo).

**Importante:** este repositorio es público. Ningún paso de abajo escribe
un secreto real dentro del repo — todos van a `/opt/maiproject/...` en el
servidor, que nunca se sube a git.

## 0. El script de despliegue no vive en este repositorio

`11-app-jpltag.sh` es un script de infraestructura del servidor, igual
que `09-app-soporte.sh` y `10-app-reservas.sh`: vive en la misma carpeta
que `00-config.sh`, junto a los scripts de las otras apps — no dentro del
repositorio de cada app. Se entrega por separado (ver el archivo adjunto
a esta conversación) para que el equipo lo revise, lo pruebe contra una
versión real de jpltag y lo lleve a esa carpeta.

Qué hace, en orden (igual que sus hermanos):
1. Verifica que PostgreSQL, PgBouncer y Valkey estén sanos.
2. Extrae el código del `.tar.gz` y hace un par de controles mínimos
   (que la clave salga de una variable de entorno, no escrita en el
   código; que no haya recursos cargados desde un CDN en las plantillas).
3. Reutiliza — o crea, si faltara — la base `jpltag` y lee
   `DATABASE_URL`/`VALKEY_URL` desde `/opt/maiproject/secretos/apps/jpltag.env`.
4. Escribe `/opt/maiproject/apps/jpltag/secretos/app.env` (600) con las
   variables que `config/settings.py` realmente lee (ver sección 1).
5. Construye la imagen y genera `/opt/maiproject/apps/jpltag/compose.yaml`
   (ese archivo, no el `docker-compose.yml` de este repo, es el que se usa
   en producción — ver sección 2).
6. Migra, corre una prueba de humo contra el esquema, y levanta el
   contenedor (espera a que quede "healthy").
7. Crea el superusuario de Django inicial si no existe (credenciales en
   un archivo 600, nunca impresas en pantalla).
8. Conecta el subdominio en DNS (firmado) y en HAProxy (`/admin/` solo
   desde las IPs autorizadas, igual que en Soporte).
9. Corre una batería de pruebas (CSP, cookies, solo-lectura, Valkey, etc.)
   y deja el resultado en pantalla.

Uso en el servidor, junto a los demás scripts:

```bash
# en el repositorio, generar el tarball de la versión a publicar
git archive --format=tar.gz --prefix=jpltag/ -o jpltag-$(git rev-parse --short HEAD).tar.gz HEAD

# copiarlo junto al script, a la misma carpeta que 00-config.sh/09-app-soporte.sh
scp 11-app-jpltag.sh jpltag-<commit>.tar.gz maiproject:<carpeta-de-los-scripts>/

# en el servidor
cd <carpeta-de-los-scripts> && sudo bash 11-app-jpltag.sh 2>&1 | tee resultado-11.txt
```

Para publicar una versión nueva más adelante, se repite exactamente lo
mismo con un `.tar.gz` nuevo — es idempotente, igual que sus hermanos.

## 1. Variables que de verdad lee `config/settings.py`

Solo seis, todas con el prefijo `DJANGO_` salvo las dos que ya genera
`maipu-nueva-bd`:

```
DJANGO_SECRET_KEY        # generada una sola vez por el script (64 bytes al azar)
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=jpltag.maiproject.cl,localhost,127.0.0.1
DATABASE_URL             # la genera maipu-nueva-bd jpltag (PostgreSQL vía PgBouncer)
VALKEY_URL               # la genera maipu-nueva-bd jpltag (caché y sesiones)
GLITCHTIP_DSN            # opcional — vacío hasta crear el proyecto en GlitchTip
```

`jpltag.env` (creado con `sudo maipu-nueva-bd jpltag`, ya ejecutado) trae
además `VALKEY_COLAS_URL` — jpltag no usa colas/Celery en la Fase 1, así
que esa variable queda sin usar a propósito; no hace falta borrarla.

`CSRF_TRUSTED_ORIGINS` no es una variable aparte: `settings.py` la
calcula de `DJANGO_ALLOWED_HOSTS` directamente.

GlitchTip: crear el proyecto `jpltag` en `errores.maiproject.cl`, dejar el
DSN en `/opt/maiproject/apps/jpltag/secretos/sentry_dsn` (600) y volver a
ejecutar el script — lo recoge solo.

## 2. Qué deja publicado el script

- Un contenedor (`maipu-jpltag`), solo en `127.0.0.1:8603` — ajustar el
  puerto en el script si ya está en uso por otra app.
- Dos carpetas persistentes **fuera** de la imagen, en el host:
  `/maipu/data/files/jpltag/media` (logos/sello/firma de cada Juzgado) y
  `/maipu/data/files/jpltag/salidas` (cartas, Excel de seguimiento y logs
  de cada corrida — con RUT, domicilio y nombre del infractor). Ninguna
  de las dos se sirve por HTTP, ni siquiera a las IPs autorizadas: la app
  solo las entrega por su propia vista protegida de descarga, o las lee
  directamente del disco para armar los `.docx` (ver `procesos/views.py`
  y `motor/cartas.py`). El panel de `/admin/` para editar el logo/sello/
  firma de un Juzgado queda sin la vista previa de la imagen (el enlace a
  `MEDIA_URL` no responde): es una limitación conocida de la Fase 1, no un
  error — la forma de confirmar que un logo/sello/firma quedó bien es
  generar una carta de prueba y abrir el `.docx`.
- `/admin/` restringido a las IPs de `/etc/haproxy/ips-admin.lst`, igual
  que Soporte Existencias (jpltag no necesita la "marcha blanca" de
  MaiReservas: el login ya protege toda la app, no hay datos de vecinos
  simulados).
- `GUNICORN_WORKERS` ajustable sin reconstruir la imagen (por defecto 3):
  `sudo GUNICORN_WORKERS=5 bash 11-app-jpltag.sh`.

## 3. Primer ingreso

1. `sudo cat /opt/maiproject/apps/jpltag/secretos/credenciales-admin.txt`
   — usuario y clave del superusuario de Django.
2. Entrar a `https://jpltag.maiproject.cl/admin/` (desde una IP
   autorizada) con esa cuenta.
3. Crear los 3 `Juzgado` (slug, nombre, rango de días, UTM vigente) y,
   para la cuenta recién creada, un `PerfilUsuario` con rol
   Administrador. Sin este paso la cuenta no puede entrar a `/` — el
   superusuario de Django por sí solo no basta, la app exige el perfil.
4. Crear un `PerfilUsuario` por cada funcionario real, con su rol y su
   Juzgado.
5. Confirmar el flujo completo: subir una nómina de prueba → generar →
   descargar, y que el `.docx` tenga el contenido esperado.

## 4. Backups

El dump de PostgreSQL ya lo cubre el backup general del servidor. Lo que
hay que agregar a esa rutina son las dos carpetas del host:
`/maipu/data/files/jpltag/media` y `/maipu/data/files/jpltag/salidas` —
igual que ya se hace con el `media/` de MaiReservas.

## 5. Desarrollo local (no es parte del despliegue)

`docker-compose.yml`, en este repositorio, es solo para desarrollo:
`docker compose up --build` levanta jpltag junto a un Postgres y un
Valkey propios, sin tocar nada del servidor. El `compose.yaml` que de
verdad corre en producción lo genera `11-app-jpltag.sh` (sección 4 de
ese script), con los secretos reales y las carpetas de datos del host —
son archivos distintos, a propósito.

## 6. Nota honesta sobre lo validado antes de tener acceso al servidor

Antes de conocer el script real de despliegue, se simuló cada paso del
Dockerfile fuera de Docker (el entorno donde se escribió este repo
bloquea los registries de contenedores — Docker Hub, GHCR, Microsoft
Container Registry y Amazon ECR Public, los cuatro con 403 de política,
confirmado explícitamente, no transitorio), con el mismo intérprete y
las mismas dependencias, con un usuario de sistema sin privilegios y el
árbol de la app en modo de solo lectura real, para reproducir las mismas
restricciones que impone `docker-compose`/el script del servidor:

- `collectstatic` (igual que en el build) — sin errores, gracias a
  `WHITENOISE_MANIFEST_STRICT = False`.
- `gunicorn ... --no-control-socket` arranca limpio y confirma
  "Control socket disabled" — la lección del socket en `$HOME` está
  resuelta. También se confirmó que la forma shell del `CMD` (necesaria
  para que `GUNICORN_WORKERS` se pueda ajustar sin reconstruir la
  imagen) arranca igual de bien.
- Con el código fuente en solo lectura y corriendo como un usuario sin
  privilegios (no root), el flujo real completo — login → subir
  nómina → generar cartas → ver resultado → descargar — funcionó de
  punta a punta, y el `.docx` descargado se abrió con `python-docx` y
  tenía el contenido esperado (las 2 infracciones de la patente de
  prueba agrupadas en 1 sola carta). Esto confirma que `media/` y
  `salidas/` como únicos directorios escribibles es suficiente — la app
  no necesita escribir en ningún otro lado en tiempo de ejecución.
- Un hallazgo real de esta prueba: con más de 1 worker de gunicorn y
  sin Valkey (`CACHES` cae a una caché en memoria, una por proceso), la
  sesión de un usuario se "pierde" según a qué worker caiga cada
  request — cada uno tiene su propia caché. **No es un bug de la app**:
  es la razón por la que `SESSION_ENGINE` depende de Valkey (un único
  store compartido entre los workers de gunicorn) y por la que el
  fallback a caché en memoria de `config/settings.py` es explícitamente
  solo para desarrollo de un solo proceso. En producción, con
  `VALKEY_URL` siempre presente (ya está en
  `/opt/maiproject/secretos/apps/jpltag.env`), esto no aplica — pero si
  alguna vez el contenedor arranca sin esa variable, la señal va a ser
  exactamente esta: usuarios que aparentemente se desloguean solos a
  mitad de una sesión. El paso 8 de `11-app-jpltag.sh` lo verifica
  directamente (escribe y lee una clave en la caché) para no depender
  de notarlo recién en producción.

Lo único que falta probar es el `docker build` mismo (bajar la imagen
base y armar las capas) y el script `11-app-jpltag.sh` completo contra
el servidor real — eso ya depende del equipo, no de este entorno de
desarrollo.
