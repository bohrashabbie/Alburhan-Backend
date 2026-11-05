"""Purchasing: Purchase order → Goods receipt (stock in, at landed cost) → Supplier invoice
(bill) → Supplier payment. Receiving books inventory against "Goods received not invoiced";
the bill clears it and books the payable; the payment settles the bill.

Orders and bills are in the supplier's currency with the company-currency rate frozen on posting.
"""

from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, QuantityField, RateField
from apps.core.models import CompanyScopedModel

ZERO = Decimal("0")


class PurchaseLineMixin(models.Model):
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    description = models.CharField(max_length=255, blank=True)
    quantity = QuantityField(default=Decimal("1"))
    unit_cost = MoneyField()
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=ZERO)
    net_amount = MoneyField()
    tax_amount = MoneyField()
    total_amount = MoneyField()
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        abstract = True
        ordering = ["sequence", "id"]

    def compute(self, decimals: int) -> None:
        from apps.core.fields import round_money

        gross = self.quantity * self.unit_cost
        net = gross * (Decimal(100) - self.discount_percent) / Decimal(100)
        self.net_amount = round_money(net, decimals)
        self.tax_amount = round_money(self.net_amount * self.tax_rate / Decimal(100), decimals)
        self.total_amount = self.net_amount + self.tax_amount

    @property
    def unit_net_cost(self) -> Decimal:
        """Cost of one unit after the line discount, in the document currency."""
        return self.unit_cost * (Decimal(100) - self.discount_percent) / Decimal(100)


class PurchaseDocumentMixin(models.Model):
    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField(db_index=True)
    supplier = models.ForeignKey("partners.Supplier", on_delete=models.PROTECT, related_name="+")
    currency = models.ForeignKey(
        "organization.Currency", on_delete=models.PROTECT, related_name="+"
    )
    exchange_rate = RateField(default=1)
    reference = models.CharField(max_length=100, blank=True)
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


class PurchaseOrder(PurchaseDocumentMixin, CompanyScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        SENT = "sent", _("Sent")
        PARTIAL = "partial", _("Partly received")
        RECEIVED = "received", _("Received")
        CLOSED = "closed", _("Closed")
        CANCELLED = "cancelled", _("Cancelled")

    branch = models.ForeignKey(
        "organization.Branch",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Who is buying: a showroom, the projects department or head office."),
    )
    warehouse = models.ForeignKey(
        "organization.Warehouse",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Where the goods will be received."),
    )
    expected_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    sent_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["company", "status", "date"])]

    def __str__(self):
        return self.number or f"Draft order #{self.pk}"


class PurchaseOrderLine(PurchaseLineMixin):
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    received_quantity = QuantityField()
    invoiced_quantity = QuantityField()

    def __str__(self):
        return f"{self.product_id} × {self.quantity}"


class GoodsReceipt(CompanyScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Received")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField(db_index=True)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.PROTECT, related_name="receipts")
    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    supplier_reference = models.CharField(
        max_length=100, blank=True, help_text=_("Supplier's delivery note or packing list.")
    )
    freight_cost = MoneyField(
        help_text=_("Shipping, in the company currency; spread over the lines.")
    )
    customs_cost = MoneyField(help_text=_("Customs and clearance, in the company currency."))
    notes = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    goods_value = MoneyField(help_text=_("Order value of the goods, in the company currency."))
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
        return self.number or f"Draft receipt #{self.pk}"


class GoodsReceiptLine(models.Model):
    receipt = models.ForeignKey(GoodsReceipt, on_delete=models.CASCADE, related_name="lines")
    order_line = models.ForeignKey(
        PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipts"
    )
    quantity = QuantityField()
    unit_cost = MoneyField(help_text=_("Landed cost per unit in the company currency."))
    goods_value = MoneyField(help_text=_("Order cost × quantity, company currency (the GRNI)."))
    invoiced_quantity = QuantityField()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(
                fields=["receipt", "order_line"], name="uniq_receipt_order_line"
            )
        ]

    def __str__(self):
        return f"{self.order_line_id} × {self.quantity}"


class SupplierInvoice(PurchaseDocumentMixin, CompanyScopedModel):
    """A supplier's bill. Lines tied to a goods receipt clear GRNI; other lines go to the
    product's expense account."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Unpaid")
        PARTIALLY_PAID = "partially_paid", _("Partly paid")
        PAID = "paid", _("Paid")
        CANCELLED = "cancelled", _("Cancelled")

    supplier_number = models.CharField(
        max_length=60, blank=True, help_text=_("The number printed on the supplier's invoice.")
    )
    due_date = models.DateField(null=True, blank=True)
    order = models.ForeignKey(
        PurchaseOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="invoices"
    )
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.DRAFT)
    amount_paid = MoneyField()
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
            models.Index(fields=["supplier", "status"]),
        ]

    def __str__(self):
        return self.number or f"Draft bill #{self.pk}"

    @property
    def amount_due(self) -> Decimal:
        return self.total - self.amount_paid


class SupplierInvoiceLine(PurchaseLineMixin):
    invoice = models.ForeignKey(SupplierInvoice, on_delete=models.CASCADE, related_name="lines")
    receipt_line = models.ForeignKey(
        GoodsReceiptLine,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="invoice_lines",
        help_text=_("The goods receipt this line bills."),
    )
    account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Expense account for lines without a goods receipt."),
    )

    def __str__(self):
        return f"{self.product_id} × {self.quantity}"


class SupplierPayment(CompanyScopedModel):
    class Method(models.TextChoices):
        BANK = "bank_transfer", _("Bank transfer")
        CHEQUE = "cheque", _("Cheque")
        CASH = "cash", _("Cash")
        LC = "letter_of_credit", _("Letter of credit")

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField(db_index=True)
    supplier = models.ForeignKey("partners.Supplier", on_delete=models.PROTECT, related_name="+")
    method = models.CharField(max_length=20, choices=Method.choices, default=Method.BANK)
    journal = models.ForeignKey(
        "accounting.Journal",
        on_delete=models.PROTECT,
        related_name="+",
        help_text=_("Bank or cash journal the money leaves from."),
    )
    reference = models.CharField(
        max_length=100, blank=True, help_text=_("Transfer or cheque number.")
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
        return self.number or f"Draft payment #{self.pk}"

    @property
    def unallocated_amount(self) -> Decimal:
        return self.amount - self.allocated_amount


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(
        SupplierPayment, on_delete=models.CASCADE, related_name="allocations"
    )
    invoice = models.ForeignKey(
        SupplierInvoice, on_delete=models.PROTECT, related_name="allocations"
    )
    amount = MoneyField()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["payment", "invoice"], name="uniq_payment_bill")
        ]

    def __str__(self):
        return f"{self.payment_id} → {self.invoice_id}: {self.amount}"
