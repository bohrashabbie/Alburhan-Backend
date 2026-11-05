from django.contrib import admin

from .models import Technician, WorkOrder


@admin.register(Technician)
class TechnicianAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "branch", "phone", "is_active"]
    list_filter = ["company", "is_active"]
    search_fields = ["name", "phone"]


@admin.register(WorkOrder)
class WorkOrderAdmin(admin.ModelAdmin):
    list_display = ["number", "title", "customer", "scheduled_date", "status"]
    list_filter = ["company", "status"]
    search_fields = ["number", "title", "customer__name"]
    filter_horizontal = ["technicians"]
