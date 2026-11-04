from django.apps import AppConfig


class AccountingConfig(AppConfig):
    name = "apps.accounting"
    label = "accounting"
    verbose_name = "Accounting"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (
            models.Account,
            models.Journal,
            models.AccountingPeriod,
            models.JournalEntry,
            models.JournalLine,
            models.AccountingSettings,
        ):
            auditlog.register(model)
