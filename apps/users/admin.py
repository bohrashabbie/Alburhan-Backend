from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from apps.access.models import UserRole

from .models import User


class UserRoleInline(admin.TabularInline):
    model = UserRole
    fk_name = "user"
    extra = 0
    fields = ["role", "company", "branch", "is_active"]


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ["username", "email", "first_name", "last_name", "is_active", "is_superuser"]
    search_fields = ["username", "email", "first_name", "last_name"]
    inlines = [UserRoleInline]
    fieldsets = BaseUserAdmin.fieldsets + (
        ("ERP", {"fields": ("phone", "preferred_language", "default_company", "default_branch")}),
    )
