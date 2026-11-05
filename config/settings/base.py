"""Shared settings for every environment. Environment-specific overrides live in dev/test/prod."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()
environ.Env.read_env(BASE_DIR / ".env")

SECRET_KEY = env(
    "DJANGO_SECRET_KEY", default="insecure-dev-only-key-change-me-in-every-real-environment"
)
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]
THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "auditlog",
]
LOCAL_APPS = [
    "apps.core",
    "apps.users",
    "apps.access",
    "apps.organization",
    "apps.catalog",
    "apps.partners",
    "apps.accounting",
    "apps.inventory",
    "apps.sales",
    "apps.pos",
    "apps.purchasing",
    "apps.projects",
    "apps.installation",
    "apps.reports",
]
INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "auditlog.middleware.AuditlogMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# PostgreSQL in every real environment. SQLite fallback only so the project boots without Docker.
DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}
# Reads run without a wrapping transaction (saves are wrapped in AuditedViewSetMixin), which saves
# two round trips per request: BEGIN and COMMIT.
DATABASES["default"]["ATOMIC_REQUESTS"] = False
if DATABASES["default"]["ENGINE"] == "django.db.backends.postgresql":
    # A pool keeps connections open and shares them across threads and requests. Opening a new
    # connection costs ~2 s when the database is in another region.
    DATABASES["default"]["CONN_MAX_AGE"] = 0  # required with pooling
    DATABASES["default"].setdefault("OPTIONS", {})["pool"] = {
        "min_size": env.int("DB_POOL_MIN", default=2),
        "max_size": env.int("DB_POOL_MAX", default=10),
        "timeout": 10,
    }

# Cache: Redis in production (CACHE_URL=redis://host:6379/1), process memory locally.
CACHES = {"default": env.cache("CACHE_URL", default="locmemcache://alburhan")}
CACHES["default"]["KEY_PREFIX"] = env("CACHE_KEY_PREFIX", default="erp")
CACHES["default"]["TIMEOUT"] = 60 * 60

AUTH_USER_MODEL = "users.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Internationalisation: English + Arabic (RTL handled by the frontends) ---
LANGUAGE_CODE = "en"
LANGUAGES = [("en", "English"), ("ar", "العربية")]
LOCALE_PATHS = [BASE_DIR / "locale"]
USE_I18N = True
# Store UTC; each company/branch has its own timezone for display and business dates.
TIME_ZONE = "Asia/Kuwait"
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = env("DJANGO_MEDIA_ROOT", default=str(BASE_DIR / "media"))

# --- ERP-wide business settings ---
# Group reporting currency for consolidated dashboards and reports.
ERP_BASE_CURRENCY = "KWD"

# --- API ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "apps.users.authentication.CachedUserJWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": ["apps.core.permissions.ErpPermission"],
    "DEFAULT_FILTER_BACKENDS": [
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardPagination",
    "PAGE_SIZE": 50,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "COERCE_DECIMAL_TO_STRING": True,
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {"login": env("LOGIN_THROTTLE_RATE", default="10/min")},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=7)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Al-Burhan ERP API",
    "DESCRIPTION": "Backend API for the Al-Burhan Regional ERP and POS.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

AUDITLOG_INCLUDE_ALL_MODELS = False

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": env("DJANGO_LOG_LEVEL", default="INFO")},
}

# --- File storage ---
# "s3": private objects in AWS_STORAGE_BUCKET_NAME under AWS_LOCATION/, served through short-lived
# signed links. "local": MEDIA_ROOT on disk, for development without S3.
STORAGE_BACKEND = env("STORAGE_BACKEND", default="local")
if STORAGE_BACKEND == "s3":
    _default_storage = {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("AWS_STORAGE_BUCKET_NAME"),
            "region_name": env("AWS_REGION", default="eu-north-1"),
            "location": env("AWS_LOCATION", default="erp"),
            "default_acl": None,  # bucket default: private
            "querystring_auth": True,
            "querystring_expire": env.int("AWS_SIGNED_URL_SECONDS", default=3600),
            "file_overwrite": False,
            "signature_version": "s3v4",
            "addressing_style": "virtual",
        },
    }
else:
    _default_storage = {"BACKEND": "django.core.files.storage.FileSystemStorage"}
STORAGES = {
    "default": _default_storage,
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
# Uploads: images only for now, at most 2 MB each.
MAX_IMAGE_UPLOAD_BYTES = env.int("MAX_IMAGE_UPLOAD_BYTES", default=2 * 1024 * 1024)
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
