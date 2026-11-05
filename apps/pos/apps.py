from django.apps import AppConfig


class PosConfig(AppConfig):
    name = "apps.pos"
    label = "pos"
    verbose_name = "Point of sale"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (models.Register, models.Shift, models.Sale):
            auditlog.register(model)
