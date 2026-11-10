from django.apps import AppConfig


class UsersConfig(AppConfig):
    name = "apps.users"
    label = "users"
    verbose_name = "Users & access"

    def ready(self):
        from auditlog.registry import auditlog

        from .models import User

        auditlog.register(User, exclude_fields=["password", "last_login"])

        from . import authentication, schema  # noqa: F401 - schema registers the docs extension

        authentication.connect()
