"""Permission catalogue: each app declares its permissions in `<app>/permissions_catalog.py` as
`PERMISSIONS = [...]` and `sync_permissions()` writes them to the database (run on every migrate).

The built-in "administrator" role always receives every active permission.
"""

from dataclasses import dataclass
from importlib import import_module

from django.apps import apps as django_apps
from django.db import transaction

ADMIN_ROLE_CODE = "administrator"
CRUD_VERBS = (("view", "View"), ("create", "Create"), ("update", "Edit"), ("delete", "Delete"))


@dataclass(frozen=True)
class PermissionDef:
    code: str
    name: str
    description: str = ""

    @property
    def module(self) -> str:
        return self.code.split(".", 1)[0]


def crud(resource: str, label: str) -> list[PermissionDef]:
    """view/create/update/delete permissions for one resource, e.g. crud("sales.invoice", "invoices")."""
    return [PermissionDef(f"{resource}.{verb}", f"{title} {label}") for verb, title in CRUD_VERBS]


def collect_permissions() -> list[PermissionDef]:
    found: dict[str, PermissionDef] = {}
    for app_config in django_apps.get_app_configs():
        try:
            module = import_module(f"{app_config.name}.permissions_catalog")
        except ModuleNotFoundError as exc:
            if exc.name == f"{app_config.name}.permissions_catalog":
                continue
            raise
        for definition in module.PERMISSIONS:
            if definition.code in found:
                raise ValueError(f"Permission {definition.code} is declared twice.")
            found[definition.code] = definition
    return list(found.values())


@transaction.atomic
def sync_permissions() -> dict[str, int]:
    from .models import Permission, Role, RolePermission

    definitions = collect_permissions()
    codes = {d.code for d in definitions}
    existing = {p.code: p for p in Permission.objects.all()}
    created = updated = 0

    for definition in definitions:
        permission = existing.get(definition.code)
        if permission is None:
            Permission.objects.create(
                code=definition.code,
                module=definition.module,
                name=definition.name,
                description=definition.description,
            )
            created += 1
        elif (
            permission.name != definition.name
            or permission.description != definition.description
            or permission.module != definition.module
            or not permission.is_active
        ):
            permission.name = definition.name
            permission.description = definition.description
            permission.module = definition.module
            permission.is_active = True
            permission.save()
            updated += 1

    deactivated = (
        Permission.objects.filter(is_active=True).exclude(code__in=codes).update(is_active=False)
    )

    admin, _ = Role.objects.get_or_create(
        code=ADMIN_ROLE_CODE,
        defaults={
            "name": "Administrator",
            "name_ar": "مدير النظام",
            "description": "Every permission in the assigned company.",
            "is_system": True,
        },
    )
    have = set(admin.role_permissions.values_list("permission_id", flat=True))
    missing = Permission.objects.filter(is_active=True).exclude(pk__in=have)
    RolePermission.objects.bulk_create(
        [RolePermission(role=admin, permission=permission) for permission in missing]
    )

    # bulk_create/update don't send signals: retire every cached grant explicitly.
    from .cache import invalidate_all

    invalidate_all()
    return {"created": created, "updated": updated, "deactivated": deactivated}
