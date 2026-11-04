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
