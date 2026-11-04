"""Stock: an append-only ledger of movements, running balances per warehouse/product/status,
weighted-average cost per company, and the documents that move stock.

Statuses
    available   on the shelf, can be sold
    in_transit  sent by a transfer, not yet received (shown at the receiving warehouse)
    damaged     kept apart, not for sale
    returned    came back from a customer, waiting for inspection
Reserved stock is not a status: reservations hold part of the available quantity for a project
or order, so  free to sell = available − reserved.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, QuantityField
from apps.core.models import CompanyScopedModel


class StockStatus(models.TextChoices):
    AVAILABLE = "available", _("Available")
    IN_TRANSIT = "in_transit", _("In transit")
    DAMAGED = "damaged", _("Damaged")
    RETURNED = "returned", _("Returned")


class StockBalance(models.Model):
    """Current quantity per warehouse, product and status (kept in step with StockMove)."""

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="balances"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="balances"
    )
    status = models.CharField(max_length=12, choices=StockStatus.choices)
    quantity = QuantityField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["warehouse", "product", "status"], name="uniq_stock_balance"
            ),
            models.CheckConstraint(condition=models.Q(quantity__gte=0), name="stock_not_negative"),
        ]
        indexes = [models.Index(fields=["company", "product"])]

    def __str__(self):
        return f"{self.product_id} @ {self.warehouse_id} [{self.status}]: {self.quantity}"


class ProductCost(models.Model):
    """Weighted-average cost of a product in one company (company currency)."""

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="costs")
    quantity = QuantityField(help_text=_("All statuses, all warehouses of the company."))
    value = MoneyField()
    average_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "product"], name="uniq_product_cost")
        ]

    def __str__(self):
        return f"{self.product_id} avg {self.average_cost}"


class StockMove(models.Model):
    """One change to one balance. Never edited or deleted."""

    class MoveType(models.TextChoices):
        ADJUSTMENT = "adjustment", _("Adjustment")
        COUNT = "count", _("Stock count")
        TRANSFER_OUT = "transfer_out", _("Transfer out")
        TRANSFER_IN = "transfer_in", _("Transfer in")
        RECEIPT = "receipt", _("Goods receipt")
        ISSUE = "issue", _("Goods issue")
        STATUS = "status", _("Status change")

    company = models.ForeignKey("organization.Company", on_delete=models.PROTECT, related_name="+")
    date = models.DateField(db_index=True)
    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="moves")
    status = models.CharField(max_length=12, choices=StockStatus.choices)
    quantity = QuantityField(help_text=_("Positive in, negative out."))
    unit_cost = models.DecimalField(max_digits=18, decimal_places=6, default=0)
    value = MoneyField(help_text=_("Company currency; signed like the quantity."))
    move_type = models.CharField(max_length=14, choices=MoveType.choices)
    reference = models.CharField(max_length=40, blank=True, help_text=_("Document number"))
    source_type = models.CharField(max_length=50, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(fields=["company", "product", "date"]),
            models.Index(fields=["warehouse", "product"]),
            models.Index(fields=["source_type", "source_id"]),
        ]

    def __str__(self):
        return f"{self.move_type} {self.quantity} {self.product_id} @ {self.warehouse_id}"


class Reservation(CompanyScopedModel):
    """Available stock held for a project or order."""

    class Status(models.TextChoices):
        ACTIVE = "active", _("Active")
        RELEASED = "released", _("Released")
        FULFILLED = "fulfilled", _("Fulfilled")

    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    product = models.ForeignKey(
        "catalog.Product", on_delete=models.PROTECT, related_name="reservations"
    )
    quantity = QuantityField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    reference = models.CharField(
        max_length=100, blank=True, help_text=_("e.g. project or order number")
    )
    source_type = models.CharField(max_length=50, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["warehouse", "product", "status"])]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="reservation_qty_positive"
            )
        ]


class ReorderRule(CompanyScopedModel):
    """Keep at least `min_quantity` free in a warehouse; suggest ordering `reorder_quantity`."""

    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    min_quantity = QuantityField()
    reorder_quantity = QuantityField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["warehouse_id", "product__sku"]
        constraints = [
            models.UniqueConstraint(fields=["warehouse", "product"], name="uniq_reorder_rule")
        ]


# --- Documents --------------------------------------------------------------------------------


class DocumentStatus(models.TextChoices):
    DRAFT = "draft", _("Draft")
    POSTED = "posted", _("Posted")


class StockAdjustment(CompanyScopedModel):
    """Add or remove stock: opening balances, write-offs, found items, moving to damaged."""

    class Reason(models.TextChoices):
        OPENING = "opening", _("Opening stock")
        CORRECTION = "correction", _("Correction")
        DAMAGE = "damage", _("Damage / write-off")
        STATUS = "status", _("Status change (e.g. to damaged)")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField()
    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    reason = models.CharField(max_length=12, choices=Reason.choices, default=Reason.CORRECTION)
    counter_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_(
            "Other side of the inventory posting. Empty = the inventory adjustment account."
        ),
    )
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT
    )
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


class StockAdjustmentLine(models.Model):
    adjustment = models.ForeignKey(StockAdjustment, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    status = models.CharField(
        max_length=12, choices=StockStatus.choices, default=StockStatus.AVAILABLE
    )
    quantity = QuantityField(help_text=_("Positive adds stock, negative removes it."))
    unit_cost = models.DecimalField(
        max_digits=18,
        decimal_places=6,
        null=True,
        blank=True,
        help_text=_("For additions; empty = current average cost."),
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.product_id} {self.quantity}"


class StockTransfer(CompanyScopedModel):
    """Transfer Out → In Transit → Transfer In between two warehouses of one company."""

    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        IN_TRANSIT = "in_transit", _("In transit")
        PARTIAL = "partial", _("Partly received")
        RECEIVED = "received", _("Received")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField()
    from_warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    to_warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    note = models.TextField(blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    dispatched_at = models.DateTimeField(null=True, blank=True, editable=False)
    received_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ["-date", "-id"]


class StockTransferLine(models.Model):
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    quantity = QuantityField()
    received_quantity = QuantityField()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.product_id} {self.quantity}"


class StockCount(CompanyScopedModel):
    """Count a warehouse; posting turns the differences into stock and accounting adjustments."""

    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField()
    warehouse = models.ForeignKey(
        "organization.Warehouse", on_delete=models.PROTECT, related_name="+"
    )
    note = models.TextField(blank=True)
    status = models.CharField(
        max_length=10, choices=DocumentStatus.choices, default=DocumentStatus.DRAFT
    )
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


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey("catalog.Product", on_delete=models.PROTECT, related_name="+")
    expected_quantity = QuantityField(help_text=_("Available quantity when the count was posted."))
    counted_quantity = QuantityField()

    class Meta:
        ordering = ["id"]
        constraints = [
            models.UniqueConstraint(fields=["count", "product"], name="uniq_count_product")
        ]

    def __str__(self):
        return f"{self.product_id} {self.counted_quantity}"
