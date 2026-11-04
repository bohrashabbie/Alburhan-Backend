from django.apps import AppConfig


class InventoryConfig(AppConfig):
    name = "apps.inventory"
    label = "inventory"
    verbose_name = "Inventory"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (
            models.StockAdjustment,
            models.StockTransfer,
            models.StockCount,
            models.Reservation,
            models.ReorderRule,
        ):
            auditlog.register(model)
