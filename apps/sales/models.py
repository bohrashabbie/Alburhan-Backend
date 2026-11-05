"""Showroom sales: Quotation → Invoice → Receipt (payment) → Delivery note, plus credit notes
for returns. Posting an invoice books the receivable and income; delivering books cost of
sales and takes stock out; a receipt settles invoices; a credit note reverses part of a sale.

Amounts are kept in the document currency with the company-currency rate frozen at posting.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, QuantityField, RateField
from apps.core.models import AuditedModel, CompanyScopedModel

ZERO = Decimal("0")


class SalesSettings(AuditedModel):
    """Per-company defaults for selling."""

    company = models.OneToOneField(
        "organization.Company", on_delete=models.CASCADE, related_name="sales_settings"
    )
    default_tax_rate = models.DecimalField(
        _("default VAT %"),
        max_digits=5,
        decimal_places=2,
        default=ZERO,
        help_text=_("0 in Kuwait, 5 in the UAE, 14 in Egypt."),
    )
    quotation_valid_days = models.PositiveSmallIntegerField(default=30)
    default_payment_terms_days = models.PositiveSmallIntegerField(default=0)
    max_line_discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("100"),
        help_text=_("Discounts above this need the 'approve discounts' permission."),
    )
    invoice_notes = models.TextField(
        blank=True, help_text=_("Printed at the foot of every invoice.")
    )

    class Meta:
        verbose_name_plural = "sales settings"

    def __str__(self):
        return f"Sales settings · {self.company.code}"


class SalesLineMixin(models.Model):
    """A sold line: product, quantity, unit price, discount %, VAT %. Totals are computed and
    stored at save time so lists and reports never recalculate."""

    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    quantity = QuantityField(default=Decimal("1"))
    unit_price = MoneyField()
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    net_amount = MoneyField(help_text=_("After discount, before VAT."))
    tax_amount = MoneyField()
    total_amount = MoneyField()
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["sequence", "id"]

    def compute(self, decimals: int) -> None:
        from apps.core.fields import round_money

        gross = self.quantity * self.unit_price
        net = gross * (Decimal(100) - self.discount_percent) / Decimal(100)
        self.net_amount = round_money(net, decimals)
        self.tax_amount = round_money(self.net_amount * self.tax_rate / Decimal(100), decimals)
        self.total_amount = self.net_amount + self.tax_amount


class SalesDocumentMixin(models.Model):
    """Header fields shared by quotations, invoices and credit notes."""

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField(db_index=True)
    branch = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("The showroom or office that made the sale."),
    )
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="+")
    salesperson = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    currency = models.ForeignKey(
        "organization.Currency", on_delete=models.PROTECT, related_name="+"
    )
    exchange_rate = RateField(default=1)
    reference = models.CharField(max_length=100, blank=True, help_text=_("Customer PO, LPO…"))
    notes = models.TextField(blank=True)
    subtotal = MoneyField()
    tax_total = MoneyField()
    total = MoneyField()

    class Meta:
        abstract = True

    def recompute_totals(self, lines) -> None:
        self.subtotal = sum((line.net_amount for line in lines), ZERO)
        self.tax_total = sum((line.tax_amount for line in lines), ZERO)
        self.total = self.subtotal + self.tax_total


class Quotation(SalesDocumentMixin, CompanyScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent")
        ACCEPTED = "accepted", _("Accepted")
        INVOICED = "invoiced", _("Invoiced")
        EXPIRED = "expired", _("Expired")
        CANCELLED = "cancelled", _("Cancelled")

    valid_until = models.DateField(null=True, blank=True)
    price_list = models.ForeignKey(
        "catalog.PriceList", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    sent_at = models.DateTimeField(null=True, blank=True, editable=False)
    invoice = models.OneToOneField(
        "Invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation",
        editable=False,
    )

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["company", "status", "date"])]

    def __str__(self):
        return self.number or f"Draft quotation #{self.pk}"


class QuotationLine(SalesLineMixin):
    quotation = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name="lines")

    def __str__(self):
        return f"{self.product_id} × {self.quantity}"


class Invoice(SalesDocumentMixin, CompanyScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")  # unpaid
        PARTIALLY_PAID = "partially_paid", _("Partly paid")
        PAID = "paid", _("Paid")
        CANCELLED = "cancelled", _("Cancelled")

    due_date = models.DateField(null=True, blank=True)
    warehouse = models.ForeignKey(
        "organization.Warehouse",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Where deliveries are taken from by default."),
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT)
    amount_paid = MoneyField()
    amount_credited = MoneyField(help_text=_("Total of posted credit notes."))
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    cancelled_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["company", "status", "date"]),
            models.Index(fields=["customer", "status"]),
        ]

    def __str__(self):
        return self.number or f"Draft invoice #{self.pk}"

    @property
    def amount_due(self) -> Decimal:
        return self.total - self.amount_paid - self.amount_credited


class InvoiceLine(SalesLineMixin):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    delivered_quantity = QuantityField()
    returned_quantity = QuantityField()

    def __str__(self):
        return f"{self.product_id} × {self.quantity}"


class DeliveryNote(CompanyScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Delivered")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField()
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="deliveries")
    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    delivered_to = models.CharField(
        max_length=150, blank=True, help_text=_("Name of the person who received the goods.")
    )
    address = models.TextField(blank=True)
    driver = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
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
        return self.number or f"Draft delivery #{self.pk}"


class DeliveryLine(models.Model):
    delivery = models.ForeignKey(DeliveryNote, on_delete=models.CASCADE, related_name="lines")
    invoice_line = models.ForeignKey(
        InvoiceLine, on_delete=models.PROTECT, related_name="deliveries"
    )
    quantity = QuantityField()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["delivery", "invoice_line"], name="uniq_delivery_line")
        ]

    def __str__(self):
        return f"{self.invoice_line_id} × {self.quantity}"


class Receipt(CompanyScopedModel):
    """Money received from a customer, allocated to invoices. Anything unallocated is held as a
    customer advance (down payment) and can be allocated to invoices later."""

    class Method(models.TextChoices):
        CASH = "cash", _("Cash")
        KNET = "knet", _("KNET")
        CARD = "card", _("Card")
        BANK = "bank_transfer", _("Bank transfer")
        CHEQUE = "cheque", _("Cheque")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField(db_index=True)
    branch = models.ForeignKey("organization.Branch", on_delete=models.PROTECT, related_name="+")
    customer = models.ForeignKey("partners.Customer", on_delete=models.PROTECT, related_name="+")
    method = models.CharField(max_length=15, choices=Method.choices, default=Method.CASH)
    journal = models.ForeignKey(
        "accounting.Journal",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Cash or bank journal the money goes into."),
    )
    reference = models.CharField(
        max_length=100, blank=True, help_text=_("Cheque or transaction number.")
    )
    currency = models.ForeignKey(
        "organization.Currency", on_delete=models.PROTECT, related_name="+"
    )
    exchange_rate = RateField(default=1)
    amount = MoneyField()
    allocated_amount = MoneyField()
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
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
        indexes = [models.Index(fields=["company", "status", "date"])]

    def __str__(self):
        return self.number or f"Draft receipt #{self.pk}"

    @property
    def unallocated_amount(self) -> Decimal:
        return self.amount - self.allocated_amount


class ReceiptAllocation(models.Model):
    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount = MoneyField()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["receipt", "invoice"], name="uniq_receipt_invoice")
        ]

    def __str__(self):
        return f"{self.receipt_id} → {self.invoice_id}: {self.amount}"


class CreditNote(SalesDocumentMixin, CompanyScopedModel):
    """A return or a price correction against a posted invoice."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")

    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="credit_notes")
    warehouse = models.ForeignKey(
        "organization.Warehouse",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Where returned goods go back into stock."),
    )
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
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
        return self.number or f"Draft credit note #{self.pk}"


class CreditNoteLine(SalesLineMixin):
    credit_note = models.ForeignKey(CreditNote, on_delete=models.CASCADE, related_name="lines")
    invoice_line = models.ForeignKey(InvoiceLine, on_delete=models.PROTECT, related_name="credits")
    restock = models.BooleanField(
        default=True, help_text=_("Goods came back: put them into Returned stock.")
    )

    def __str__(self):
        return f"{self.product_id} × {self.quantity}"
