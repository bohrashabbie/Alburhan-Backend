"""Customers and suppliers. Each belongs to one company, because receivables and payables are
kept per legal entity. Codes are issued automatically (KW-CUS-00001, KW-SUP-00001)."""

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import AuditedModel, CompanyScopedModel


class Customer(CompanyScopedModel):
    class CustomerType(models.TextChoices):
        INDIVIDUAL = "individual", _("Individual")
        BUSINESS = "business", _("Business")
        CONTRACTOR = "contractor", _("Contractor")
        GOVERNMENT = "government", _("Government")

    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    customer_type = models.CharField(
        max_length=12, choices=CustomerType.choices, default=CustomerType.INDIVIDUAL
    )
    phone = models.CharField(max_length=32, blank=True)
    mobile = models.CharField(max_length=32, blank=True, db_index=True)
    email = models.EmailField(blank=True)
    civil_id = models.CharField(_("civil ID / passport"), max_length=40, blank=True)
    commercial_registration = models.CharField(max_length=60, blank=True)
    tax_number = models.CharField(max_length=60, blank=True)
    address = models.TextField(blank=True)
    area = models.CharField(max_length=80, blank=True, help_text=_("Area / city"))
    credit_limit = MoneyField(validators=[MinValueValidator(0)])
    payment_terms_days = models.PositiveSmallIntegerField(
        default=0, help_text=_("0 = cash / due on invoice.")
    )
    price_list = models.ForeignKey(
        "catalog.PriceList", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_customer_code")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Supplier(CompanyScopedModel):
    code = models.CharField(max_length=30)
    name = models.CharField(max_length=200)
    name_ar = models.CharField(max_length=200, blank=True)
    country = models.ForeignKey(
        "organization.Country", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    currency = models.ForeignKey(
        "organization.Currency",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Currency the supplier invoices in."),
    )
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    address = models.TextField(blank=True)
    tax_number = models.CharField(max_length=60, blank=True)
    payment_terms_days = models.PositiveSmallIntegerField(default=0)
    lead_time_days = models.PositiveSmallIntegerField(
        default=0, help_text=_("Typical days from order to delivery.")
    )
    intercompany = models.ForeignKey(
        "organization.Company",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Set when the supplier is another group company, e.g. MS Lighting China."),
    )
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_supplier_code")
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Contact(AuditedModel):
    """A person at a customer or supplier (exactly one of the two)."""

    customer = models.ForeignKey(
        Customer, on_delete=models.CASCADE, null=True, blank=True, related_name="contacts"
    )
    supplier = models.ForeignKey(
        Supplier, on_delete=models.CASCADE, null=True, blank=True, related_name="contacts"
    )
    name = models.CharField(max_length=120)
    job_title = models.CharField(max_length=80, blank=True)
    phone = models.CharField(max_length=32, blank=True)
    email = models.EmailField(blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_primary", "name"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(customer__isnull=False, supplier__isnull=True)
                    | models.Q(customer__isnull=True, supplier__isnull=False)
                ),
                name="contact_belongs_to_one_partner",
            )
        ]

    def __str__(self):
        return self.name
