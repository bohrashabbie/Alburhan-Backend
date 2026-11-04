from django.db import transaction
from rest_framework import serializers

from .cache import invalidate_all
from .models import Permission, Role, RolePermission, UserRole
from .services import has_permission

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


class PermissionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Permission
        fields = ["id", "code", "module", "name", "description", "is_active"]


class RoleSerializer(serializers.ModelSerializer):
    permissions = serializers.SlugRelatedField(
        slug_field="code",
        queryset=Permission.objects.filter(is_active=True),
        many=True,
        required=False,
    )

    class Meta:
        model = Role
        fields = [
            "id",
            "code",
            "name",
            "name_ar",
            "description",
            "is_system",
            "is_active",
            "permissions",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["is_system", *AUDIT_FIELDS]

    def validate(self, attrs):
        if self.instance and self.instance.is_system:
            raise serializers.ValidationError("System roles cannot be changed.")
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and not user.is_superuser:
            # Nobody can build a role that grants more than they hold themselves.
            missing = [
                p.code for p in attrs.get("permissions", []) if not has_permission(user, p.code)
            ]
            if missing:
                raise serializers.ValidationError(
                    {"permissions": f"You cannot grant permissions you do not hold: {missing}"}
                )
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        permissions = validated_data.pop("permissions", [])
        role = super().create(validated_data)
        self._set_permissions(role, permissions)
        return role

    @transaction.atomic
    def update(self, instance, validated_data):
        permissions = validated_data.pop("permissions", None)
        role = super().update(instance, validated_data)
        if permissions is not None:
            self._set_permissions(role, permissions)
        return role

    def _set_permissions(self, role, permissions):
        request = self.context.get("request")
        wanted = {p.pk for p in permissions}
        current = set(role.role_permissions.values_list("permission_id", flat=True))
        role.role_permissions.exclude(permission_id__in=wanted).delete()
        RolePermission.objects.bulk_create(
            [
                RolePermission(
                    role=role, permission_id=pk, granted_by=getattr(request, "user", None)
                )
                for pk in wanted - current
            ]
        )
        invalidate_all()  # bulk_create sends no signals


class UserRoleSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)
    company_code = serializers.CharField(source="company.code", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = UserRole
        fields = [
            "id",
            "user",
            "username",
            "role",
            "role_name",
            "company",
            "company_code",
            "branch",
            "branch_name",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        # Uniqueness (with a nullable branch) is enforced by the database constraint and
        # checked in validate() so the API returns a clean 400.
        validators = []

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        user, role, company, branch = get("user"), get("role"), get("company"), get("branch")

        if branch and branch.company_id != company.pk:
            raise serializers.ValidationError(
                {"branch": "Branch must belong to the selected company."}
            )

        duplicate = UserRole.objects.filter(user=user, role=role, company=company, branch=branch)
        if self.instance:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise serializers.ValidationError("This user already has this role there.")

        request = self.context.get("request")
        actor = getattr(request, "user", None)
        if actor and not actor.is_superuser:
            # Prevent privilege escalation: you may only hand out a role whose every permission
            # you hold yourself in that same company/branch.
            codes = role.role_permissions.filter(permission__is_active=True).values_list(
                "permission__code", flat=True
            )
            branch_id = branch.pk if branch else None
            missing = [c for c in codes if not has_permission(actor, c, company.pk, branch_id)]
            if missing:
                raise serializers.ValidationError(
                    {"role": "You cannot assign a role with permissions you do not hold there."}
                )
        return attrs
