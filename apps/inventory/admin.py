from django.contrib import admin

from .models import (
    ProductCost,
    ReorderRule,
    Reservation,
    StockAdjustment,
    StockAdjustmentLine,
    StockBalance,
    StockCount,
    StockCountLine,
    StockMove,
    StockTransfer,
    StockTransferLine,
)


@admin.register(StockBalance)
class BalanceAdmin(admin.ModelAdmin):
    list_display = ["warehouse", "product", "status", "quantity"]
    list_filter = ["company", "warehouse", "status"]
    search_fields = ["product__sku", "product__name"]

    def has_add_permission(self, request):
        return False  # balances only change through stock moves


@admin.register(ProductCost)
class CostAdmin(admin.ModelAdmin):
    list_display = ["company", "product", "quantity", "value", "average_cost"]
    search_fields = ["product__sku"]


@admin.register(StockMove)
class MoveAdmin(admin.ModelAdmin):
    list_display = ["date", "warehouse", "product", "status", "quantity", "move_type", "reference"]
    list_filter = ["company", "move_type", "status"]
    search_fields = ["product__sku", "reference"]

    def has_change_permission(self, request, obj=None):
        return False


class AdjustmentLineInline(admin.TabularInline):
    model = StockAdjustmentLine
    extra = 0


@admin.register(StockAdjustment)
class AdjustmentAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "warehouse", "reason", "status"]
    list_filter = ["company", "status", "reason"]
    inlines = [AdjustmentLineInline]


class TransferLineInline(admin.TabularInline):
    model = StockTransferLine
    extra = 0


@admin.register(StockTransfer)
class TransferAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "from_warehouse", "to_warehouse", "status"]
    list_filter = ["company", "status"]
    inlines = [TransferLineInline]


class CountLineInline(admin.TabularInline):
    model = StockCountLine
    extra = 0


@admin.register(StockCount)
class CountAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "warehouse", "status"]
    list_filter = ["company", "status"]
    inlines = [CountLineInline]


@admin.register(Reservation)
class ReservationAdmin(admin.ModelAdmin):
    list_display = ["warehouse", "product", "quantity", "status", "reference"]
    list_filter = ["company", "status"]


@admin.register(ReorderRule)
class ReorderRuleAdmin(admin.ModelAdmin):
    list_display = ["warehouse", "product", "min_quantity", "reorder_quantity", "is_active"]
    list_filter = ["company", "is_active"]
