from decimal import Decimal

from rest_framework import serializers

from apps.accounting.models import Journal
from apps.catalog.models import Product
from apps.partners.models import Customer

from .models import Register, Sale, SalePayment, Shift
from .services import shift_totals

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


class RegisterSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    price_list_name = serializers.CharField(source="price_list.name", read_only=True, default=None)
    cash_journal_name = serializers.CharField(source="cash_journal.name", read_only=True)
    card_journal_name = serializers.CharField(source="card_journal.name", read_only=True)
    walk_in_customer_name = serializers.CharField(source="walk_in_customer.name", read_only=True)
    walk_in_customer_name_ar = serializers.CharField(
        source="walk_in_customer.name_ar", read_only=True
    )
    open_shift = serializers.SerializerMethodField()

    class Meta:
        model = Register
        fields = [
            "id",
            "company",
            "branch",
            "branch_name",
            "code",
            "name",
            "name_ar",
            "warehouse",
            "warehouse_name",
            "price_list",
            "price_list_name",
            "cash_journal",
            "cash_journal_name",
            "card_journal",
            "card_journal_name",
            "walk_in_customer",
            "walk_in_customer_name",
            "walk_in_customer_name_ar",
            "cash_difference_account",
            "receipt_header",
            "receipt_footer",
            "is_active",
            "open_shift",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {"is_active": {"default": True}}

    def get_open_shift(self, register) -> dict | None:
        shifts = getattr(register, "open_shifts", None)
        shift = shifts[0] if shifts else None
        if shift is None:
            return None
        return {
            "id": shift.pk,
            "number": shift.number,
            "opened_at": shift.opened_at,
            "opened_by": shift.opened_by_id,
        }

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError({"company": "A register can't move company."})
        for field in (
            "branch",
            "warehouse",
            "price_list",
            "cash_journal",
            "card_journal",
            "walk_in_customer",
            "cash_difference_account",
        ):
            _same_company(get(field), company, field)
        for field in ("cash_journal", "card_journal"):
            journal = get(field)
            if journal is not None:
                if journal.journal_type not in (Journal.JournalType.CASH, Journal.JournalType.BANK):
                    raise serializers.ValidationError({field: "Choose a cash or bank journal."})
                if journal.default_account_id is None:
                    raise serializers.ValidationError(
                        {field: "This journal has no cash or bank account."}
                    )
        return attrs


class ShiftSerializer(serializers.ModelSerializer):
    register_name = serializers.CharField(source="register.name", read_only=True)
    register_code = serializers.CharField(source="register.code", read_only=True)
    branch = serializers.IntegerField(source="register.branch_id", read_only=True)
    branch_name = serializers.CharField(source="register.branch.name", read_only=True)
    opened_by_name = serializers.SerializerMethodField()
    closed_by_name = serializers.SerializerMethodField()
    totals = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = [
            "id",
            "company",
            "register",
            "register_code",
            "register_name",
            "branch",
            "branch_name",
            "number",
            "status",
            "opened_by",
            "opened_by_name",
            "opened_at",
            "opening_cash",
            "closed_by",
            "closed_by_name",
            "closed_at",
            "closing_cash",
            "expected_cash",
            "cash_difference",
            "sales_count",
            "sales_total",
            "refunds_total",
            "notes",
            "difference_entry",
            "totals",
        ]
        read_only_fields = fields

    @staticmethod
    def _name(user) -> str | None:
        if user is None:
            return None
        return f"{user.first_name} {user.last_name}".strip() or user.email

    def get_opened_by_name(self, shift) -> str | None:
        return self._name(shift.opened_by)

    def get_closed_by_name(self, shift) -> str | None:
        return self._name(shift.closed_by)

    def get_totals(self, shift) -> dict:
        totals = shift_totals(shift)
        return {
            key: ({m: str(v) for m, v in value.items()} if isinstance(value, dict) else str(value))
            for key, value in totals.items()
        }


class OpenShiftSerializer(serializers.Serializer):
    register = serializers.PrimaryKeyRelatedField(queryset=Register.objects.all())
    opening_cash = serializers.DecimalField(
        max_digits=18, decimal_places=3, min_value=Decimal(0), default=Decimal(0)
    )


class CloseShiftSerializer(serializers.Serializer):
    closing_cash = serializers.DecimalField(max_digits=18, decimal_places=3, min_value=Decimal(0))
    notes = serializers.CharField(allow_blank=True, required=False, default="")


class CartLineSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(
        queryset=Product.objects.select_related("uom", "category")
    )
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4, default=Decimal(1))
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2, default=Decimal(0))


class CartPaymentSerializer(serializers.Serializer):
    method = serializers.ChoiceField(choices=SalePayment.Method.choices)
    amount = serializers.DecimalField(max_digits=18, decimal_places=3)
    reference = serializers.CharField(allow_blank=True, required=False, default="", max_length=100)


class CartSerializer(serializers.Serializer):
    shift = serializers.PrimaryKeyRelatedField(
        queryset=Shift.objects.select_related("register", "company")
    )
    customer = serializers.PrimaryKeyRelatedField(
        queryset=Customer.objects.all(), required=False, allow_null=True, default=None
    )
    reference = serializers.CharField(allow_blank=True, required=False, default="", max_length=100)
    lines = CartLineSerializer(many=True)
    payments = CartPaymentSerializer(many=True)


class VoidSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class SalePaymentSerializer(serializers.ModelSerializer):
    method_label = serializers.CharField(source="get_method_display", read_only=True)
    receipt_number = serializers.CharField(source="receipt.number", read_only=True, default=None)

    class Meta:
        model = SalePayment
        fields = [
            "id",
            "method",
            "method_label",
            "amount",
            "reference",
            "receipt",
            "receipt_number",
        ]


class SaleLineSerializer(serializers.Serializer):
    product = serializers.IntegerField(source="product_id")
    sku = serializers.CharField(source="product.sku")
    name = serializers.CharField(source="product.name")
    name_ar = serializers.CharField(source="product.name_ar")
    uom = serializers.CharField(source="product.uom.code")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4)
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=3)
    discount_percent = serializers.DecimalField(max_digits=5, decimal_places=2)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2)
    net_amount = serializers.DecimalField(max_digits=18, decimal_places=3)
    tax_amount = serializers.DecimalField(max_digits=18, decimal_places=3)
    total_amount = serializers.DecimalField(max_digits=18, decimal_places=3)


class SaleSerializer(serializers.ModelSerializer):
    lines = SaleLineSerializer(source="invoice.lines", many=True, read_only=True)
    payments = SalePaymentSerializer(many=True, read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_mobile = serializers.CharField(source="customer.mobile", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    shift_number = serializers.CharField(source="shift.number", read_only=True)
    # Void is only possible while the shift is open; the back office hides it otherwise.
    shift_status = serializers.CharField(source="shift.status", read_only=True)
    register_code = serializers.CharField(source="shift.register.code", read_only=True)
    register_name = serializers.CharField(source="shift.register.name", read_only=True)
    receipt_header = serializers.CharField(source="shift.register.receipt_header", read_only=True)
    receipt_footer = serializers.CharField(source="shift.register.receipt_footer", read_only=True)
    currency = serializers.CharField(source="invoice.currency_id", read_only=True)
    cashier = serializers.SerializerMethodField()
    credit_note_number = serializers.CharField(
        source="credit_note.number", read_only=True, default=None
    )

    class Meta:
        model = Sale
        fields = [
            "id",
            "company",
            "shift",
            "shift_number",
            "shift_status",
            "register_code",
            "register_name",
            "branch",
            "branch_name",
            "number",
            "date",
            "customer",
            "customer_name",
            "customer_mobile",
            "invoice",
            "delivery",
            "credit_note",
            "credit_note_number",
            "currency",
            "subtotal",
            "tax_total",
            "total",
            "tendered",
            "change_given",
            "status",
            "voided_at",
            "voided_by",
            "void_reason",
            "cashier",
            "receipt_header",
            "receipt_footer",
            "lines",
            "payments",
            "created_at",
        ]
        read_only_fields = fields

    def get_cashier(self, sale) -> str | None:
        return ShiftSerializer._name(sale.created_by)
