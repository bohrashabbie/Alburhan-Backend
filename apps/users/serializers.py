from django.db.models import Q
from rest_framework import serializers

from apps.access.services import has_permission, load_grants
from apps.core.images import image_url
from apps.organization.models import Branch, Company

from .models import User


class CompanyAccessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = ["id", "code", "name", "name_ar", "country", "currency"]


class BranchAccessSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = ["id", "company", "code", "name", "name_ar", "branch_type"]


class MeSerializer(serializers.ModelSerializer):
    """The signed-in user plus what the frontends need to build menus and company switchers.

    `access` lists each role assignment with its scope and permission codes, so the UI can show a
    button only where the user may use it: company-wide when branch is null, else that branch.
    """

    companies = serializers.SerializerMethodField()
    branches = serializers.SerializerMethodField()
    access = serializers.SerializerMethodField()
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "preferred_language",
            "avatar_url",
            "is_superuser",
            "default_company",
            "default_branch",
            "companies",
            "branches",
            "access",
        ]
        read_only_fields = ["id", "username", "email", "is_superuser"]

    def get_avatar_url(self, user) -> str | None:
        return image_url(user, "avatar")

    # One query each (the database can be far away, so every round trip counts).

    def get_companies(self, user) -> list[dict]:
        companies = Company.objects.filter(is_active=True)
        if not user.is_superuser:
            companies = companies.filter(pk__in={g.company_id for g in load_grants(user)})
        return CompanyAccessSerializer(companies, many=True).data

    def get_branches(self, user) -> list[dict]:
        branches = Branch.objects.filter(is_active=True, company__is_active=True)
        if not user.is_superuser:
            grants = load_grants(user)
            whole = {g.company_id for g in grants if g.branch_id is None}
            single = {g.branch_id for g in grants if g.branch_id is not None}
            branches = branches.filter(Q(company_id__in=whole) | Q(pk__in=single))
        return BranchAccessSerializer(branches, many=True).data

    def get_access(self, user) -> list[dict]:
        assignments = user.user_roles.filter(
            is_active=True, role__is_active=True, company__is_active=True
        ).values("company_id", "branch_id", "role__code", "role__name")
        grants = {(g.company_id, g.branch_id): set() for g in load_grants(user)}
        for grant in load_grants(user):
            grants[(grant.company_id, grant.branch_id)] |= grant.permissions
        return [
            {
                "company": a["company_id"],
                "branch": a["branch_id"],
                "role": a["role__code"],
                "role_name": a["role__name"],
                "permissions": sorted(grants.get((a["company_id"], a["branch_id"]), set())),
            }
            for a in assignments
        ]

    def validate_default_company(self, company):
        if company and company.pk not in self.instance.allowed_company_ids():
            raise serializers.ValidationError("You do not have access to this company.")
        return company

    def validate_default_branch(self, branch):
        if branch and branch.pk not in self.instance.allowed_branch_ids():
            raise serializers.ValidationError("You do not have access to this branch.")
        return branch


class UserSerializer(serializers.ModelSerializer):
    """Account details only. Access is granted separately through /access/user-roles/."""

    password = serializers.CharField(write_only=True, required=False, min_length=8)
    avatar_url = serializers.SerializerMethodField()

    def get_avatar_url(self, user) -> str | None:
        return image_url(user, "avatar")

    class Meta:
        model = User
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "phone",
            "preferred_language",
            "is_active",
            "default_company",
            "default_branch",
            "password",
            "avatar_url",
            "last_login",
            "date_joined",
        ]
        read_only_fields = ["last_login", "date_joined"]

    def validate(self, attrs):
        request = self.context.get("request")
        actor = getattr(request, "user", None)
        company = attrs.get("default_company", getattr(self.instance, "default_company", None))
        branch = attrs.get("default_branch", getattr(self.instance, "default_branch", None))
        if branch and company and branch.company_id != company.pk:
            raise serializers.ValidationError(
                {"default_branch": "Branch must belong to the default company."}
            )
        if actor and not actor.is_superuser:
            # Non-superusers manage users of their own companies; the home company scopes the user.
            if company is None:
                raise serializers.ValidationError(
                    {"default_company": "A home company is required."}
                )
            code = "users.user.update" if self.instance else "users.user.create"
            if not has_permission(actor, code, company.pk):
                raise serializers.ValidationError(
                    {"default_company": "You cannot manage users of this company."}
                )
        return attrs

    def create(self, validated_data):
        password = validated_data.pop("password", None)
        user = User(**validated_data)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return user

    def update(self, instance, validated_data):
        password = validated_data.pop("password", None)
        user = super().update(instance, validated_data)
        if password:
            user.set_password(password)
            user.save()
        return user


class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=8)

    def validate_current_password(self, value):
        if not self.context["request"].user.check_password(value):
            raise serializers.ValidationError("That is not your current password.")
        return value

    def validate_new_password(self, value):
        from django.contrib.auth.password_validation import validate_password

        validate_password(value, self.context["request"].user)
        return value
