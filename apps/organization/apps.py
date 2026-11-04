from django.apps import AppConfig


class OrganizationConfig(AppConfig):
    name = "apps.organization"
    label = "organization"
    verbose_name = "Organization"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (
            models.Company,
            models.Branch,
            models.Warehouse,
            models.ExchangeRate,
            models.DocumentSequence,
        ):
            auditlog.register(model)

        from django.db.models.signals import post_delete, post_save

        from .services import invalidate_currencies, invalidate_rates

        for signal in (post_save, post_delete):
            signal.connect(
                lambda **kw: invalidate_rates(),
                sender=models.ExchangeRate,
                weak=False,
                dispatch_uid=f"fx_cache_{signal}",
            )
            signal.connect(
                lambda **kw: invalidate_currencies(),
                sender=models.Currency,
                weak=False,
                dispatch_uid=f"currency_cache_{signal}",
            )
