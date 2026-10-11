# jpltag

Aplicación web única para los 3 Juzgados de Policía Local de Maipú (JPL1,
JPL2, JPL3): reemplaza los 6 scripts de Python que hoy corren por
separado, por terminal, para generar las cartas de notificación/citación
de infracciones TAG (Art. 3° Ley 18.287).

El problema que resuelve: hoy un infractor con más de una multa en el
mes recibe más de una carta (una por cada vez que corre el script). Acá
todas las infracciones de una misma patente en el mes se agrupan en
**una sola carta**, con una tabla que lista cada causa por separado.

Se publica en `jpltag.maiproject.cl` — ver [DESPLIEGUE.md](DESPLIEGUE.md)
para instalarla en el servidor.

## Estructura del proyecto

```
config/     configuración de Django (settings, urls, wsgi)
nucleo/     modelos: Juzgado, BloqueHorario (agenda con cupos),
            PerfilUsuario/Rol (quién puede hacer qué)
motor/      paquete Python plano (NO es una app Django) con la lógica de
            negocio portada fielmente de los scripts originales: lectura
            y mapeo flexible de columnas del Excel, detección de N° de
            parte duplicado, agrupación de infracciones por patente,
            cálculo de multa por tipo de causa, asignación de agenda, y
            la generación de la carta de citación oficial en .docx a
            partir de la plantilla Word de cada Juzgado (motor/plantilla.py)
procesos/   la app web: login, panel, subir nómina → generar cartas,
            ver/descargar resultado, agenda, plantilla y logos del Juzgado
scripts/    scripts que se corren a mano (no en producción) — hoy solo
            el que genera la plantilla Word por defecto, ver abajo
```

`motor/` es deliberadamente independiente de Django: es el mismo motor
que corrían los scripts, solo que parametrizado por juzgado (`config`,
un dict) en vez de constantes fijas por archivo. Se puede probar y usar
sin levantar la aplicación web.

### Plantilla Word editable por Juzgado (Módulo 1) y logos propios (Módulo 2)

Cada Juzgado puede subir su propia plantilla de carta (`/plantilla/` en
la app, o el campo `plantilla_word` de `Juzgado` desde `/admin/`): un
.docx con marcadores estilo Jinja2 (`{{ nombre_infractor }}`, una tabla
que se repite por causa, etc.) que define el diseño completo — logo,
colores, orden de los bloques. La lista completa de marcadores
disponibles está documentada en `motor/plantilla.py`. El texto legal de
la citación (Art. 3° Ley 18.287) no es editable desde la plantilla: lo
arma `motor/plantilla.construir_contexto()` para no poder introducir sin
querer un error de redacción legal con solo editar un Word.

Si un Juzgado no sube su propia plantilla, se usa la que trae el repo
por defecto (`motor/plantillas_defecto/citacion_tag.docx`), generada con
`scripts/construir_plantilla_defecto.py` — para regenerarla tras cambiar
su diseño:

```bash
python scripts/construir_plantilla_defecto.py
```

Antes de guardar una plantilla nueva, la app la prueba con datos
ficticios (`motor.plantilla.validar_plantilla`) para atrapar un marcador
mal escrito (ej. un `{%tr for %}` sin su `{%tr endfor %}`) antes de que
rompa una generación de cartas real. Logo, sello judicial y firma se
suben desde la misma pantalla — se validan como imagen real (Pillow, ver
`requirements.in`) y tienen una vista previa que no depende de que
`MEDIA_URL` se sirva por HTTP (en producción no se sirve, ver
DESPLIEGUE.md).

## Roles y permisos

| Rol | Plantilla | Agenda | Generar cartas | Gestionar usuarios |
|---|---|---|---|---|
| Operador | Editar | Editar | Sí | No |
| Secretario(a) | Editar | Editar | Sí | No |
| Jueza/Juez | Sin acceso | Editar | No | No |
| Administrador | Editar | Editar | Sí | Sí |

Definidos en `nucleo/models.py` (`PERMISOS_POR_ROL`), confirmados con el
Juzgado el 10-oct-2026. Un Administrador sin juzgado asignado ve los 3;
el resto, solo el suyo.

## Desarrollo local

Dos formas, a elegir:

**Con Docker** (no requiere nada instalado salvo Docker):

```bash
docker compose up --build
```

Levanta jpltag junto a un Postgres y un Valkey propios, en
`http://localhost:8000`. Es solo para desarrollo — en producción se usa
el `compose.yaml` que genera el script de despliegue del servidor, no
este archivo (ver DESPLIEGUE.md).

**Sin Docker**, con Python 3.13 y las dependencias de `requirements.txt`
(generadas con `pip-compile --generate-hashes --strip-extras` desde
`requirements.in`):

```bash
pip install --require-hashes -r requirements.txt
python manage.py migrate        # usa SQLite si no hay DATABASE_URL (ver abajo)
python manage.py createsuperuser
python manage.py runserver
```

Sin un `.env`, `config/settings.py` cae a SQLite y a una caché en
memoria — es a propósito, solo para poder desarrollar y correr las
pruebas sin un Postgres/Valkey a mano. En el servidor real, `DATABASE_URL`
y `VALKEY_URL` siempre llegan vía `sudo maipu-nueva-bd jpltag` (ver
DESPLIEGUE.md) y ese fallback nunca se usa.

Después de crear un superusuario, hay que crear al menos un `Juzgado` y
un `PerfilUsuario` (rol Administrador) desde `/admin/` antes de poder
entrar a `/` con esa cuenta.

## Pruebas

El motor de negocio (lo más sensible: duplicados, agrupación, multas,
agenda, fechas en letras) tiene pruebas unitarias independientes de
Django:

```bash
python -m unittest discover -s motor/tests -t .
```

`procesos/tests/` prueba la app web sobre ese motor — login (incluido el
bloqueo de django-axes tras 5 intentos), los permisos de cada rol,
el flujo completo subir → generar → ver resultado → descargar, que una
plantilla propia de un Juzgado se use de verdad al generar sus cartas (y
que una con un marcador mal cerrado se rechace antes de guardarse), y lo
más importante: que un usuario de un Juzgado nunca pueda ver ni
descargar los resultados de otro (ahí está el RUT, el domicilio y el
nombre del infractor). Correr ambas suites (motor + procesos) antes de
cada despliegue:

```bash
python manage.py check
python manage.py test
```

## Dependencias

`requirements.in` lista las dependencias de primer nivel; `requirements.txt`
es el resultado de `pip-compile --generate-hashes --strip-extras` (con
hash de cada paquete, incluidas las dependencias transitivas, mismo
comando que usa Soporte Existencias) y es el que de verdad se instala —
en desarrollo y en la imagen de Docker. Para actualizar una versión:

```bash
pip-compile --generate-hashes --strip-extras --output-file=requirements.txt requirements.in
pip-audit -r requirements.txt
```
