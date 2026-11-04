from .base import *  # noqa: F403

DEBUG = False
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

REST_FRAMEWORK = {
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_RATES": {"login": "1000/min"},
}

SECRET_KEY = "test-only-secret-key-that-is-long-enough-for-hs256"

# Tests run on a throwaway local database, never the shared server in DATABASE_URL.
DATABASES = {
    "default": env.db("TEST_DATABASE_URL", default="sqlite://:memory:"),  # noqa: F405
}
DATABASES["default"]["ATOMIC_REQUESTS"] = False
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
