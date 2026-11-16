from rest_framework import viewsets
from rest_framework.exceptions import ValidationError

from apps.core.viewsets import AuditedModelViewSet, CompanyScopedModelViewSet

from .models import Permission, Role, UserRole
from .serializers import PermissionSerializer, RoleSerializer, UserRoleSerializer


class PermissionViewSet(viewsets.ReadOnlyModelViewSet):
    """The permission catalogue (defined in code, read-only over the API)."""

    queryset = Permission.objects.filter(is_active=True)
    serializer_class = PermissionSerializer
    action_permissions = {"list": "access.role.view", "retrieve": "access.role.view"}
    filterset_fields = ["module"]
    search_fields = ["code", "name"]
    pagination_class = None


class RoleViewSet(AuditedModelViewSet):
    queryset = Role.objects.prefetch_related("permissions")
    serializer_class = RoleSerializer
    permission_resource = "access.role"
    filterset_fields = ["is_active", "is_system"]
    search_fields = ["code", "name", "name_ar"]

    def perform_destroy(self, instance):
        if instance.is_system:
            raise ValidationError("System roles cannot be deleted.")
        if instance.user_roles.exists():
            raise ValidationError("Role is assigned to users; remove the assignments first.")
        instance.delete()


class UserRoleViewSet(CompanyScopedModelViewSet):
    queryset = UserRole.objects.select_related("user", "role", "company", "branch")
    serializer_class = UserRoleSerializer
    permission_resource = "access.user_role"
    branch_lookup = "branch"
    branch_field = "branch"
    filterset_fields = ["user", "role", "company", "branch", "is_active"]

    def perform_create(self, serializer):
        super().perform_create(serializer)
        _follow_access(serializer.instance.user)

    def perform_destroy(self, instance):
        user = instance.user
        super().perform_destroy(instance)
        _follow_access(user)


def _follow_access(user) -> None:
    """A person's home country and store (where the app opens at sign-in) follow their access:
    the country is the one they work in, the store their first store, so nobody has to set
    them twice."""
    assignments = list(user.user_roles.filter(is_active=True).order_by("id"))
    if not assignments:
        return
    countries = {a.company_id for a in assignments}
    company_id = (
        user.default_company_id
        if user.default_company_id in countries
        else assignments[0].company_id
    )
    stores = [a.branch_id for a in assignments if a.company_id == company_id and a.branch_id]
    branch_id = (
        user.default_branch_id
        if user.default_branch_id in stores
        else (stores[0] if stores else None)
    )
    if (company_id, branch_id) != (user.default_company_id, user.default_branch_id):
        user.default_company_id, user.default_branch_id = company_id, branch_id
        user.save(update_fields=["default_company", "default_branch"])
