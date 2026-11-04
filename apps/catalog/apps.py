from django.apps import AppConfig


class CatalogConfig(AppConfig):
    name = "apps.catalog"
    label = "catalog"
    verbose_name = "Catalog"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (
            models.Brand,
            models.ProductCategory,
            models.UnitOfMeasure,
            models.Product,
            models.PriceList,
            models.PriceListItem,
        ):
            auditlog.register(model)
