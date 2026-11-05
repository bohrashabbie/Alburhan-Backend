"""Projects department: a project for a customer with a bill of quantities (BOQ), stock reserved
and issued to the site, and progress invoices raised through Sales. Profitability = project
income (posted invoices) − materials issued − installation labour."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, QuantityField
from apps.core.models import CompanyScopedModel

ZERO = Decimal("0")


class Project(CompanyScopedModel):
    class Status(models.TextChoices):
        LEAD = "lead", _("Lead")
        QUOTED = "quoted", _("Quoted")
        WON = "won", _("Won")
        IN_PROGRESS = "in_progress", _("In progress")
        COMPLETED = "completed", _("Completed")
        CLOSED = "closed", _("Closed")
        LOST = "lost", _("Lost")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, related_name="projects"
    )
    branch = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Usually the projects department."),
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    site_address = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.LEAD)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    contract_value = MoneyField(help_text=_("Agreed value in the company currency."))
    retention_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=ZERO,
        help_text=_("Held back by the client until handover."),
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["company", "status"])]

    def __str__(self):
        return f"{self.number} {self.name}".strip()


class BOQLine(models.Model):
    """One item of the bill of quantities: what was priced for the client and what it should cost."""

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="boq")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    quantity = QuantityField(default=Decimal("1"))
    unit_price = MoneyField(help_text=_("Selling price to the client, company currency."))
    unit_cost = MoneyField(help_text=_("Estimated cost per unit."))
    issued_quantity = QuantityField(help_text=_("Delivered to the site so far."))
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sequence", "id"]

    def __str__(self):
        return f"{self.product_id} × {self.quantity}"

    @property
    def amount(self) -> Decimal:
        return self.quantity * self.unit_price


class MaterialIssue(CompanyScopedModel):
    """Stock taken from a warehouse to the project site. Posting books project material cost."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Issued")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField(db_index=True)
    project = models.ForeignKey(Project, on_delete=models.PROTECT, related_name="issues")
    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    issued_to = models.CharField(
        max_length=150, blank=True, help_text=_("Site engineer or foreman.")
    )
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    cost_total = MoneyField()
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return self.number or f"Draft issue #{self.pk}"


class MaterialIssueLine(models.Model):
    issue = models.ForeignKey(MaterialIssue, on_delete=models.CASCADE, related_name="lines")
    boq_line = models.ForeignKey(
        BOQLine, on_delete=models.PROTECT, null=True, blank=True, related_name="issues"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    quantity = QuantityField()
    unit_cost = MoneyField(help_text=_("Average cost at issue time."))
    value = MoneyField()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.product_id} × {self.quantity}"
