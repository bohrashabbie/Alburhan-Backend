from django.contrib import admin

from .models import (
    GoodsReceipt,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierPayment,
)


class OrderLineInline(admin.TabularInline):
    model = PurchaseOrderLine
    extra = 0


@admin.register(PurchaseOrder)
class PurchaseOrderAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "supplier", "warehouse", "total", "status"]
    list_filter = ["company", "status"]
    search_fields = ["number", "supplier__name"]
    inlines = [OrderLineInline]


class BillLineInline(admin.TabularInline):
    model = SupplierInvoiceLine
    extra = 0


@admin.register(SupplierInvoice)
class SupplierInvoiceAdmin(admin.ModelAdmin):
    list_display = [
        "number",
        "supplier_number",
        "date",
        "supplier",
        "total",
        "amount_paid",
        "status",
    ]
    list_filter = ["company", "status"]
    search_fields = ["number", "supplier_number", "supplier__name"]
    inlines = [BillLineInline]


admin.site.register(GoodsReceipt)
admin.site.register(SupplierPayment)
