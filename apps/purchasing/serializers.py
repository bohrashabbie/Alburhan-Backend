from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.catalog.models import Product

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PaymentAllocation,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierPayment,
)
from .services import PurchaseError, document_rate, recompute

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]
ZERO = Decimal("0")


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


class LineSerializerBase(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    uom = serializers.CharField(source="product.uom.code", read_only=True)
    product_type = serializers.CharField(source="product.product_type", read_only=True)

    LINE_FIELDS = [
        "id",
        "product",
        "sku",
        "product_name",
        "uom",
        "product_type",
        "description",
        "quantity",
        "unit_cost",
        "discount_percent",
        "tax_rate",
        "net_amount",
        "tax_amount",
        "total_amount",
    ]
    READ_ONLY = ["id", "net_amount", "tax_amount", "total_amount"]


class OrderLineSerializer(LineSerializerBase):
    class Meta:
        model = PurchaseOrderLine
        fields = LineSerializerBase.LINE_FIELDS + ["received_quantity", "invoiced_quantity"]
        read_only_fields = LineSerializerBase.READ_ONLY + ["received_quantity", "invoiced_quantity"]


class BillLineSerializer(LineSerializerBase):
    receipt_number = serializers.CharField(
        source="receipt_line.receipt.number", read_only=True, default=None
    )
    account_code = serializers.CharField(source="account.code", read_only=True, default=None)

    class Meta:
        model = SupplierInvoiceLine
        fields = LineSerializerBase.LINE_FIELDS + [
            "receipt_line",
            "receipt_number",
            "account",
            "account_code",
        ]
        read_only_fields = LineSerializerBase.READ_ONLY


class PurchaseDocumentSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    line_model = None
    line_parent = ""

    def _check_editable(self):
        if self.instance and self.instance.status != "draft":
            raise serializers.ValidationError("Only drafts can be changed.")

    def validate(self, attrs):
        self._check_editable()
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError(
                {"company": "A document cannot move to another company."}
            )
        for field in ("supplier", "branch", "warehouse", "order"):
            if field in attrs or hasattr(self.instance, field):
                _same_company(get(field), company, field)
        currency = get("currency")
        on_date = get("date")
        if currency is not None and ("currency" in attrs or "date" in attrs or not self.instance):
            if not attrs.get("exchange_rate"):
                try:
                    attrs["exchange_rate"] = document_rate(company, currency.code, on_date)
                except PurchaseError as exc:
                    raise serializers.ValidationError({"exchange_rate": exc.message}) from exc
        if currency is not None and currency.code == company.currency_id:
            attrs["exchange_rate"] = Decimal(1)
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            where = f"Line {number}"
            product: Product = line["product"]
            if not product.is_active:
                raise serializers.ValidationError({"lines": f"{where}: {product.sku} is inactive."})
            quantity = line.get("quantity", Decimal(1))
            if quantity <= 0:
                raise serializers.ValidationError({"lines": f"{where}: quantity must be positive."})
            if not product.uom.allow_decimal and quantity != quantity.to_integral_value():
                raise serializers.ValidationError(
                    {"lines": f"{where}: {product.sku} is bought in whole {product.uom.code}."}
                )
            if (line.get("unit_cost") or ZERO) < 0:
                raise serializers.ValidationError(
                    {"lines": f"{where}: the cost can't be negative."}
                )
            discount = line.get("discount_percent") or ZERO
            if discount < 0 or discount > 100:
                raise serializers.ValidationError(
                    {"lines": f"{where}: discount must be between 0 and 100%."}
                )
            tax = line.get("tax_rate") or ZERO
            if tax < 0 or tax > 100:
                raise serializers.ValidationError(
                    {"lines": f"{where}: VAT must be between 0 and 100%."}
                )
            account = line.get("account")
            if account is not None:
                _same_company(account, company, "lines")
                if account.is_group:
                    raise serializers.ValidationError(
                        {"lines": f"{where}: choose a detail account."}
                    )
        return attrs

    def _save_lines(self, document, lines):
        document.lines.all().delete()
        objects = [
            self.line_model(**{self.line_parent: document}, sequence=index, **line)
            for index, line in enumerate(lines)
        ]
        recompute(document, objects)
        self.line_model.objects.bulk_create(objects)
        document.save(update_fields=["subtotal", "tax_total", "total", "updated_at"])

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        document = super().create(validated_data)
        self._save_lines(document, lines)
        return document

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        document = super().update(instance, validated_data)
        if lines is not None:
            self._save_lines(document, lines)
        else:
            recompute(document, list(document.lines.all()))
            document.save(update_fields=["subtotal", "tax_total", "total", "updated_at"])
        return document


HEADER_FIELDS = [
    "id",
    "company",
    "number",
    "date",
    "supplier",
    "supplier_name",
    "currency",
    "exchange_rate",
    "reference",
    "notes",
    "subtotal",
    "tax_total",
    "total",
    "status",
]
HEADER_READ_ONLY = ["number", "subtotal", "tax_total", "total", "status", *AUDIT_FIELDS]


class PurchaseOrderSerializer(PurchaseDocumentSerializer):
    line_model = PurchaseOrderLine
    line_parent = "order"
    lines = OrderLineSerializer(many=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    supplier_currency = serializers.CharField(source="supplier.currency_id", read_only=True)
    intercompany = serializers.SerializerMethodField()
    receipt_status = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseOrder
        fields = HEADER_FIELDS + [
            "branch",
            "branch_name",
            "warehouse",
            "warehouse_name",
            "supplier_currency",
            "expected_date",
            "intercompany",
            "receipt_status",
            "sent_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = HEADER_READ_ONLY + ["sent_at"]
        extra_kwargs = {"exchange_rate": {"required": False}}

    def get_intercompany(self, order) -> bool:
        return order.supplier.intercompany_id is not None

    def get_receipt_status(self, order) -> str:
        lines = list(order.lines.all())
        ordered = sum((ln.quantity for ln in lines), ZERO)
        received = sum((ln.received_quantity for ln in lines), ZERO)
        if received <= 0:
            return "pending"
        return "received" if received >= ordered else "partial"


class ReceiptLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="order_line.product.sku", read_only=True)
    product_name = serializers.CharField(source="order_line.product.name", read_only=True)
    uom = serializers.CharField(source="order_line.product.uom.code", read_only=True)
    ordered_quantity = serializers.DecimalField(
        source="order_line.quantity", max_digits=18, decimal_places=4, read_only=True
    )
    received_before = serializers.DecimalField(
        source="order_line.received_quantity", max_digits=18, decimal_places=4, read_only=True
    )

    class Meta:
        model = GoodsReceiptLine
        fields = [
            "id",
            "order_line",
            "sku",
            "product_name",
            "uom",
            "ordered_quantity",
            "received_before",
            "quantity",
            "unit_cost",
            "goods_value",
            "invoiced_quantity",
        ]
        read_only_fields = ["id", "unit_cost", "goods_value", "invoiced_quantity"]


class GoodsReceiptSerializer(serializers.ModelSerializer):
    lines = ReceiptLineSerializer(many=True)
    order_number = serializers.CharField(source="order.number", read_only=True)
    supplier_name = serializers.CharField(source="order.supplier.name", read_only=True)
    supplier = serializers.IntegerField(source="order.supplier_id", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = GoodsReceipt
        fields = [
            "id",
            "company",
            "number",
            "date",
            "order",
            "order_number",
            "supplier",
            "supplier_name",
            "warehouse",
            "warehouse_name",
            "supplier_reference",
            "freight_cost",
            "customs_cost",
            "goods_value",
            "notes",
            "status",
            "journal_entry",
            "journal_number",
            "posted_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = [
            "number",
            "status",
            "goods_value",
            "journal_entry",
            "posted_at",
            *AUDIT_FIELDS,
        ]

    def validate(self, attrs):
        if self.instance and self.instance.status != GoodsReceipt.Status.DRAFT:
            raise serializers.ValidationError("Posted receipts can't be changed.")
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, order, warehouse = get("company"), get("order"), get("warehouse")
        _same_company(order, company, "order")
        _same_company(warehouse, company, "warehouse")
        if order is not None and order.status not in (
            PurchaseOrder.Status.SENT,
            PurchaseOrder.Status.PARTIAL,
        ):
            raise serializers.ValidationError({"order": "Send the order first."})
        seen = set()
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            if order is not None and line["order_line"].order_id != order.pk:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: not a line of this order."}
                )
            if line["quantity"] <= 0:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: quantity must be positive."}
                )
            if line["order_line"].pk in seen:
                raise serializers.ValidationError({"lines": f"Line {number}: listed twice."})
            seen.add(line["order_line"].pk)
        return attrs

    def _save_lines(self, receipt, lines):
        receipt.lines.all().delete()
        GoodsReceiptLine.objects.bulk_create(
            [GoodsReceiptLine(receipt=receipt, **line) for line in lines]
        )

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        receipt = super().create(validated_data)
        self._save_lines(receipt, lines)
        return receipt

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        receipt = super().update(instance, validated_data)
        if lines is not None:
            self._save_lines(receipt, lines)
        return receipt


class SupplierInvoiceSerializer(PurchaseDocumentSerializer):
    line_model = SupplierInvoiceLine
    line_parent = "invoice"
    lines = BillLineSerializer(many=True)
    amount_due = serializers.SerializerMethodField()
    order_number = serializers.CharField(source="order.number", read_only=True, default=None)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = SupplierInvoice
        fields = HEADER_FIELDS + [
            "supplier_number",
            "due_date",
            "order",
            "order_number",
            "amount_paid",
            "amount_due",
            "journal_entry",
            "journal_number",
            "posted_at",
            "cancelled_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = HEADER_READ_ONLY + [
            "amount_paid",
            "journal_entry",
            "posted_at",
            "cancelled_at",
        ]
        extra_kwargs = {"exchange_rate": {"required": False}}

    def get_amount_due(self, invoice) -> str:
        return str(invoice.amount_due)


class AllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    supplier_number = serializers.CharField(source="invoice.supplier_number", read_only=True)

    class Meta:
        model = PaymentAllocation
        fields = ["id", "invoice", "invoice_number", "supplier_number", "amount"]
        read_only_fields = ["id"]


class SupplierPaymentSerializer(serializers.ModelSerializer):
    allocations = AllocationSerializer(many=True, required=False)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)
    journal_name = serializers.CharField(source="journal.name", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )
    unallocated_amount = serializers.SerializerMethodField()

    class Meta:
        model = SupplierPayment
        fields = [
            "id",
            "company",
            "number",
            "date",
            "supplier",
            "supplier_name",
            "method",
            "journal",
            "journal_name",
            "reference",
            "currency",
            "exchange_rate",
            "amount",
            "allocated_amount",
            "unallocated_amount",
            "notes",
            "status",
            "journal_entry",
            "journal_number",
            "posted_at",
            "allocations",
            *AUDIT_FIELDS,
        ]
        read_only_fields = [
            "number",
            "allocated_amount",
            "status",
            "journal_entry",
            "posted_at",
            *AUDIT_FIELDS,
        ]
        extra_kwargs = {"exchange_rate": {"required": False}}

    def get_unallocated_amount(self, payment) -> str:
        return str(payment.amount - sum((a.amount for a in payment.allocations.all()), ZERO))

    def validate(self, attrs):
        if self.instance and self.instance.status != SupplierPayment.Status.DRAFT:
            raise serializers.ValidationError("Posted payments can't be changed.")
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        _same_company(get("supplier"), company, "supplier")
        _same_company(get("journal"), company, "journal")
        journal = get("journal")
        if journal is not None and journal.journal_type not in ("cash", "bank"):
            raise serializers.ValidationError({"journal": "Choose a cash or bank journal."})
        currency = get("currency")
        on_date = get("date")
        if currency is not None and ("currency" in attrs or "date" in attrs or not self.instance):
            if not attrs.get("exchange_rate"):
                try:
                    attrs["exchange_rate"] = document_rate(company, currency.code, on_date)
                except PurchaseError as exc:
                    raise serializers.ValidationError({"exchange_rate": exc.message}) from exc
        if currency is not None and currency.code == company.currency_id:
            attrs["exchange_rate"] = Decimal(1)
        if (attrs.get("amount") or getattr(self.instance, "amount", ZERO)) < 0:
            raise serializers.ValidationError({"amount": "The amount can't be negative."})
        seen = set()
        for allocation in attrs.get("allocations") or []:
            if allocation["invoice"].pk in seen:
                raise serializers.ValidationError({"allocations": "A bill is listed twice."})
            seen.add(allocation["invoice"].pk)
            if allocation["amount"] <= 0:
                raise serializers.ValidationError({"allocations": "Allocations must be positive."})
        return attrs

    def _save_allocations(self, payment, allocations):
        payment.allocations.all().delete()
        PaymentAllocation.objects.bulk_create(
            [PaymentAllocation(payment=payment, **item) for item in allocations]
        )

    @transaction.atomic
    def create(self, validated_data):
        allocations = validated_data.pop("allocations", [])
        payment = super().create(validated_data)
        self._save_allocations(payment, allocations)
        return payment

    @transaction.atomic
    def update(self, instance, validated_data):
        allocations = validated_data.pop("allocations", None)
        payment = super().update(instance, validated_data)
        if allocations is not None:
            self._save_allocations(payment, allocations)
        return payment


class DateSerializer(serializers.Serializer):
    date = serializers.DateField()
