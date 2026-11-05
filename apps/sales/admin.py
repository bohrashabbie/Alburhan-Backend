from django.contrib import admin

from .models import (
    CreditNote,
    DeliveryNote,
    Invoice,
    InvoiceLine,
    Quotation,
    QuotationLine,
    Receipt,
    SalesSettings,
)


class QuotationLineInline(admin.TabularInline):
    model = QuotationLine
    extra = 0


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "customer", "branch", "total", "status"]
    list_filter = ["company", "status"]
    search_fields = ["number", "customer__name"]
    inlines = [QuotationLineInline]


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "customer", "branch", "total", "amount_paid", "status"]
    list_filter = ["company", "status"]
    search_fields = ["number", "customer__name"]
    inlines = [InvoiceLineInline]


admin.site.register(DeliveryNote)
admin.site.register(Receipt)
admin.site.register(CreditNote)
admin.site.register(SalesSettings)
