"""Abstract base models shared by every ERP module."""

from django.conf import settings
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class AuditedModel(TimeStampedModel):
    """Records who created and last changed a row. Field-level history is kept by django-auditlog."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )

    class Meta:
        abstract = True


class CompanyScopedModel(AuditedModel):
    """Business data that belongs to exactly one legal entity (country company)."""

    company = models.ForeignKey(
        "organization.Company",
        on_delete=models.PROTECT,
        related_name="+",
    )

    class Meta:
        abstract = True
