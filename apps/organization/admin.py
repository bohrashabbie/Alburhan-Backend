from django.contrib import admin

from .models import Branch, Company, Country, Currency, DocumentSequence, ExchangeRate, Warehouse


class AuditedAdmin(admin.ModelAdmin):
    readonly_fields = ["created_at", "updated_at", "created_by", "updated_by"]

    def save_model(self, request, obj, form, change):
        if not change:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "symbol", "decimal_places", "is_active"]


@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "default_currency", "is_active"]


class BranchInline(admin.TabularInline):
    model = Branch
    extra = 0
    fields = ["code", "name", "branch_type", "is_active"]


@admin.register(Company)
class CompanyAdmin(AuditedAdmin):
    list_display = ["code", "name", "country", "currency", "is_active"]
    list_filter = ["country", "is_active"]
    search_fields = ["code", "name", "name_ar", "legal_name"]
    inlines = [BranchInline]


@admin.register(Branch)
class BranchAdmin(AuditedAdmin):
    list_display = ["code", "name", "company", "branch_type", "is_active"]
    list_filter = ["company", "branch_type", "is_active"]
    search_fields = ["code", "name", "name_ar"]


@admin.register(Warehouse)
class WarehouseAdmin(AuditedAdmin):
    list_display = ["code", "name", "company", "branch", "warehouse_type", "is_active"]
    list_filter = ["company", "warehouse_type", "is_active"]
    search_fields = ["code", "name", "name_ar"]


@admin.register(ExchangeRate)
class ExchangeRateAdmin(AuditedAdmin):
    list_display = ["currency", "to_currency", "rate", "effective_date", "source"]
    list_filter = ["currency", "to_currency"]
    date_hierarchy = "effective_date"


@admin.register(DocumentSequence)
class DocumentSequenceAdmin(AuditedAdmin):
    list_display = ["company", "doc_type", "prefix", "padding", "next_number", "reset_yearly"]
    list_filter = ["company"]
    readonly_fields = AuditedAdmin.readonly_fields + ["current_year"]
