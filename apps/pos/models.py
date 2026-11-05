"""Point of sale for the showrooms.

A Register is a till at a branch: it sells from one warehouse at one price list and puts money
into a cash journal (cash) or a bank journal (KNET / card). A cashier opens a Shift with a float,
rings up Sales, and closes the shift by counting the drawer.

Every sale is a normal posted Invoice + Delivery note + Receipt(s) underneath, so the books,
stock and customer statements stay identical to showroom sales made on the Sales screens.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import CompanyScopedModel

ZERO = Decimal("0")


class Register(CompanyScopedModel):
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, related_name="pos_registers"
    )
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True)
    warehouse = models.ForeignKey(
        "organization.Warehouse",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Goods sold here leave this warehouse."),
    )
    price_list = models.ForeignKey(
        "catalog.PriceList",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Empty = the company's default price list."),
    )
    cash_journal = models.ForeignKey(
        "accounting.Journal",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Cash payments go into this journal's account (the drawer)."),
    )
    card_journal = models.ForeignKey(
        "accounting.Journal",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("KNET and card payments go into this journal's account."),
    )
    walk_in_customer = models.ForeignKey(
        "partners.Customer",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Used when the cashier doesn't pick a customer."),
    )
    cash_difference_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Where cash over / short at shift close is booked. Empty = record only."),
    )
    receipt_header = models.TextField(blank=True, help_text=_("Printed at the top of receipts."))
    receipt_footer = models.TextField(
        blank=True, help_text=_("Printed at the bottom, e.g. return policy.")
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_pos_register_code")
        ]

    def __str__(self):
        return f"{self.code} {self.name}"


class Shift(CompanyScopedModel):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")

    register = models.ForeignKey(Register, on_delete=models.PROTECT, related_name="shifts")
    number = models.CharField(max_length=40, db_index=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    opened_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    opened_at = models.DateTimeField()
    opening_cash = MoneyField(help_text=_("Float in the drawer at the start."))
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    closed_at = models.DateTimeField(null=True, blank=True)
    closing_cash = MoneyField(help_text=_("Cash counted at close."))
    expected_cash = MoneyField(help_text=_("Float + cash sales − cash refunds."))
    cash_difference = MoneyField(help_text=_("Counted − expected (negative = short)."))
    sales_count = models.PositiveIntegerField(default=0)
    sales_total = MoneyField()
    refunds_total = MoneyField()
    notes = models.TextField(blank=True)
    difference_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )

    class Meta:
        ordering = ["-opened_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["register"],
                condition=models.Q(status="open"),
                name="one_open_shift_per_register",
            )
        ]

    def __str__(self):
        return self.number


class Sale(CompanyScopedModel):
    class Status(models.TextChoices):
        COMPLETED = "completed", _("Completed")
        VOIDED = "voided", _("Voided")

    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="sales")
    branch = models.ForeignKey("organization.Branch", on_delete=models.PROTECT, related_name="+")
    number = models.CharField(max_length=40, db_index=True, help_text=_("The invoice number."))
    date = models.DateField(db_index=True)
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="+")
    invoice = models.OneToOneField(
        "sales.Invoice", on_delete=models.PROTECT, related_name="pos_sale"
    )
    delivery = models.ForeignKey(
        "sales.DeliveryNote", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    credit_note = models.ForeignKey(
        "sales.CreditNote", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    subtotal = MoneyField()
    tax_total = MoneyField()
    total = MoneyField()
    tendered = MoneyField(help_text=_("Money handed over, before change."))
    change_given = MoneyField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.COMPLETED)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [models.Index(fields=["company", "date"]), models.Index(fields=["shift"])]

    def __str__(self):
        return self.number


class SalePayment(models.Model):
    class Method(models.TextChoices):
        CASH = "cash", _("Cash")
        KNET = "knet", _("KNET")
        CARD = "card", _("Card")

    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="payments")
    method = models.CharField(max_length=10, choices=Method.choices)
    amount = MoneyField(help_text=_("Kept by the shop; negative for a refund."))
    reference = models.CharField(max_length=100, blank=True)
    receipt = models.ForeignKey(
        "sales.Receipt", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("The refund entry, for negative payments."),
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.sale_id} {self.method} {self.amount}"
