from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.catalog.models import Product
from apps.organization.models import Warehouse

from .models import (
    DocumentStatus,
    ReorderRule,
    Reservation,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
    StockMove,
    StockTransfer,
    StockTransferLine,
)

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


def _check_product(product: Product, where: str):
    if product.product_type != Product.ProductType.STOCK:
        raise serializers.ValidationError({"lines": f"{where}: {product.sku} is not a stock item."})


def _check_quantity(product: Product, quantity: Decimal, where: str, allow_negative=False):
    if quantity == 0:
        raise serializers.ValidationError({"lines": f"{where}: quantity can't be zero."})
    if quantity < 0 and not allow_negative:
        raise serializers.ValidationError({"lines": f"{where}: quantity must be positive."})
    if not product.uom.allow_decimal and quantity != quantity.to_integral_value():
        raise serializers.ValidationError(
            {"lines": f"{where}: {product.sku} is counted in whole {product.uom.code}."}
        )


class LineProductMixin(serializers.Serializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    uom = serializers.CharField(source="product.uom.code", read_only=True)


class DocumentSerializer(serializers.ModelSerializer):
    """Shared behaviour for draft documents with nested lines: drafts only, lines replaced on save."""

    line_model = None
    line_field = "lines"

    def validate(self, attrs):
        if (
            self.instance
            and self.instance.status != DocumentStatus.DRAFT
            and self.instance.status != "draft"
        ):
            raise serializers.ValidationError("Posted documents can't be changed.")
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError(
                {"company": "A document cannot move to another company."}
            )
        return attrs

    def _save_lines(self, document, lines):
        getattr(document, self.line_field).all().delete()
        parent = {
            self.line_model._meta.get_field(
                "adjustment"
                if self.line_model is StockAdjustmentLine
                else "transfer"
                if self.line_model is StockTransferLine
                else "count"
            ).name: document
        }
        self.line_model.objects.bulk_create([self.line_model(**parent, **line) for line in lines])

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop(self.line_field, [])
        document = super().create(validated_data)
        self._save_lines(document, lines)
        return document

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop(self.line_field, None)
        document = super().update(instance, validated_data)
        if lines is not None:
            self._save_lines(document, lines)
        return document


# --- Adjustments ---------------------------------------------------------------------------------


class AdjustmentLineSerializer(LineProductMixin, serializers.ModelSerializer):
    class Meta:
        model = StockAdjustmentLine
        fields = ["id", "product", "sku", "product_name", "uom", "status", "quantity", "unit_cost"]
        read_only_fields = ["id"]


class AdjustmentSerializer(DocumentSerializer):
    line_model = StockAdjustmentLine
    lines = AdjustmentLineSerializer(many=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = StockAdjustment
        fields = [
            "id",
            "company",
            "number",
            "date",
            "warehouse",
            "warehouse_name",
            "reason",
            "counter_account",
            "note",
            "status",
            "journal_entry",
            "journal_number",
            "posted_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["number", "status", "journal_entry", "posted_at", *AUDIT_FIELDS]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        _same_company(get("warehouse"), company, "warehouse")
        _same_company(get("counter_account"), company, "counter_account")
        account = get("counter_account")
        if account is not None and account.is_group:
            raise serializers.ValidationError({"counter_account": "Choose a detail account."})
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            where = f"Line {number}"
            _check_product(line["product"], where)
            _check_quantity(line["product"], line["quantity"], where, allow_negative=True)
            if line.get("unit_cost") is not None and line["unit_cost"] < 0:
                raise serializers.ValidationError(
                    {"lines": f"{where}: unit cost can't be negative."}
                )
        return attrs


# --- Transfers -----------------------------------------------------------------------------------


class TransferLineSerializer(LineProductMixin, serializers.ModelSerializer):
    class Meta:
        model = StockTransferLine
        fields = ["id", "product", "sku", "product_name", "uom", "quantity", "received_quantity"]
        read_only_fields = ["id", "received_quantity"]


class TransferSerializer(DocumentSerializer):
    line_model = StockTransferLine
    lines = TransferLineSerializer(many=True)
    from_warehouse_name = serializers.CharField(source="from_warehouse.name", read_only=True)
    to_warehouse_name = serializers.CharField(source="to_warehouse.name", read_only=True)

    class Meta:
        model = StockTransfer
        fields = [
            "id",
            "company",
            "number",
            "date",
            "from_warehouse",
            "from_warehouse_name",
            "to_warehouse",
            "to_warehouse_name",
            "note",
            "status",
            "dispatched_at",
            "received_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["number", "status", "dispatched_at", "received_at", *AUDIT_FIELDS]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, source, target = get("company"), get("from_warehouse"), get("to_warehouse")
        _same_company(source, company, "from_warehouse")
        _same_company(target, company, "to_warehouse")
        if source is not None and source == target:
            raise serializers.ValidationError({"to_warehouse": "Choose a different warehouse."})
        if target is not None and target.warehouse_type == Warehouse.WarehouseType.TRANSIT:
            raise serializers.ValidationError(
                {"to_warehouse": "Transit is tracked automatically; choose the real destination."}
            )
        seen = set()
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            where = f"Line {number}"
            _check_product(line["product"], where)
            _check_quantity(line["product"], line["quantity"], where)
            if line["product"].pk in seen:
                raise serializers.ValidationError(
                    {"lines": f"{where}: {line['product'].sku} is listed twice."}
                )
            seen.add(line["product"].pk)
        return attrs


class ReceiveSerializer(serializers.Serializer):
    date = serializers.DateField()
    quantities = serializers.DictField(
        child=serializers.DecimalField(max_digits=18, decimal_places=4, min_value=Decimal(0)),
        help_text="{line id: quantity received now}",
    )


# --- Counts --------------------------------------------------------------------------------------


class CountLineSerializer(LineProductMixin, serializers.ModelSerializer):
    class Meta:
        model = StockCountLine
        fields = [
            "id",
            "product",
            "sku",
            "product_name",
            "uom",
            "expected_quantity",
            "counted_quantity",
        ]
        read_only_fields = ["id", "expected_quantity"]


class CountSerializer(DocumentSerializer):
    line_model = StockCountLine
    lines = CountLineSerializer(many=True, required=False)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = StockCount
        fields = [
            "id",
            "company",
            "number",
            "date",
            "warehouse",
            "warehouse_name",
            "note",
            "status",
            "journal_entry",
            "journal_number",
            "posted_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["number", "status", "journal_entry", "posted_at", *AUDIT_FIELDS]

    def validate(self, attrs):
        attrs = super().validate(attrs)
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        _same_company(get("warehouse"), get("company"), "warehouse")
        seen = set()
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            where = f"Line {number}"
            _check_product(line["product"], where)
            if line["counted_quantity"] < 0:
                raise serializers.ValidationError(
                    {"lines": f"{where}: counted quantity can't be negative."}
                )
            if (
                not line["product"].uom.allow_decimal
                and line["counted_quantity"] != line["counted_quantity"].to_integral_value()
            ):
                raise serializers.ValidationError(
                    {"lines": f"{where}: whole {line['product'].uom.code} only."}
                )
            if line["product"].pk in seen:
                raise serializers.ValidationError(
                    {"lines": f"{where}: {line['product'].sku} is listed twice."}
                )
            seen.add(line["product"].pk)
        return attrs

    def _save_lines(self, document, lines):
        # Keep the expected quantity the engine will fill at posting; the UI shows the current one.
        from .services import available_quantity

        document.lines.all().delete()
        StockCountLine.objects.bulk_create(
            [
                StockCountLine(
                    count=document,
                    expected_quantity=available_quantity(document.warehouse, line["product"]),
                    **line,
                )
                for line in lines
            ]
        )


# --- Reservations, rules, moves ----------------------------------------------------------------


class ReservationSerializer(LineProductMixin, serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = Reservation
        fields = [
            "id",
            "company",
            "warehouse",
            "warehouse_name",
            "product",
            "sku",
            "product_name",
            "uom",
            "quantity",
            "status",
            "reference",
            "source_type",
            "source_id",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["status", "source_type", "source_id", *AUDIT_FIELDS]

    def validate(self, attrs):
        if self.instance:
            raise serializers.ValidationError(
                "Reservations can't be edited; release it and make a new one."
            )
        _same_company(attrs.get("warehouse"), attrs.get("company"), "warehouse")
        _check_product(attrs["product"], "Product")
        _check_quantity(attrs["product"], attrs["quantity"], "Quantity")
        return attrs


class ReorderRuleSerializer(LineProductMixin, serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = ReorderRule
        fields = [
            "id",
            "company",
            "warehouse",
            "warehouse_name",
            "product",
            "sku",
            "product_name",
            "uom",
            "min_quantity",
            "reorder_quantity",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        validators = []
        # A boolean omitted from form data would otherwise read as False.
        extra_kwargs = {"is_active": {"default": True}}

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        _same_company(get("warehouse"), get("company"), "warehouse")
        _check_product(get("product"), "Product")
        if get("min_quantity") < 0 or get("reorder_quantity") < 0:
            raise serializers.ValidationError({"min_quantity": "Quantities can't be negative."})
        taken = ReorderRule.objects.filter(
            warehouse=get("warehouse"), product=get("product")
        ).exclude(pk=getattr(self.instance, "pk", None))
        if taken.exists():
            raise serializers.ValidationError(
                {"product": "This product already has a rule in that warehouse."}
            )
        return attrs


class MoveSerializer(LineProductMixin, serializers.ModelSerializer):
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)

    class Meta:
        model = StockMove
        fields = [
            "id",
            "date",
            "warehouse",
            "warehouse_name",
            "product",
            "sku",
            "product_name",
            "uom",
            "status",
            "quantity",
            "unit_cost",
            "value",
            "move_type",
            "reference",
            "source_type",
            "source_id",
            "note",
            "created_at",
            "created_by",
        ]
