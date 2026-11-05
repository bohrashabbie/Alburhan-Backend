from django.contrib import admin

from .models import Register, Sale, SalePayment, Shift


@admin.register(Register)
class RegisterAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "company", "branch", "warehouse", "is_active"]
    list_filter = ["company", "is_active"]
    search_fields = ["code", "name"]


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ["number", "register", "status", "opened_at", "closed_at", "cash_difference"]
    list_filter = ["company", "status"]
    search_fields = ["number"]


class SalePaymentInline(admin.TabularInline):
    model = SalePayment
    extra = 0


@admin.register(Sale)
class SaleAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "customer", "branch", "total", "status"]
    list_filter = ["company", "status"]
    search_fields = ["number", "customer__name"]
    inlines = [SalePaymentInline]
