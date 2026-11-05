"""Installation: technicians and work orders (site jobs) for showroom sales and projects.
A completed work order carries its labour cost into the project margin and can be billed as a
sales invoice with an installation service product."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import CompanyScopedModel

ZERO = Decimal("0")


class Technician(CompanyScopedModel):
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Linked login, if the technician uses the app."),
    )
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    skills = models.CharField(
        max_length=200, blank=True, help_text=_("e.g. electrical, magnetic track, outdoor")
    )
    hourly_cost = MoneyField(help_text=_("Internal cost per hour, company currency."))
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class WorkOrder(CompanyScopedModel):
    class Status(models.TextChoices):
        SCHEDULED = "scheduled", _("Scheduled")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")
        CANCELLED = "cancelled", _("Cancelled")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    title = models.CharField(
        max_length=200, help_text=_("e.g. Install 24 downlights, villa in Salwa")
    )
    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="work_orders"
    )
    project = models.ForeignKey(
        "projects.Project",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="work_orders",
    )
    source_invoice = models.ForeignKey(
        "sales.Invoice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("The showroom sale being installed."),
    )
    branch = models.ForeignKey("organization.Branch", on_delete=models.PROTECT, related_name="+")
    site_address = models.TextField(blank=True)
    contact_name = models.CharField(max_length=120, blank=True)
    contact_phone = models.CharField(max_length=32, blank=True)
    scheduled_date = models.DateField(db_index=True)
    scheduled_time = models.TimeField(null=True, blank=True)
    duration_hours = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("2"))
    technicians = models.ManyToManyField(Technician, related_name="work_orders", blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.SCHEDULED)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    completion_notes = models.TextField(blank=True)
    signed_by = models.CharField(
        max_length=120, blank=True, help_text=_("Who accepted the work on site.")
    )
    labour_hours = models.DecimalField(max_digits=7, decimal_places=2, default=ZERO)
    labour_cost = MoneyField(help_text=_("Hours × technicians' hourly cost, company currency."))
    cancel_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-scheduled_date", "-id"]
        indexes = [models.Index(fields=["company", "status", "scheduled_date"])]

    def __str__(self):
        return f"{self.number} {self.title}".strip()
