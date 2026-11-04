from django.apps import AppConfig
from django.db.models.signals import post_migrate


def _sync_after_migrate(sender, **kwargs):
    from .catalog import sync_permissions

    sync_permissions()


class AccessConfig(AppConfig):
    name = "apps.access"
    label = "access"
    verbose_name = "Roles & permissions"

    def ready(self):
        from auditlog.registry import auditlog

        from .models import Role, RolePermission, UserRole

        auditlog.register(Role)
        auditlog.register(RolePermission)
        auditlog.register(UserRole)
        post_migrate.connect(_sync_after_migrate, sender=self, dispatch_uid="access_sync_perms")

        from . import signals

        signals.connect()
