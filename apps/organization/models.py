"""Group structure: Country company -> Branch (showroom, office, ...) -> Warehouse.

Countries are data, not separate deployments: Kuwait, UAE, China and Egypt are each a `Company`
(legal entity) with its own functional currency, while the group reports in ERP_BASE_CURRENCY.
"""

from pathlib import Path
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import RateField
from apps.core.models import AuditedModel


class Currency(models.Model):
    code = models.CharField(_("ISO code"), max_length=3, primary_key=True)
    name = models.CharField(max_length=64)
    name_ar = models.CharField(max_length=64, blank=True)
    symbol = models.CharField(max_length=8, blank=True)
    decimal_places = models.PositiveSmallIntegerField(
        default=2, help_text=_("Minor units, e.g. 3 for KWD, 2 for AED.")
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "currencies"

    def __str__(self):
        return self.code


class Country(models.Model):
    code = models.CharField(_("ISO code"), max_length=2, primary_key=True)
    name = models.CharField(max_length=64)
    name_ar = models.CharField(max_length=64, blank=True)
    default_currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "countries"

    def __str__(self):
        return self.name


def company_logo_path(instance, filename):
    """companies/<id>/logo-<random>.png: a new name per upload, so cached links never go stale."""
    return f"companies/{instance.pk}/logo-{uuid4().hex[:10]}{Path(filename).suffix.lower()}"


class Company(AuditedModel):
    """A legal entity in one country. All business documents belong to exactly one company."""

    code = models.CharField(
        max_length=10, unique=True, help_text=_("Short code used in document numbers, e.g. KW.")
    )
    name = models.CharField(max_length=128)
    name_ar = models.CharField(max_length=128, blank=True)
    legal_name = models.CharField(max_length=255, blank=True)
    country = models.ForeignKey(Country, on_delete=models.PROTECT, related_name="companies")
    currency = models.ForeignKey(
        Currency,
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Functional currency the company keeps its books in."),
    )
    timezone = models.CharField(max_length=64, default="Asia/Kuwait")
    tax_registration_number = models.CharField(max_length=64, blank=True)
    commercial_registration_number = models.CharField(max_length=64, blank=True)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    logo = models.ImageField(upload_to=company_logo_path, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["code"]
        verbose_name_plural = "companies"

    def __str__(self):
        return f"{self.code} - {self.name}"


class Branch(AuditedModel):
    class BranchType(models.TextChoices):
        HEAD_OFFICE = "head_office", _("Head office")
        SHOWROOM = "showroom", _("Showroom")
        PROJECTS = "projects", _("Projects office")
        WAREHOUSE = "warehouse", _("Warehouse site")
        FACTORY = "factory", _("Factory")
        OFFICE = "office", _("Office")

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="branches")
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=128)
    name_ar = models.CharField(max_length=128, blank=True)
    branch_type = models.CharField(max_length=20, choices=BranchType.choices)
    address = models.TextField(blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "code"]
        verbose_name_plural = "branches"
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_branch_code_per_company")
        ]

    def __str__(self):
        return f"{self.company.code}/{self.code} - {self.name}"


class Warehouse(AuditedModel):
    """A stock-holding place. Stock status buckets (available, reserved, damaged, returned) are
    tracked per warehouse by the inventory module; `TRANSIT` warehouses hold goods in transfer."""

    class WarehouseType(models.TextChoices):
        MAIN = "main", _("Main warehouse")
        SHOWROOM = "showroom", _("Showroom stock")
        TRANSIT = "transit", _("In transit")
        PROJECT_SITE = "project_site", _("Project site")

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="warehouses")
    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, null=True, blank=True, related_name="warehouses"
    )
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=128)
    name_ar = models.CharField(max_length=128, blank=True)
    warehouse_type = models.CharField(max_length=20, choices=WarehouseType.choices)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "code"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="uniq_warehouse_code_per_company"
            )
        ]

    def __str__(self):
        return f"{self.company.code}/{self.code} - {self.name}"

    def clean(self):
        if self.branch_id and self.company_id and self.branch.company_id != self.company_id:
            raise ValidationError({"branch": _("Branch must belong to the same company.")})


class ExchangeRate(AuditedModel):
    """1 unit of `currency` = `rate` units of `to_currency`, effective from `effective_date`."""

    currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    to_currency = models.ForeignKey(Currency, on_delete=models.PROTECT, related_name="+")
    rate = RateField()
    effective_date = models.DateField(db_index=True)
    source = models.CharField(max_length=64, blank=True, help_text=_("e.g. Central Bank, manual"))

    class Meta:
        ordering = ["-effective_date", "currency_id", "to_currency_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["currency", "to_currency", "effective_date"],
                name="uniq_exchange_rate_per_day",
            ),
            models.CheckConstraint(condition=models.Q(rate__gt=0), name="exchange_rate_positive"),
            models.CheckConstraint(
                condition=~models.Q(currency=models.F("to_currency")),
                name="exchange_rate_distinct_currencies",
            ),
        ]

    def __str__(self):
        return f"1 {self.currency_id} = {self.rate} {self.to_currency_id} ({self.effective_date})"


class DocumentSequence(AuditedModel):
    """Gap-free numbering per company and document type, e.g. KW-INV-2026-00001.

    Modules use their own `doc_type` codes (QT, INV, DN, SO, PO, GRN, TRF, ...). Numbers are issued
    by `apps.organization.services.next_document_number`, which locks the row.
    """

    company = models.ForeignKey(Company, on_delete=models.PROTECT, related_name="sequences")
    doc_type = models.CharField(max_length=10)
    prefix = models.CharField(max_length=20, help_text=_("e.g. KW-INV-"))
    padding = models.PositiveSmallIntegerField(default=5)
    reset_yearly = models.BooleanField(default=True)
    current_year = models.PositiveIntegerField(null=True, blank=True, editable=False)
    next_number = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["company__code", "doc_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "doc_type"], name="uniq_sequence_per_company_doc_type"
            )
        ]

    def __str__(self):
        return f"{self.company.code} {self.doc_type}"
