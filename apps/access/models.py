"""Role-based access control.

    Permission      fixed catalogue of codes, defined in code (each app's permissions_catalog.py)
    Role            a job function, e.g. "Showroom Salesperson", "Accountant"
    RolePermission  which permissions a role has                         (role <-> permission)
    UserRole        which role a user holds, and WHERE: a company, optionally one branch

A UserRole without a branch applies to the whole company. One user can hold different roles in
different companies/branches, e.g. Salesperson at Showroom 1 and Store Keeper for UAE.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import AuditedModel, TimeStampedModel


class Permission(TimeStampedModel):
    code = models.CharField(max_length=100, unique=True, help_text=_("e.g. sales.invoice.create"))
    module = models.CharField(max_length=50, db_index=True)
    name = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(
        default=True, help_text=_("Turned off when the code no longer defines it.")
    )

    class Meta:
        ordering = ["module", "code"]

    def __str__(self):
        return self.code


class Role(AuditedModel):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    is_system = models.BooleanField(
        default=False, help_text=_("Managed by the system; cannot be edited or deleted.")
    )
    is_active = models.BooleanField(default=True)
    permissions = models.ManyToManyField(
        Permission, through="RolePermission", related_name="roles", blank=True
    )

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class RolePermission(TimeStampedModel):
    role = models.ForeignKey(Role, on_delete=models.CASCADE, related_name="role_permissions")
    permission = models.ForeignKey(
        Permission, on_delete=models.CASCADE, related_name="role_permissions"
    )
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["role", "permission"], name="uniq_role_permission")
        ]

    def __str__(self):
        return f"{self.role} → {self.permission}"


class UserRole(AuditedModel):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="user_roles"
    )
    role = models.ForeignKey(Role, on_delete=models.PROTECT, related_name="user_roles")
    company = models.ForeignKey(
        "organization.Company", on_delete=models.PROTECT, related_name="user_roles"
    )
    branch = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="user_roles",
        help_text=_("Leave empty to apply the role to every branch of the company."),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["user_id", "company_id", "branch_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "role", "company", "branch"],
                name="uniq_user_role_scope",
                nulls_distinct=False,
            )
        ]

    def __str__(self):
        where = self.branch.name if self.branch_id else self.company.name
        return f"{self.user} — {self.role} @ {where}"

    def clean(self):
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": _("Branch must belong to the selected company.")})
