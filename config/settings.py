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
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in ("localhost", "127.0.0.1")]

# HAProxy termina TLS y reenvía esta cabecera (ver sección 10 del informe)
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

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
    "csp.middleware.CSPMiddleware",
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
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "font-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "frame-ancestors": ["'none'"],
    }
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
# ──────────────────────────────────────────────────────────────────────────
GLITCHTIP_DSN = env("GLITCHTIP_DSN", default="")
if GLITCHTIP_DSN:
    import sentry_sdk
    from sentry_sdk.integrations.django import DjangoIntegration

    sentry_sdk.init(
        dsn=GLITCHTIP_DSN,
        integrations=[DjangoIntegration()],
        send_default_pii=False,
    )
