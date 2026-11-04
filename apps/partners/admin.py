from django.contrib import admin

from .models import Contact, Customer, Supplier


class CustomerContactInline(admin.TabularInline):
    model = Contact
    fk_name = "customer"
    exclude = ["supplier"]
    extra = 0


class SupplierContactInline(admin.TabularInline):
    model = Contact
    fk_name = "supplier"
    exclude = ["customer"]
    extra = 0


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "company", "customer_type", "mobile", "is_active"]
    list_filter = ["company", "customer_type", "is_active"]
    search_fields = ["code", "name", "name_ar", "mobile", "email"]
    inlines = [CustomerContactInline]


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "company", "country", "currency", "is_active"]
    list_filter = ["company", "country", "is_active"]
    search_fields = ["code", "name", "name_ar", "email"]
    inlines = [SupplierContactInline]
