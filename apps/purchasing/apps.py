from django.apps import AppConfig


class PurchasingConfig(AppConfig):
    name = "apps.purchasing"
    label = "purchasing"
    verbose_name = "Purchasing"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (
            models.PurchaseOrder,
            models.GoodsReceipt,
            models.SupplierInvoice,
            models.SupplierPayment,
        ):
            auditlog.register(model)
