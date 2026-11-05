from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.access.services import companies_with
from apps.catalog.models import Product

from .models import (
    CreditNote,
    CreditNoteLine,
    DeliveryLine,
    DeliveryNote,
    Invoice,
    InvoiceLine,
    Quotation,
    QuotationLine,
    Receipt,
    ReceiptAllocation,
    SalesSettings,
)
from .services import SalesError, delivery_status, document_rate, recompute

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]
ZERO = Decimal("0")


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


class SalesSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = SalesSettings
        exclude = ["created_by", "updated_by"]
        read_only_fields = ["company", "created_at", "updated_at"]


# --- Lines -------------------------------------------------------------------------------------


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
        "unit_price",
        "discount_percent",
        "tax_rate",
        "net_amount",
        "tax_amount",
        "total_amount",
    ]
    READ_ONLY = ["id", "net_amount", "tax_amount", "total_amount"]


class QuotationLineSerializer(LineSerializerBase):
    class Meta:
        model = QuotationLine
        fields = LineSerializerBase.LINE_FIELDS
        read_only_fields = LineSerializerBase.READ_ONLY


class InvoiceLineSerializer(LineSerializerBase):
    class Meta:
        model = InvoiceLine
        fields = LineSerializerBase.LINE_FIELDS + ["delivered_quantity", "returned_quantity"]
        read_only_fields = LineSerializerBase.READ_ONLY + [
            "delivered_quantity",
            "returned_quantity",
        ]


class CreditNoteLineSerializer(LineSerializerBase):
    class Meta:
        model = CreditNoteLine
        fields = LineSerializerBase.LINE_FIELDS + ["invoice_line", "restock"]
        read_only_fields = LineSerializerBase.READ_ONLY


# --- Documents with sold lines -------------------------------------------------------------------


class SalesDocumentSerializer(serializers.ModelSerializer):
    """Shared validation for quotations, invoices and credit notes: company consistency, line
    rules, discount ceiling, exchange rate, and computed totals."""

    customer_name = serializers.CharField(source="customer.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    salesperson_name = serializers.SerializerMethodField()
    line_model = None
    line_parent = ""

    def get_salesperson_name(self, doc) -> str | None:
        person = doc.salesperson
        return f"{person.first_name} {person.last_name}".strip() or person.email if person else None

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
        for field in ("branch", "customer", "warehouse", "price_list"):
            if field in attrs or hasattr(self.instance, field):
                _same_company(get(field), company, field)
        salesperson = get("salesperson")
        if salesperson and company.pk not in salesperson.allowed_company_ids():
            raise serializers.ValidationError(
                {"salesperson": "This person has no role in the company."}
            )

        currency = get("currency")
        on_date = get("date")
        if currency is not None and ("currency" in attrs or "date" in attrs or not self.instance):
            if not attrs.get("exchange_rate"):
                try:
                    attrs["exchange_rate"] = document_rate(company, currency.code, on_date)
                except SalesError as exc:
                    raise serializers.ValidationError({"exchange_rate": exc.message}) from exc
        if currency is not None and currency.code == company.currency_id:
            attrs["exchange_rate"] = Decimal(1)

        max_discount = self._max_discount(company)
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
                    {"lines": f"{where}: {product.sku} is sold in whole {product.uom.code}."}
                )
            if (line.get("unit_price") or ZERO) < 0:
                raise serializers.ValidationError(
                    {"lines": f"{where}: the price can't be negative."}
                )
            discount = line.get("discount_percent") or ZERO
            if discount < 0 or discount > 100:
                raise serializers.ValidationError(
                    {"lines": f"{where}: discount must be between 0 and 100%."}
                )
            if discount > max_discount:
                raise serializers.ValidationError(
                    {
                        "lines": f"{where}: discounts above {max_discount}% need approval (permission 'approve discounts')."
                    }
                )
            tax = line.get("tax_rate") or ZERO
            if tax < 0 or tax > 100:
                raise serializers.ValidationError(
                    {"lines": f"{where}: VAT must be between 0 and 100%."}
                )
        return attrs

    def _max_discount(self, company) -> Decimal:
        from .services import _settings

        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and (
            user.is_superuser or company.pk in companies_with(user, "sales.discount.approve")
        ):
            return Decimal(100)
        return _settings(company).max_line_discount_percent

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
    "branch",
    "branch_name",
    "customer",
    "customer_name",
    "salesperson",
    "salesperson_name",
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


class QuotationSerializer(SalesDocumentSerializer):
    line_model = QuotationLine
    line_parent = "quotation"
    lines = QuotationLineSerializer(many=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True, default=None)

    class Meta:
        model = Quotation
        fields = HEADER_FIELDS + [
            "valid_until",
            "price_list",
            "sent_at",
            "invoice",
            "invoice_number",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = HEADER_READ_ONLY + ["sent_at", "invoice"]
        extra_kwargs = {"exchange_rate": {"required": False}}


class InvoiceSerializer(SalesDocumentSerializer):
    line_model = InvoiceLine
    line_parent = "invoice"
    lines = InvoiceLineSerializer(many=True)
    amount_due = serializers.SerializerMethodField()
    delivery_status = serializers.SerializerMethodField()
    quotation_number = serializers.CharField(
        source="quotation.number", read_only=True, default=None
    )
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = Invoice
        fields = HEADER_FIELDS + [
            "due_date",
            "warehouse",
            "amount_paid",
            "amount_credited",
            "amount_due",
            "delivery_status",
            "quotation_number",
            "journal_entry",
            "journal_number",
            "posted_at",
            "cancelled_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = HEADER_READ_ONLY + [
            "amount_paid",
            "amount_credited",
            "journal_entry",
            "posted_at",
            "cancelled_at",
        ]
        extra_kwargs = {"exchange_rate": {"required": False}}

    def get_amount_due(self, invoice) -> str:
        return str(invoice.amount_due)

    def get_delivery_status(self, invoice) -> str:
        return delivery_status(invoice)


class CreditNoteSerializer(SalesDocumentSerializer):
    line_model = CreditNoteLine
    line_parent = "credit_note"
    lines = CreditNoteLineSerializer(many=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = CreditNote
        fields = HEADER_FIELDS + [
            "invoice",
            "invoice_number",
            "warehouse",
            "reason",
            "journal_entry",
            "journal_number",
            "posted_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = HEADER_READ_ONLY + ["journal_entry", "posted_at"]
        extra_kwargs = {"exchange_rate": {"required": False}}

    def validate(self, attrs):
        attrs = super().validate(attrs)
        invoice = attrs.get("invoice", getattr(self.instance, "invoice", None))
        if invoice is not None:
            if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
                raise serializers.ValidationError({"invoice": "Choose a posted invoice."})
            for key in ("customer", "currency", "branch"):
                attrs.setdefault(key, getattr(invoice, key))
            if attrs.get("customer") != invoice.customer:
                raise serializers.ValidationError(
                    {"customer": "The customer must match the invoice."}
                )
            for number, line in enumerate(attrs.get("lines") or [], start=1):
                if line["invoice_line"].invoice_id != invoice.pk:
                    raise serializers.ValidationError(
                        {"lines": f"Line {number}: not a line of this invoice."}
                    )
                if line["product"] != line["invoice_line"].product:
                    raise serializers.ValidationError(
                        {"lines": f"Line {number}: product must match the invoice line."}
                    )
        return attrs


# --- Deliveries ---------------------------------------------------------------------------------


class DeliveryLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="invoice_line.product.sku", read_only=True)
    product_name = serializers.CharField(source="invoice_line.product.name", read_only=True)
    uom = serializers.CharField(source="invoice_line.product.uom.code", read_only=True)
    ordered_quantity = serializers.DecimalField(
        source="invoice_line.quantity", max_digits=18, decimal_places=4, read_only=True
    )
    delivered_before = serializers.DecimalField(
        source="invoice_line.delivered_quantity", max_digits=18, decimal_places=4, read_only=True
    )

    class Meta:
        model = DeliveryLine
        fields = [
            "id",
            "invoice_line",
            "sku",
            "product_name",
            "uom",
            "ordered_quantity",
            "delivered_before",
            "quantity",
        ]
        read_only_fields = ["id"]


class DeliveryNoteSerializer(serializers.ModelSerializer):
    lines = DeliveryLineSerializer(many=True)
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    customer_name = serializers.CharField(source="invoice.customer.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = DeliveryNote
        fields = [
            "id",
            "company",
            "number",
            "date",
            "invoice",
            "invoice_number",
            "customer_name",
            "warehouse",
            "warehouse_name",
            "delivered_to",
            "address",
            "driver",
            "notes",
            "status",
            "journal_entry",
            "journal_number",
            "posted_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["number", "status", "journal_entry", "posted_at", *AUDIT_FIELDS]

    def validate(self, attrs):
        if self.instance and self.instance.status != DeliveryNote.Status.DRAFT:
            raise serializers.ValidationError("Posted delivery notes can't be changed.")
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, invoice, warehouse = get("company"), get("invoice"), get("warehouse")
        _same_company(invoice, company, "invoice")
        _same_company(warehouse, company, "warehouse")
        if invoice is not None and invoice.status in (
            Invoice.Status.DRAFT,
            Invoice.Status.CANCELLED,
        ):
            raise serializers.ValidationError({"invoice": "Post the invoice first."})
        seen = set()
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            if invoice is not None and line["invoice_line"].invoice_id != invoice.pk:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: not a line of this invoice."}
                )
            if line["quantity"] <= 0:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: quantity must be positive."}
                )
            if line["invoice_line"].pk in seen:
                raise serializers.ValidationError({"lines": f"Line {number}: listed twice."})
            seen.add(line["invoice_line"].pk)
        return attrs

    def _save_lines(self, delivery, lines):
        delivery.lines.all().delete()
        DeliveryLine.objects.bulk_create(
            [DeliveryLine(delivery=delivery, **line) for line in lines]
        )

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        delivery = super().create(validated_data)
        self._save_lines(delivery, lines)
        return delivery

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        delivery = super().update(instance, validated_data)
        if lines is not None:
            self._save_lines(delivery, lines)
        return delivery


# --- Receipts -----------------------------------------------------------------------------------


class AllocationSerializer(serializers.ModelSerializer):
    invoice_number = serializers.CharField(source="invoice.number", read_only=True)
    invoice_total = serializers.DecimalField(
        source="invoice.total", max_digits=18, decimal_places=3, read_only=True
    )
    invoice_due = serializers.SerializerMethodField()

    class Meta:
        model = ReceiptAllocation
        fields = ["id", "invoice", "invoice_number", "invoice_total", "invoice_due", "amount"]
        read_only_fields = ["id"]

    def get_invoice_due(self, allocation) -> str:
        return str(allocation.invoice.amount_due)


class ReceiptSerializer(serializers.ModelSerializer):
    allocations = AllocationSerializer(many=True, required=False)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    journal_code = serializers.CharField(source="journal.code", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )
    unallocated_amount = serializers.SerializerMethodField()

    class Meta:
        model = Receipt
        fields = [
            "id",
            "company",
            "number",
            "date",
            "branch",
            "branch_name",
            "customer",
            "customer_name",
            "method",
            "journal",
            "journal_code",
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

    def get_unallocated_amount(self, receipt) -> str:
        return str(receipt.unallocated_amount)

    def validate(self, attrs):
        if self.instance and self.instance.status != Receipt.Status.DRAFT:
            raise serializers.ValidationError("Posted receipts can't be changed.")
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        for field in ("branch", "customer", "journal"):
            _same_company(get(field), company, field)
        journal = get("journal")
        if journal is not None and journal.journal_type not in ("cash", "bank"):
            raise serializers.ValidationError({"journal": "Choose a cash or bank journal."})
        if (get("amount") or ZERO) <= 0:
            raise serializers.ValidationError({"amount": "Enter the amount received."})
        currency = get("currency")
        if (
            currency is not None
            and ("currency" in attrs or "date" in attrs or not self.instance)
            and not attrs.get("exchange_rate")
        ):
            try:
                attrs["exchange_rate"] = document_rate(company, currency.code, get("date"))
            except SalesError as exc:
                raise serializers.ValidationError({"exchange_rate": exc.message}) from exc
        if currency is not None and currency.code == company.currency_id:
            attrs["exchange_rate"] = Decimal(1)
        total = ZERO
        seen = set()
        for number, allocation in enumerate(attrs.get("allocations") or [], start=1):
            invoice = allocation["invoice"]
            if invoice.customer_id != get("customer").pk:
                raise serializers.ValidationError(
                    {
                        "allocations": f"Line {number}: invoice {invoice.number} belongs to another customer."
                    }
                )
            if invoice.pk in seen:
                raise serializers.ValidationError(
                    {"allocations": f"Line {number}: invoice listed twice."}
                )
            seen.add(invoice.pk)
            if allocation["amount"] <= 0:
                raise serializers.ValidationError(
                    {"allocations": f"Line {number}: amount must be positive."}
                )
            total += allocation["amount"]
        if total > (get("amount") or ZERO):
            raise serializers.ValidationError(
                {"allocations": "Allocations exceed the amount received."}
            )
        return attrs

    def _save_allocations(self, receipt, allocations):
        receipt.allocations.all().delete()
        ReceiptAllocation.objects.bulk_create(
            [ReceiptAllocation(receipt=receipt, **item) for item in allocations]
        )

    @transaction.atomic
    def create(self, validated_data):
        allocations = validated_data.pop("allocations", [])
        receipt = super().create(validated_data)
        self._save_allocations(receipt, allocations)
        return receipt

    @transaction.atomic
    def update(self, instance, validated_data):
        allocations = validated_data.pop("allocations", None)
        receipt = super().update(instance, validated_data)
        if allocations is not None:
            self._save_allocations(receipt, allocations)
        return receipt


class DateSerializer(serializers.Serializer):
    date = serializers.DateField()
