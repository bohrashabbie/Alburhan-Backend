from django.contrib import admin

from .models import Permission, Role, RolePermission, UserRole


@admin.register(Permission)
class PermissionAdmin(admin.ModelAdmin):
    list_display = ["code", "module", "name", "is_active"]
    list_filter = ["module", "is_active"]
    search_fields = ["code", "name"]

    # The catalogue is defined in code; edit permissions_catalog.py instead.
    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class RolePermissionInline(admin.TabularInline):
    model = RolePermission
    extra = 0
    autocomplete_fields = ["permission"]
    readonly_fields = ["granted_by", "created_at"]


@admin.register(Role)
class RoleAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_system", "is_active"]
    list_filter = ["is_system", "is_active"]
    search_fields = ["code", "name", "name_ar"]
    inlines = [RolePermissionInline]


@admin.register(UserRole)
class UserRoleAdmin(admin.ModelAdmin):
    list_display = ["user", "role", "company", "branch", "is_active"]
    list_filter = ["role", "company", "is_active"]
    search_fields = ["user__username", "user__email"]
    autocomplete_fields = ["user", "role"]
