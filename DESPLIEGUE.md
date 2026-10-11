# Despliegue de jpltag en maiproject.cl

Sigue el mismo procedimiento ya documentado para publicar una app Django
nueva en el servidor (usado para Soporte Existencias y MaiReservas): BD
dedicada, contenedor Docker con el filesystem de solo lectura, HAProxy
como única puerta de entrada, y reutilizando el PostgreSQL+PgBouncer y
el Valkey que ya existen — sin infraestructura nueva.

**Importante:** este repositorio es público. Ningún paso de abajo escribe
un secreto real dentro del repo — todos van al `.env` del servidor, que
nunca se sube.

## 1. Base de datos y `.env` base

```bash
sudo maipu-nueva-bd jpltag
```

Esto crea el rol de base de datos dedicado y un `.env` en el servidor con:

```
DATABASE_URL=...   # PostgreSQL vía PgBouncer, modo transacción
VALKEY_URL=...     # caché y sesiones
```

jpltag no usa colas/Celery todavía (no hay tareas en segundo plano en la
Fase 1), así que no necesita `VALKEY_COLAS_URL`.

## 2. Completar el `.env`

`maiproject-nueva-bd` no genera estas variables — hay que agregarlas a
mano al mismo `.env`:

```
DJANGO_SECRET_KEY=<clave larga y aleatoria, generada una sola vez>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=jpltag.maiproject.cl
GLITCHTIP_DSN=<DSN del proyecto jpltag en GlitchTip>
```

Una forma simple de generar `DJANGO_SECRET_KEY`:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

## 3. Clonar y construir la imagen

```bash
git clone https://github.com/leomaipu/jpltag.git
cd jpltag
docker compose build
```

El Dockerfile instala las dependencias con `pip install --require-hashes`
desde `requirements.txt` (generado con `pip-compile --generate-hashes`,
verificado con `pip-audit` — ver README.md) y corre `collectstatic`
durante la construcción, antes de bajar a un usuario sin privilegios.

> Esta imagen no se pudo construir en el entorno donde se escribió este
> repositorio (su política de red bloquea `registry-1.docker.io`, de
> donde sale la imagen base `python:3.13-slim`) — si falla algo en el
> primer build real en el servidor, revisar primero eso antes de asumir
> un error del Dockerfile. Todo lo demás (resolución de dependencias con
> hash, `pip-audit`, y la aplicación completa con `manage.py test` y con
> el cliente de pruebas de Django) sí se verificó.

## 4. Levantar el contenedor

```bash
docker compose up -d
docker compose exec jpltag python manage.py migrate
docker compose exec jpltag python manage.py createsuperuser
```

Desde `/admin/` (ver paso 6), crear:
- Los 3 `Juzgado` (slug, nombre, rango de días, UTM vigente).
- Un `PerfilUsuario` por cada cuenta, con su rol y su juzgado (el
  Administrador puede dejarse sin juzgado, para ver los 3).

## 5. HAProxy — subdominio nuevo

Agregar el backend de jpltag siguiendo el mismo patrón que los backends
ya existentes (Soporte Existencias / MaiReservas) — **ejemplo a ajustar
al formato real del `haproxy.cfg` del servidor**, no una copia textual:

```
backend back_jpltag
    server jpltag1 127.0.0.1:8042 check   # mismo puerto que en docker-compose.yml
```

Y agregar la línea de ruteo por subdominio en `/etc/haproxy/hosts.map`:

```
jpltag.maiproject.cl back_jpltag
```

Recargar HAProxy sin cortar conexiones activas (`systemctl reload haproxy`
o el comando que ya se use para los otros backends).

## 6. DNS

`jpltag.maiproject.cl` es un subdominio nuevo — agregar el registro A en
BIND9 apuntando a la IP del servidor (los subdominios ya existentes de
Soporte Existencias/MaiReservas no necesitaron este paso porque ya
estaban creados; este si, por ser la primera vez).

## 7. Verificación

- `https://jpltag.maiproject.cl/ingresar/` carga el formulario de login.
- Entrar con el superusuario, crear un `Juzgado` de prueba y un
  `PerfilUsuario`, y confirmar que el flujo completo (subir una nómina
  de prueba → generar → descargar) funciona de punta a punta.
- Un error disparado a propósito (ej. subir un .xlsx corrupto) debe
  aparecer en GlitchTip, no solo en el log del contenedor.

## 8. Backups

Las cartas generadas y los Excel de seguimiento/duplicados/log de
errores quedan en el volumen `jpltag_salidas`, y los logos/sellos/firmas
de cada Juzgado en `jpltag_media` — agregar ambos a la rutina de backup
ya existente para los volúmenes de las otras apps (la base de datos ya
está cubierta por el backup de PostgreSQL del servidor).
