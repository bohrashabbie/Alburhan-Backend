from django.apps import AppConfig


class InstallationConfig(AppConfig):
    name = "apps.installation"
    label = "installation"
    verbose_name = "Installation"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (models.Technician, models.WorkOrder):
            auditlog.register(model)
