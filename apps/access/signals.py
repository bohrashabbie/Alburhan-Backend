"""Keep cached grants in step with the data they come from."""

from django.db.models.signals import post_delete, post_save

from apps.organization.models import Company

from .cache import invalidate_all, invalidate_user
from .models import Permission, Role, RolePermission, UserRole


def _user_role_changed(sender, instance, **kwargs):
    invalidate_user(instance.user_id)


def _global_change(sender, instance, **kwargs):
    invalidate_all()


def connect():
    for signal in (post_save, post_delete):
        signal.connect(_user_role_changed, sender=UserRole, dispatch_uid=f"grants_ur_{signal}")
        for model in (Role, RolePermission, Permission, Company):
            signal.connect(
                _global_change, sender=model, dispatch_uid=f"grants_{model.__name__}_{signal}"
            )
