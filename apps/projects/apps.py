from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    name = "apps.projects"
    label = "projects"
    verbose_name = "Projects"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (models.Project, models.MaterialIssue):
            auditlog.register(model)
