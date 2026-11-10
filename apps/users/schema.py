"""Tells the API docs (drf-spectacular) that our cached JWT authentication is a Bearer token."""

from drf_spectacular.contrib.rest_framework_simplejwt import SimpleJWTScheme


class CachedJWTScheme(SimpleJWTScheme):
    target_class = "apps.users.authentication.CachedUserJWTAuthentication"
