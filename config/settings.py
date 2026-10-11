"""
Configuración de Django para jpltag.

Sigue las mismas convenciones que las otras dos aplicaciones ya publicadas
en maiproject.cl (Soporte Existencias y MaiReservas), descritas en el
"Informe de implementación — Servidor maiproject.cl" (10-oct-2026):
  - Detrás de HAProxy (TLS terminado ahí, X-Forwarded-Proto).
  - PostgreSQL a través de PgBouncer en modo transacción
    (CONN_MAX_AGE=0, cursores de servidor desactivados).
  - Valkey para caché y sesiones.
  - Sin librerías/fuentes cargadas desde CDN (CSP estricta, todo autoalojado).
  - Errores reportados a GlitchTip (SDK de Sentry, nunca a Sentry mismo).
  - Login con límite de intentos por usuario + IP (django-axes).
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
)
environ.Env.read_env(BASE_DIR / ".env")

# ──────────────────────────────────────────────────────────────────────────
# Seguridad básica
# ──────────────────────────────────────────────────────────────────────────
SECRET_KEY = env("DJANGO_SECRET_KEY", default="clave-insegura-solo-para-desarrollo-local")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
# Igual que Soporte Existencias: se puede fijar explícitamente con
# DJANGO_CSRF_TRUSTED_ORIGINS (lista separada por comas, con esquema,
# ej. "https://jpltag.maiproject.cl"). Sin esa variable (desarrollo local),
# se calcula sola a partir de ALLOWED_HOSTS, igual que antes.
CSRF_TRUSTED_ORIGINS = env.list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=[f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")],
)

# HAProxy termina TLS y reenvía esta cabecera (ver sección 10 del informe)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# HSTS (manage.py check --deploy, 11-oct-2026): activarlo acá es seguro
# aunque la app también reciba tráfico HTTP interno sin terminar en TLS
# (el healthcheck del compose.yaml generado por 13-app-jpltag.sh, y la
# prueba de login dentro del propio contenedor cuando JPLTAG_SOLO_IPS=si
# — ver DESPLIEGUE.md §0 y §6): SecurityMiddleware solo agrega el header
# Strict-Transport-Security cuando request.is_secure() es True, es decir
# cuando ve X-Forwarded-Proto: https (SECURE_PROXY_SSL_HEADER, arriba) —
# ninguna de esas dos pruebas internas manda esa cabecera, así que no les
# afecta. jpltag.maiproject.cl no tiene subdominios propios, por eso
# INCLUDE_SUBDOMAINS es inofensivo; PRELOAD solo agrega la directiva al
# header — no inscribe el dominio en ninguna lista por su cuenta.
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000  # 1 año, recomendación de Django/Mozilla
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

# SECURE_SSL_REDIRECT (manage.py check --deploy, security.W008) se deja
# DELIBERADAMENTE en False — NO es un olvido. HAProxy termina el TLS y,
# igual que con las otras apps del servidor, es HAProxy quien decide si
# redirige HTTP→HTTPS en el borde; Django nunca ve esa conexión. Si se
# pusiera en True acá, redirigiría también el tráfico HTTP interno (sin
# X-Forwarded-Proto: https) con el que YA se prueba la app dentro del
# propio contenedor — el healthcheck del compose.yaml generado por
# 13-app-jpltag.sh, y la prueba de login cuando JPLTAG_SOLO_IPS=si (ver
# DESPLIEGUE.md §0 y §6) — rompiendo ambas, que hoy están verificadas y
# funcionando. Se silencia el warning en vez de ignorarlo en silencio.
SILENCED_SYSTEM_CHECKS = ["security.W008"]

# ──────────────────────────────────────────────────────────────────────────
# Apps y middleware
# ──────────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "nucleo",
    "procesos",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "axes.middleware.AxesMiddleware",
]

AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ──────────────────────────────────────────────────────────────────────────
# Base de datos (PostgreSQL vía PgBouncer, modo transacción)
# ──────────────────────────────────────────────────────────────────────────
# El default a SQLite local es solo para correr `manage.py test`/`check` y
# generar migraciones sin un Postgres a mano (ej. en este sandbox de
# desarrollo); en el servidor real SIEMPRE llega DATABASE_URL (lo genera
# `sudo maipu-nueva-bd jpltag`), así que este default nunca se usa ahí.
DATABASES = {
    "default": env.db_url("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"]["DISABLE_SERVER_SIDE_CURSORS"] = True

# ──────────────────────────────────────────────────────────────────────────
# Caché y sesiones (Valkey)
# ──────────────────────────────────────────────────────────────────────────
# Igual que con DATABASES: sin VALKEY_URL (fuera del servidor real) se cae a
# caché en memoria del proceso, solo para poder correr `manage.py test`/
# `runserver` en una máquina de desarrollo sin Valkey instalado.
if env("VALKEY_URL", default=""):
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": env("VALKEY_URL"),
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"

# ──────────────────────────────────────────────────────────────────────────
# Autenticación / roles (ver nucleo.models.PerfilUsuario)
# ──────────────────────────────────────────────────────────────────────────
LOGIN_URL = "procesos:ingresar"
LOGIN_REDIRECT_URL = "procesos:panel"
LOGOUT_REDIRECT_URL = "procesos:ingresar"

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 0.5  # horas (30 min), igual que las otras apps del servidor
AXES_LOCKOUT_PARAMETERS = ["username", "ip_address"]

# ──────────────────────────────────────────────────────────────────────────
# Seguridad de navegación: sin CDN, todo autoalojado (regla de la plataforma)
# ──────────────────────────────────────────────────────────────────────────
# CSP nativa de Django 6.1 (django.middleware.csp.ContentSecurityPolicyMiddleware),
# no el paquete django-csp: así lo confirma el informe de Soporte Existencias
# ("CSP nativa de Django 6.1"), que es la app hermana ya publicada en el mismo
# servidor. El formato es un dict plano {directiva: [valores]}, sin el
# envoltorio "DIRECTIVES" que usa django-csp.
SECURE_CSP = {
    "default-src": ["'self'"],
    "script-src": ["'self'"],
    "style-src": ["'self'"],
    "font-src": ["'self'"],
    "img-src": ["'self'", "data:"],
    "object-src": ["'none'"],
    "base-uri": ["'self'"],
    "frame-ancestors": ["'none'"],
}

# ──────────────────────────────────────────────────────────────────────────
# Internacionalización
# ──────────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = "es-cl"
TIME_ZONE = "America/Santiago"
USE_I18N = True
USE_TZ = True

# ──────────────────────────────────────────────────────────────────────────
# Archivos estáticos y subidos (plantillas, logos, firmas)
# ──────────────────────────────────────────────────────────────────────────
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Lección ya documentada en el servidor (Soporte Existencias/MaiReservas):
# en True, un admin theme o un estático sin hash en el manifest hace que
# /admin/ devuelva 500 en vez de solo omitir ese archivo.
WHITENOISE_MANIFEST_STRICT = False
STORAGES = {
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage",
    },
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# Cartas generadas, Excel de seguimiento, duplicados y logs de error de cada
# corrida (motor.cartas.generar_cartas_certificadas). A propósito NO viven
# bajo MEDIA_ROOT/MEDIA_URL: traen RUT, domicilio y nombre del infractor, así
# que nunca se sirven por una URL pública — solo la vista protegida de
# descarga (procesos.views) los entrega, y solo a un usuario con acceso a
# ese juzgado (ver nucleo.models.PerfilUsuario.tiene_acceso_a).
SALIDAS_ROOT = BASE_DIR / "salidas"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ──────────────────────────────────────────────────────────────────────────
# Errores → GlitchTip (SDK de Sentry; nunca a Sentry directamente)
#
# Por defecto, el SDK de Sentry adjunta a cada error las variables locales
# de cada marco del stack trace (include_local_variables) y hasta ~10 KB
# del cuerpo de la request (max_request_body_size="medium"). Para esta app
# eso es justo el dato que en todo el resto del código se trata como
# sensible (RUT, domicilio, nombre del infractor — ver SALIDAS_ROOT más
# arriba y nucleo.models.PerfilUsuario.tiene_acceso_a): una excepción
# dentro de motor/lectura.py, motor/plantilla.py o procesos/views.py
# fácilmente tiene esos datos en una variable local (la fila de la nómina,
# el contexto de la carta, etc.). GlitchTip puede ser visible para gente
# sin acceso a ningún Juzgado (quien administra errores.maiproject.cl para
# las 3 apps), así que acá se desactiva esa captura explícitamente — un
# error en GlitchTip debe alcanzar para diagnosticar dónde y qué tipo de
# excepción, no para leer los datos de un infractor.
# ──────────────────────────────────────────────────────────────────────────
GLITCHTIP_DSN = env("GLITCHTIP_DSN", default="")
if GLITCHTIP_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=GLITCHTIP_DSN,
        integrations=[DjangoIntegration()],
        environment="produccion" if not DEBUG else "desarrollo",
        send_default_pii=False,
        include_local_variables=False,
        max_request_body_size="never",
    )
