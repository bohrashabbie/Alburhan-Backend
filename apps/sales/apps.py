from django.apps import AppConfig


class SalesConfig(AppConfig):
    name = "apps.sales"
    label = "sales"
    verbose_name = "Sales"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (
            models.Quotation,
            models.Invoice,
            models.DeliveryNote,
            models.Receipt,
            models.CreditNote,
            models.SalesSettings,
        ):
            auditlog.register(model)
