from django.contrib import admin

from .models import BOQLine, MaterialIssue, Project


class BOQInline(admin.TabularInline):
    model = BOQLine
    extra = 0


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ["number", "name", "customer", "branch", "status", "contract_value"]
    list_filter = ["company", "status"]
    search_fields = ["number", "name", "customer__name"]
    inlines = [BOQInline]


admin.site.register(MaterialIssue)
