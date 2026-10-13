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
            la generación de las cartas oficiales en .docx a partir de
            la plantilla Word de cada Juzgado y tipo de carta
            (motor/plantilla.py, motor/plantillas_defecto/)
procesos/   la app web: login, panel, subir nómina → generar cartas,
            ver/descargar resultado, agenda, plantilla y logos del Juzgado
```

`motor/` es deliberadamente independiente de Django: es el mismo motor
que corrían los scripts, solo que parametrizado por juzgado (`config`,
un dict) en vez de constantes fijas por archivo. Se puede probar y usar
sin levantar la aplicación web.

### Tres tipos de carta, una plantilla Word por Juzgado y tipo (Módulos 1 y 2)

Cada Juzgado emite tres tipos de carta, que se elige al subir la nómina:

| Tipo | Qué dice | Agenda |
|---|---|---|
| Citación | Cita a prestar declaración en una fecha y hora (Art. 3° Ley 18.287) | Sí: asigna un bloque de `/agenda/` |
| Citación y sentencia | Lo anterior + "si no concurre, será CONDENADO(A) al pago de N UTM por infracción" | Sí |
| Sentencia | Notifica la sentencia ya dictada (Art. 18° Ley 18.287); se indica la fecha de la sentencia al generar | No usa agenda ni gasta cupos |

**El texto legal vive en el Word, no en el código.** Las 9 plantillas
(3 Juzgados × 3 tipos) están en `motor/plantillas_defecto/jpl1/`,
`jpl2/`, `jpl3/` (`citacion.docx`, `citacion_sentencia.docx`,
`sentencia.docx`), adaptadas de los ejemplos que entregó cada Juzgado:
conservan su diseño y su redacción (por ejemplo, la citación del 2do
JPL no dice "a prestar declaración indagatoria"; el 1ro y el 3ro sí).
Se usan según el número del Juzgado (`jpl1`/`jpl2`/`jpl3` en el slug, o
"PRIMER/SEGUNDO/TERCER" en el nombre): **el slug de cada Juzgado debe
ser `jpl1`, `jpl2` o `jpl3`**. Si no se puede deducir el número y el
Juzgado no subió plantilla propia, la generación **falla con un mensaje
claro** en vez de usar el texto legal de otro Juzgado.

Cada Juzgado puede cambiar su texto desde `/plantilla/`: descarga la que
se usa hoy ("Descargar la que se usa hoy"), la edita en Word conservando
los marcadores (`{{ nombre_infractor }}`, `{{ patente }}`, una tabla con
`{%tr for causa in causas %}` … `{%tr endfor %}`, etc.; la lista completa
está en el docstring de `motor/plantilla.py`) y la vuelve a subir (o
desde el campo correspondiente de `Juzgado` en `/admin/`). Antes de
guardarla, la app genera una carta de prueba con datos ficticios y
comprueba que traiga nombre, RUT, patente, tabla de infracciones y la
fecha de la audiencia/sentencia, para atrapar un marcador mal escrito o
un Word sin los datos antes de que arruine una impresión real
(`motor.plantilla.validar_plantilla`).

Logo, sello judicial y firma se suben desde la misma pantalla — se
validan como imagen real (Pillow, ver `requirements.in`) y tienen una
vista previa que no depende de que `MEDIA_URL` se sirva por HTTP (en
producción no se sirve, ver DESPLIEGUE.md). Sin logo propio se usa el
logo municipal que trae el repo (`motor/plantillas_defecto/logo_maipu.png`).
La firma y el sello **no** están en el repo (son de una persona
concreta): para el 2do JPL, la secretaria abogada sube su firma y el
secretario su timbre en `/plantilla/`; mientras no estén cargados, la
carta sale con una línea punteada para firmar a mano. Cargos de quien
firma disponibles: Secretario, Secretaria, Secretario abogado, Secretaria
abogada.

Cuántas infracciones caben en una hoja (`Juzgado.max_causas_por_pagina`,
12 por defecto): se midió con las plantillas por defecto y la nómina real
de octubre-2026 — caben 14 con firma + sello (la más alta) y 16 ya
desborda; si una patente tiene más, la carta sale en varias partes
completas ("Parte 2 de 3: infracciones 13 a 24 de 26"), cada una con su
firma. Con una plantilla propia más alta o más compacta, ajustar desde
`/admin/`.

**Rol:** la nómina que entrega la DAF no trae el Rol/folio de causa (solo
el N° de parte), así que la columna Rol imprime `PENDIENTE` hasta que esa
columna venga en la nómina (`motor/cartas.formatear_rol`). Igual con
`Reg N°` («folio carta trámite»).

### Plantillas con control de cambios y comentarios

Las plantillas Word pueden subirse con el control de cambios activo y con
comentarios de revisión: Word los imprime (cambios en color, globos al margen)
si quedan en el archivo, y las cartas son documentos oficiales. `motor/revisiones.py`
limpia una copia en memoria de la plantilla antes de usarla —tanto al validarla
como al generar cada carta—: acepta todos los cambios (lo insertado queda como
texto normal, lo eliminado desaparece), descarta los registros de formato anterior,
borra los comentarios y la lista de revisores y apaga el seguimiento de cambios.
El archivo guardado en el Juzgado no se modifica.

### Diseño de la interfaz (manual de marca de la Municipalidad)

La interfaz sigue el manual de marca: tipografía **Rubik** (variable, licencia
OFL, autoalojada en `procesos/static/procesos/fonts/`), azul `#10174a`,
turquesa `#33cccc`, magenta `#e60380` y ámbar `#ffb71b`, la barra tricolor del
subrayado del logo, bloques con la esquina superior redondeada, botones
píldora y los tres puntos. El logo y el escudo se extrajeron del manual como
SVG (`procesos/static/procesos/img/`), solo optimizados (svgo, 2 decimales).

- **Mobile first y adaptativa**: teléfono (<720 px) con barra superior y
  navegación inferior; tablet (≥720 px) con la navegación en la barra; escritorio
  (≥960 px) con riel lateral y logo completo. El CSS base es el de teléfono y las
  *media queries* solo agregan.
- **Todo propio, nada externo**: la CSP de producción (`SECURE_CSP` en
  `config/settings.py`) solo admite estilos, scripts y fuentes de `'self'`. Por
  eso no hay `<style>`, `style=""`, `on*=` ni scripts en línea ni CDN: el CSS está
  en `procesos/static/procesos/css/app.css` y la mejora progresiva en
  `.../js/app.js`. Todo funciona sin JavaScript; el JS agrega comodidad (zona de
  arrastre, estado de espera, selector de juzgado). `procesos/tests/test_diseno.py`
  falla si una pantalla vuelve a incluir algo que la CSP bloquearía.
- **Accesibilidad**: contrastes AA (botón primario `#d90277`, bordes de controles
  ≥3:1), foco visible, resumen de errores con foco, `forced-colors`,
  `prefers-reduced-motion` y barras que no se comen la pantalla con poca altura.
- Los estáticos se sirven con WhiteNoise (`collectstatic` corre en el
  `Dockerfile`). Los tests usan el almacenamiento simple y una prueba aparte ejecuta
  `collectstatic` con el almacenamiento de producción (manifiesto con hash).

### UTM automático (Módulo 4)

El botón "Verificar valor UTM" en `/plantilla/` consulta el valor UTM
vigente en [mindicador.cl](https://mindicador.cl/api/utm) (`motor/utm.py`)
y actualiza `Juzgado.utm_valor` si cambió — nunca en segundo plano, solo
al apretar el botón, porque afecta el monto de cada multa en la carta.
Se eligió mindicador.cl (pública, gratuita, sin registro) en vez de la
API oficial de la CMF porque esta última exige una API key que no se
tramitó; si en algún momento se consigue, `motor/utm.py` es el único
archivo que habría que tocar. El valor también se puede seguir editando
a mano desde `/admin/`. Ver la nota sobre la salida a internet del
servidor en DESPLIEGUE.md.

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
