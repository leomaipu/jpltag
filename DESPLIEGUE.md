# Despliegue de jpltag en maiproject.cl

Sigue el mismo procedimiento ya en uso para publicar una app Django nueva
en el servidor (el mismo usado para Soporte Existencias y MaiReservas):
un script numerado, idempotente, que arma la imagen, migra la base,
conecta HAProxy/DNS y deja un informe de pruebas — no un `docker compose
build/up` manual. Ver `13-app-jpltag.sh` (entregado aparte, no vive en
este repositorio — ver la sección 0 de abajo). El número definitivo es
13, no 11: en `~/maiproject-scripts` el 11 y el 12 ya estaban tomados
(ajustes de seguridad y correo).

**Importante:** este repositorio es público. Ningún paso de abajo escribe
un secreto real dentro del repo — todos van a `/opt/maiproject/...` en el
servidor, que nunca se sube a git.

**Antes de generar el `.tar.gz` de cada versión**, correr las dos suites
de pruebas (ver README.md § Pruebas):

```bash
python -m unittest discover -s motor/tests -t .
python manage.py test
```

La segunda incluye `procesos/tests/`: login, permisos por rol, el flujo
completo subir → generar → descargar, y que un usuario de un Juzgado no
pueda ver ni descargar los resultados de otro. Si algo de esto falla, no
generar el tarball — corregirlo primero.

Desde la versión final de `13-app-jpltag.sh` (la que incorpora
`JPLTAG_PRUEBAS`, ver sección 0, paso 5bis) el propio script vuelve a
correr `python manage.py test --noinput` dentro de la imagen recién
construida, antes de publicar nada — así que un tarball con pruebas
rotas no llega a reemplazar la versión publicada aunque alguien se
salte este paso manual. Correrlas aquí primero sigue siendo más rápido
(no hay que esperar a construir la imagen para enterarse).

**Ojo:** correr `manage.py test` en el entorno de desarrollo local NO
reemplaza construir la imagen. Ya pasó una vez (11-oct-2026): faltaban
`Pillow` y `redis` en `requirements.in`, y las pruebas pasaron igual en
desarrollo porque esos paquetes ya estaban instalados ahí por otra vía
— `JPLTAG_PRUEBAS` los detectó porque instala en la imagen estrictamente
lo que dice `requirements.txt`, nada más. Para repetir esa misma
garantía antes de hacer commit, sin depender de poder construir la
imagen de Docker (bloqueada en este entorno de desarrollo — ver sección
6), conviene instalar `requirements.txt` en un entorno virtual limpio
(`python -m venv` nuevo, sin nada más instalado) y correr ahí
`manage.py check`, `manage.py migrate` y las pruebas — eso sí habría
mostrado el `ImageFieldException`/`ModuleNotFoundError` que la imagen
mostró.

## 0. El script de despliegue no vive en este repositorio

`13-app-jpltag.sh` es un script de infraestructura del servidor, igual
que `09-app-soporte.sh` y `10-app-reservas.sh`: vive en `~/maiproject-scripts`,
junto a `00-config.sh` y los scripts de las otras apps — no dentro del
repositorio de cada app. Lo revisó y corrigió el equipo que administra
el servidor en tres rondas (sintaxis, shellcheck, `haproxy -c`,
idempotencia del bloque de HAProxy, la excepción de 50 MB y, en la
última ronda, la puerta de calidad `JPLTAG_PRUEBAS` del paso 5bis); esta
es la versión confirmada como la que se va a desplegar en el servidor —
falta correrla contra el servidor real, lo demás ya quedó validado
contra este código.

Qué hace, en orden:
1. Verifica que PostgreSQL, PgBouncer y Valkey estén sanos, y que el
   puerto elegido (`JPLTAG_PUERTO`, 8603 por defecto — siguiente
   correlativo después de 8601/8602) no lo esté usando ya otro
   contenedor, servicio o backend de HAProxy.
2. Extrae el código del `.tar.gz` y hace un par de controles mínimos:
   que la clave salga de una variable de entorno (no escrita en el
   código), que el `Dockerfile` fije un `USER` sin privilegios, que no
   haya recursos cargados desde un CDN en las plantillas, y avisa si
   falta `SECURE_PROXY_SSL_HEADER`, django-axes o `CONN_MAX_AGE` en
   `settings.py` (los tres ya están — ver sección 1, ningún aviso
   debería aparecer).
3. Reutiliza — o crea, si faltara — la base `jpltag` y lee
   `DATABASE_URL`/`VALKEY_URL` desde `/opt/maiproject/secretos/apps/jpltag.env`.
4. Escribe `/opt/maiproject/apps/jpltag/secretos/app.env` (600) con las
   variables que `config/settings.py` realmente lee, más `TZ` y las
   variables de la excepción de subidas grandes (ver sección 1). Si más
   adelante se agrega `jpltag` a `CORREO_APPS` en `12-correo.sh`, el
   script incluye también ese archivo de SMTP — jpltag no envía correo
   todavía, así que por ahora no tiene efecto.
5. Construye la imagen y confirma que no corre como root.
5bis. **Puerta de calidad (`JPLTAG_PRUEBAS`, por defecto `si`):** antes
   de generar nada más, corre `python manage.py test --noinput` (la
   suite completa: `motor/` + `procesos/tests/`) dentro de la imagen
   recién construida, aislada de verdad — `--network none`,
   `--cap-drop ALL`, `--security-opt no-new-privileges:true`, con
   `media/`, `salidas/` y `tmp/` en `tmpfs` y una `DJANGO_SECRET_KEY`
   de pruebas, nunca la real. Si algo falla: imprime las últimas 25
   líneas del log, borra la imagen recién construida y el script
   termina ahí — no toca el contenedor que ya está publicado ni genera
   `compose.yaml`. Para saltarla (solo en una emergencia, por ejemplo
   si el servidor no tiene Docker con soporte de `--network none`):
   `sudo JPLTAG_PRUEBAS=no bash 13-app-jpltag.sh`. Depende directamente
   de que `procesos/tests/` exista y pase — ver README.md § Pruebas; en
   particular, de la prueba que confirma que un usuario de un Juzgado
   recibe 403 (no 404) al intentar ver o descargar resultados de otro.
6. Genera `/opt/maiproject/apps/jpltag/compose.yaml` (ese archivo, no el
   `docker-compose.yml` de este repo, es el que se usa en producción —
   ver sección 2). La red `maipu-apps` se declara externa (la creó
   Soporte) y se crea si no existiera.
7. Migra, corre una prueba de humo contra el esquema, y levanta el
   contenedor (espera a que quede "healthy" — el chequeo vive en el
   `compose.yaml` generado, en Python puro, sin depender de `curl`).
8. Crea el superusuario de Django inicial si no existe (credenciales en
   un archivo 600, nunca impresas en pantalla).
9. Conecta el subdominio en DNS (firmado) y en HAProxy: por defecto todo
   el sitio queda restringido a las IPs autorizadas
   (`JPLTAG_SOLO_IPS=si` — jpltag maneja RUT y domicilios, igual criterio
   que la marcha blanca de MaiReservas, aunque aquí no es temporal sino
   la política por defecto). Para abrirlo a Internet con login,
   `JPLTAG_SOLO_IPS=no`. Además rechaza subidas sobre 50 MB (excepción
   propia de jpltag — el resto de las apps sigue en 6–10 MB) y agrega
   una sala de espera, igual que en Reservas.
10. Corre una batería de pruebas (solo-lectura, Valkey, que `/salidas/`
   no responda por HTTP, que las subidas grandes vayan a disco, etc.).
   Con el sitio restringido (`JPLTAG_SOLO_IPS=si`, el default), la
   prueba de login se hace desde dentro del contenedor —desde el propio
   servidor, por HAProxy, daría 403, como se espera— y se omiten las
   pruebas que solo tienen sentido con el sitio abierto (CSP por HTTP,
   cookie CSRF, etc.); esas quedan para cuando se corra con
   `JPLTAG_SOLO_IPS=no`, o para probarlas directamente dentro del
   contenedor.

Uso en el servidor, junto a los demás scripts:

```bash
# en el repositorio, generar el tarball de la versión a publicar
git archive --format=tar.gz --prefix=jpltag/ -o jpltag-$(git rev-parse --short HEAD).tar.gz HEAD

# copiarlo junto al script, a ~/maiproject-scripts
scp 13-app-jpltag.sh jpltag-<commit>.tar.gz maiproject:~/maiproject-scripts/

# en el servidor
cd ~/maiproject-scripts && sudo bash 13-app-jpltag.sh 2>&1 | tee resultado-13.txt
```

Para publicar una versión nueva más adelante, se repite exactamente lo
mismo con un `.tar.gz` nuevo — es idempotente, igual que sus hermanos.

**Política de acceso confirmada con Leonardo (11-oct-2026):** el sitio
queda restringido (`JPLTAG_SOLO_IPS=si`, el default) a la red municipal
más la IP de la casa de Leonardo — no se abre a Internet
(`JPLTAG_SOLO_IPS=no`) ni se agrega ninguna otra IP de teletrabajo por
ahora. En la práctica: la red municipal ya debería estar en
`/etc/haproxy/ips-admin.lst` (la usan las otras apps); falta agregar ahí
la IP de la casa de Leonardo — si es una IP residencial sin contrato fijo,
puede cambiar con el tiempo, así que conviene que el equipo de
infraestructura confirme si es fija o si hay que revisarla más adelante.
Si algún día algún funcionario de los 3 Juzgados necesita entrar desde
otro lugar (teletrabajo), esto hay que revisarlo de nuevo — no está
cubierto por esta decisión.

## 1. Variables que de verdad lee `config/settings.py`

```
DJANGO_SECRET_KEY            # generada una sola vez por el script (64 bytes al azar)
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=jpltag.maiproject.cl,localhost,127.0.0.1
DATABASE_URL                 # la genera maipu-nueva-bd jpltag (PostgreSQL vía PgBouncer)
VALKEY_URL                   # la genera maipu-nueva-bd jpltag (caché y sesiones)
GLITCHTIP_DSN                # opcional — vacío hasta crear el proyecto en GlitchTip
JPLTAG_MAX_CARTAS_POR_ARCHIVO # opcional — tope de cartas por archivo Word (50)
JPLTAG_LOG_NIVEL             # opcional — INFO por defecto
JPLTAG_AGENDA_ACTIVA         # opcional — False: las cartas no usan la agenda (todas «a coordinar»)
```

`JPLTAG_GENERAR_EN_LA_PETICION` existe solo para pruebas y desarrollo sin
worker: en el servidor nunca se define (vale False).

`settings.py` también sabe leer `DJANGO_CSRF_TRUSTED_ORIGINS` si alguna
vez se necesita fijarla a mano (igual que Soporte), pero el script ya no
la escribe: la quitaron porque, sin ella, se calcula sola a partir de
`DJANGO_ALLOWED_HOSTS` — exactamente el mismo valor.

Además del `.env` de Django, el script ajusta tres cosas por fuera de
los *settings* para que la excepción de subidas de hasta 50 MB
funcione de punta a punta (nada de esto requirió cambiar código —
Django y gunicorn ya respetan estos mecanismos):

```
TMPDIR=/app/tmp                     # tempfile.gettempdir() la respeta: las subidas
                                     # grandes se escriben en disco, no en RAM
GUNICORN_CMD_ARGS=--timeout 180     # gunicorn la lee solo; subir+procesar un Excel
                                     # de 50 MB puede pasar los 30 s por defecto
TZ=America/Santiago                 # solo afecta la hora de los logs del contenedor;
                                     # TIME_ZONE sigue fijo en settings.py
```

Lo que sí hace falta para que `TIME_ZONE = "America/Santiago"` (fijo en
`settings.py`) funcione es el paquete `tzdata` de PyPI (ya en
`requirements.in`): `python:3.13-slim` no trae el paquete de sistema del
mismo nombre, y el Dockerfile no usa `apt-get` — sin él, la primera
fecha con huso horario habría fallado con `ZoneInfoNotFoundError`.
Verificado simulando un contenedor sin tzdata de sistema.

`jpltag.env` (creado con `sudo maipu-nueva-bd jpltag`, ya ejecutado) trae
además `VALKEY_COLAS_URL` — jpltag no usa Celery: su cola de generaciones
vive en PostgreSQL (`GeneracionCartas` en estado «En cola», que el worker
toma con `SELECT … FOR UPDATE SKIP LOCKED`), así que esa variable queda
sin usar a propósito; no hace falta borrarla.

GlitchTip: crear el proyecto `jpltag` en `errores.maiproject.cl`, dejar el
DSN en `/opt/maiproject/apps/jpltag/secretos/sentry_dsn` (600) y volver a
ejecutar el script — lo recoge solo. `config/settings.py` ya configura el
SDK (11-oct-2026) con `include_local_variables=False` y
`max_request_body_size="never"`: por defecto el SDK de Sentry adjunta a
cada error las variables locales del stack trace y parte del cuerpo de la
request, y en esta app eso puede ser el RUT, domicilio o nombre de un
infractor (una fila de la nómina, el contexto de una carta, etc. — ver el
comentario junto a `sentry_sdk.init()`). GlitchTip puede quedar visible
para gente sin acceso a ningún Juzgado (quien administra
`errores.maiproject.cl` para las 3 apps), así que esos datos no deben
llegar ahí — un error en GlitchTip debe alcanzar para saber dónde y qué
tipo de excepción ocurrió, nunca para leer datos de un infractor. No
probado contra una instancia de GlitchTip real desde este entorno de
desarrollo (sin salida a `errores.maiproject.cl`, misma limitación de red
que Docker Hub y mindicador.cl); sí se confirmó que `sentry_sdk.init()`
acepta estas opciones y no bloquea el arranque con un DSN de prueba.

## 2. Qué deja publicado el script

- Un contenedor web (`maipu-jpltag`), solo en `127.0.0.1:$JPLTAG_PUERTO`
  (8603 si está libre; el script se detiene si ya lo usa otro
  contenedor, servicio o backend de HAProxy, y se puede fijar a mano con
  `JPLTAG_PUERTO=86xx`).
- Un worker (`maipu-jpltag-worker`), la misma imagen con
  `python manage.py procesar_generaciones`, sin puertos: arma las cartas
  de las generaciones en cola (`procesos/generacion.py`). La web solo lee
  la nómina (~20–30 s con la del mes completo), reserva la agenda y deja
  la corrida «En cola»; la pantalla de resultado muestra el avance
  (paquete N de M) y se recarga sola. Cada paquete se arma en un proceso
  hijo, para que la memoria no crezca con miles de cartas: 1 GB y 1 vCPU
  (`JPLTAG_WORKER_MEM`, `JPLTAG_WORKER_CPUS`). Basta con uno; si por
  error arrancan dos no se pisan (cada corrida queda a nombre del que la
  tomó). Con `docker stop` (despliegue, apagado de la VM a las 23 h)
  termina el paquete en curso y devuelve la corrida a la cola; al volver
  sigue desde el último paquete terminado. Si muere de golpe, otro worker
  la retoma cuando su latido tiene más de 7 min. Un paquete que no termina
  en 2 min se corta y queda como fallido, sin detener la cola. Una
  generación en cola se puede cancelar desde su pantalla (sus cupos
  vuelven a la agenda). Cada hora borra las nóminas guardadas que nadie
  confirmó (aviso de patentes repetidas) después de 12 h; la copia de
  trabajo (`trabajo.json`) se borra al cerrar cada corrida.
- Tres carpetas persistentes **fuera** de la imagen, en el host:
  `/maipu/data/files/jpltag/media` (logo/sello/firma y las plantillas Word
  propias de cada Juzgado — Módulos 1 y 2, ver README.md),
  `/maipu/data/files/jpltag/salidas` (cartas, Excel de seguimiento y logs
  de cada corrida — con RUT, domicilio y nombre del infractor) y
  `/maipu/data/files/jpltag/tmp` (archivos subidos grandes mientras se
  procesan). Ninguna se sirve por HTTP, ni siquiera a las IPs
  autorizadas: la app solo entrega `salidas/` por su propia vista
  protegida de descarga, y lee `media/` directamente del disco para
  armar los `.docx` (ver `procesos/views.py` y `motor/plantilla.py`) y
  para la vista previa de logo/sello/firma en `/plantilla/` — esa
  vista previa propia (no depende de `MEDIA_URL`, que no se sirve por
  HTTP) reemplazó la limitación que tenía `/admin/` en la Fase 1.
- Todo el sitio restringido a las IPs de `/etc/haproxy/ips-admin.lst`
  por defecto (`JPLTAG_SOLO_IPS=si`), más una sala de espera y el
  rechazo de subidas sobre 50 MB en HAProxy (excepción solo para
  jpltag — el resto de las apps sigue en 6–10 MB) — ver la nota
  pendiente de confirmar en la sección 0.
- Para que esos 50 MB funcionen de verdad y no solo en HAProxy: 1 GB de
  memoria para el contenedor (pandas leyendo un Excel grande puede usar
  varios cientos de MB — conviene mirar el uso real la primera vez en
  Dozzle, la VM tiene 3,8 GB en total), 180 s de tiempo máximo en
  gunicorn y 190 s en HAProxy hacia jpltag (los 30/60 s por defecto
  cortarían a mitad de un Excel grande), y las subidas a disco
  (`TMPDIR=/app/tmp`) en vez de RAM.
- Los estáticos de la interfaz (CSS, JS, fuente Rubik, logo y escudo en SVG)
  salen de la propia app vía WhiteNoise, con la CSP `'self'` de
  `config/settings.py`: el reverse proxy no necesita servir `/static/` ni
  habilitar ningún dominio externo. `collectstatic` ya corre al construir la
  imagen (`Dockerfile`); si en el servidor la pantalla se ve sin estilos,
  revisar primero que esa etapa no falló.
- Este despliegue aplica migraciones nuevas en la agenda (`nucleo` 0005–0006,
  `procesos` 0003–0004). La 0005 acota a su total cualquier bloque que tenga
  más cupos ocupados que totales (deja un aviso en el log con los ids) antes
  de que la 0006 agregue la restricción. Una generación «En curso» sin
  latido del worker por más de 7 minutos la retoma el worker; si el
  worker no vuelve, se cancela desde su pantalla o desde la del bloque
  («Devolver los cupos de esa generación»).
- `/admin/` solo abre a superusuarios (`config/urls.py`): no aísla por
  juzgado.
- `GUNICORN_WORKERS` ajustable sin reconstruir la imagen (por defecto 3):
  `sudo GUNICORN_WORKERS=5 bash 13-app-jpltag.sh`.

## 3. Primer ingreso

1. `sudo cat /opt/maiproject/apps/jpltag/secretos/credenciales-admin.txt`
   — usuario y clave del superusuario de Django.
2. Entrar a `https://jpltag.maiproject.cl/admin/` (desde una IP
   autorizada) con esa cuenta.
3. Crear los 3 `Juzgado` (slug, nombre, rango de días, UTM vigente) y,
   para la cuenta recién creada, un `PerfilUsuario` con rol
   Administrador. Sin este paso la cuenta no puede entrar a `/` — el
   superusuario de Django por sí solo no basta, la app exige el perfil.
   **El slug de cada Juzgado debe ser `jpl1`, `jpl2` o `jpl3`**: con eso
   la app elige las plantillas de carta de cada uno (ver README.md); con
   otro slug cuyo nombre no diga "Primer/Segundo/Tercer", no hay plantilla
   por defecto y la generación falla pidiendo subir una.
4. Crear un `PerfilUsuario` por cada funcionario real, con su rol y su
   Juzgado.
5. Para el 2do Juzgado: en `/plantilla/` subir la **firma** de la
   secretaria abogada y el **timbre (sello)** del secretario — no vienen
   en el repo. Sin ellos, las cartas salen con una línea punteada.
6. Confirmar el flujo completo, con cada uno de los 3 tipos de carta:
   subir una nómina de prueba → generar → descargar, y que el `.docx`
   tenga el contenido esperado. (La sentencia pide la fecha de la
   sentencia y no usa la agenda.)

Al actualizar desde la versión anterior, `migrate` hace dos cosas con los
datos existentes: la plantilla Word que un Juzgado ya hubiera subido pasa
a ser su plantilla de **citación**, y `max_causas_por_pagina` que siga en
20 (el valor del diseño anterior) baja a 12 — con las plantillas oficiales
no caben más por hoja; cualquier otro valor no se toca.

## 4. Backups

El dump de PostgreSQL ya lo cubre el backup general del servidor. Lo que
hay que agregar a esa rutina son `/maipu/data/files/jpltag/media` y
`/maipu/data/files/jpltag/salidas` — igual que ya se hace con el
`media/` de MaiReservas. `/maipu/data/files/jpltag/tmp` NO necesita
backup: son archivos subidos a medio procesar, se pueden borrar sin
pérdida (la app no depende de que sobrevivan a un reinicio).

## 5. Desarrollo local (no es parte del despliegue)

`docker-compose.yml`, en este repositorio, es solo para desarrollo:
`docker compose up --build` levanta jpltag junto a un Postgres y un
Valkey propios, sin tocar nada del servidor. El `compose.yaml` que de
verdad corre en producción lo genera `13-app-jpltag.sh`, con los
secretos reales y las carpetas de datos del host — son archivos
distintos, a propósito.

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
  mitad de una sesión. El paso 9 de `13-app-jpltag.sh` lo verifica
  directamente (escribe y lee una clave en la caché) para no depender
  de notarlo recién en producción.

Ya con el script `13-app-jpltag.sh` en su versión final (la de la
excepción de 50 MB), se verificaron dos cosas puntuales contra este
código, sin necesidad de Docker:

- `GET /ingresar/` responde 200 con `DJANGO_DEBUG=False` (el healthcheck
  del `compose.yaml` que genera el script, que apunta justo a esa URL,
  funciona).
- Una subida multipart de 10 MB a `/subir/` (con sesión real, por
  `/ingresar/`, no `client.login()`) llega completa a la vista: Django
  no la corta con `RequestDataTooBig` ni 413 antes de eso —
  `DATA_UPLOAD_MAX_MEMORY_SIZE` solo limita los campos que no son
  archivo del POST, no el contenido de `request.FILES`. No hizo falta
  tocar ningún *setting* de Django para que la excepción de 50 MB de
  HAProxy funcione de punta a punta; solo el `TMPDIR`/`GUNICORN_CMD_ARGS`
  que ya pone el script.

Lo único que falta probar es el `docker build` mismo (bajar la imagen
base y armar las capas) y el script `13-app-jpltag.sh` completo contra
el servidor real — eso ya depende del equipo, no de este entorno de
desarrollo (se reintentó explícitamente, `docker build` sigue recibiendo
403 de Docker Hub — bloqueo de política, no transitorio).

**Hallazgo real de `JPLTAG_PRUEBAS` contra el servidor (11-oct-2026):**
la primera corrida de la puerta de calidad frenó la publicación —
`Pillow` y `redis` faltaban en `requirements.in`. `Pillow` lo necesita
cualquier `ImageField` (logo/sello/firma de `Juzgado`) para que Django
valide esos campos al migrar; `redis` lo necesita
`django.core.cache.backends.redis.RedisCache` (se activa con
`VALKEY_URL`, ver `config/settings.py`) para conectarse a Valkey. Nada
de lo publicado se tocó: el script aborta antes de migrar o reemplazar
el contenedor. `manage.py test` no lo había detectado porque, sin
`Docker` disponible en este entorno de desarrollo, se venía corriendo
contra el intérprete del sistema, donde `Pillow` ya estaba instalado
por otra vía y las pruebas usan caché en memoria (sin `VALKEY_URL`) —
exactamente el escenario que `JPLTAG_PRUEBAS` existe para atrapar, al
instalar en la imagen estrictamente lo que dice `requirements.txt`. Sin
poder reproducir un `docker build` aquí, se verificó el arreglo con el
equivalente más cercano posible: un entorno virtual nuevo con
`pip install --require-hashes -r requirements.txt` y nada más instalado
— ahí `manage.py check`, `manage.py migrate` (confirma que los
`ImageField` ya no fallan) y la conexión de `RedisCache` contra un
Valkey/Redis real (`cache.set`/`cache.get` de ida y vuelta) funcionan,
y la suite completa (52 pruebas) sigue en verde.

**Módulo 4 (UTM automático, 11-oct-2026) — misma limitación de red, otro
destino:** el botón "Verificar valor UTM" de `/plantilla/` consulta
`https://mindicador.cl/api/utm` (ver `motor/utm.py`; se eligió esa API en
vez de la oficial de la CMF porque esta última exige una API key que no
se tramitó). Igual que con Docker Hub, este entorno de desarrollo
tampoco tiene salida a `mindicador.cl` (`urllib.request.urlopen` contra
esa URL devuelve el mismo 403 de política, no transitorio), así que
**no se pudo probar la consulta real de punta a punta desde aquí**. Las
pruebas (`motor/tests/test_utm.py` y `procesos/tests/test_plantilla.py::ActualizarUTMTest`)
reemplazan esa llamada por una respuesta fabricada. Lo que falta
confirmar contra el servidor real es que el contenedor de producción sí
pueda llegar a `mindicador.cl` por HTTPS (no hay ninguna razón para
pensar que no, a diferencia del bloqueo de Docker Hub que es específico
de este entorno de desarrollo, pero no se verificó); si el botón muestra
"No se pudo consultar el valor UTM vigente" en el servidor, el primer
sospechoso es la salida a internet del contenedor (firewall/proxy), no
el código.

**Hallazgo real de `manage.py check --deploy` contra el servidor
(11-oct-2026):** Leonardo lo corrió dentro del contenedor ya publicado
(`docker exec maipu-jpltag python manage.py check --deploy`) y salieron
dos warnings, `security.W004` (sin `SECURE_HSTS_SECONDS`) y `security.W008`
(`SECURE_SSL_REDIRECT` no es `True`). Los dos se dejan SIN activar en
`config/settings.py` y se silencian a propósito (`SILENCED_SYSTEM_CHECKS`),
no por descuido — es la misma razón para los dos, confirmada con Leonardo
el mismo día: HAProxy ya hace ambas cosas en el borde, de forma idéntica
para las 3 apps del servidor, no solo jpltag: redirige todo el puerto 80 a
HTTPS con 301, y agrega `Strict-Transport-Security` (max-age de 1 año,
`includeSubDomains`) en toda respuesta. Django nunca ve esa conexión
directamente, así que duplicar cualquiera de las dos cosas acá:

- `SECURE_SSL_REDIRECT=True` redirigiría también el tráfico HTTP interno
  (sin `X-Forwarded-Proto: https`) con el que ya se prueba la app dentro
  del propio contenedor — el healthcheck del `compose.yaml` generado por
  `13-app-jpltag.sh`, y la prueba de login cuando `JPLTAG_SOLO_IPS=si` (ver
  §0 y más arriba en esta sección) — rompiendo ambas.
- `SECURE_HSTS_SECONDS` dejaría dos cabeceras `Strict-Transport-Security`
  en la misma respuesta (la de HAProxy y la de Django). Los navegadores
  solo procesan la primera (RFC 6797), así que no rompe nada, pero no
  suma nada tampoco y es un lugar más donde la política podría
  desincronizarse si algún día cambia en HAProxy.

Si alguna vez deja de ser cierto (cambia la configuración de HAProxy, o la
app queda expuesta sin pasar por él), hay que revisar esta decisión.
