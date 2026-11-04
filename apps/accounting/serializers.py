from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.organization.services import currency_decimals

from .models import (
    Account,
    AccountingPeriod,
    AccountingSettings,
    Journal,
    JournalEntry,
    JournalLine,
)
from .services import PostingError, default_rate

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


class AccountSerializer(serializers.ModelSerializer):
    parent_code = serializers.CharField(source="parent.code", read_only=True, default=None)

    class Meta:
        model = Account
        fields = [
            "id",
            "company",
            "code",
            "name",
            "name_ar",
            "account_type",
            "parent",
            "parent_code",
            "is_group",
            "currency",
            "is_reconcilable",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        validators = []

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, parent, code = get("company"), get("parent"), get("code")
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError(
                {"company": "An account cannot move to another company."}
            )
        taken = Account.objects.filter(company=company, code=code).exclude(
            pk=getattr(self.instance, "pk", None)
        )
        if taken.exists():
            raise serializers.ValidationError({"code": "This code is already used."})
        if parent is not None:
            _same_company(parent, company, "parent")
            if not parent.is_group:
                raise serializers.ValidationError(
                    {"parent": "The parent must be a heading account."}
                )
            if parent.account_type != get("account_type"):
                raise serializers.ValidationError(
                    {"parent": "The parent must be of the same type."}
                )
            node = parent
            while node is not None:
                if self.instance and node.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        {"parent": "An account cannot be inside itself."}
                    )
                node = node.parent
        if self.instance and "is_group" in attrs:
            if attrs["is_group"] and self.instance.lines.exists():
                raise serializers.ValidationError(
                    {"is_group": "This account has entries, so it can't become a heading."}
                )
            if not attrs["is_group"] and self.instance.children.exists():
                raise serializers.ValidationError(
                    {"is_group": "This heading has accounts under it."}
                )
        return attrs


class JournalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Journal
        fields = [
            "id",
            "company",
            "code",
            "name",
            "name_ar",
            "journal_type",
            "default_account",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        validators = []

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        code = (get("code") or "").upper()
        attrs["code"] = code
        if (
            Journal.objects.filter(company=company, code=code)
            .exclude(pk=getattr(self.instance, "pk", None))
            .exists()
        ):
            raise serializers.ValidationError({"code": "This code is already used."})
        if self.instance and "code" in attrs and code != self.instance.code:
            if self.instance.entries.exclude(number="").exists():
                raise serializers.ValidationError(
                    {"code": "Entries already use this code in their numbers."}
                )
        account = get("default_account")
        _same_company(account, company, "default_account")
        if account is not None and account.is_group:
            raise serializers.ValidationError({"default_account": "Choose a detail account."})
        return attrs


class PeriodSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountingPeriod
        fields = [
            "id",
            "company",
            "name",
            "start_date",
            "end_date",
            "status",
            "closed_at",
            "closed_by",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["status", "closed_at", "closed_by", *AUDIT_FIELDS]
        validators = []

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, start, end = get("company"), get("start_date"), get("end_date")
        if end < start:
            raise serializers.ValidationError({"end_date": "Must be on or after the start date."})
        others = AccountingPeriod.objects.filter(company=company).exclude(
            pk=getattr(self.instance, "pk", None)
        )
        if others.filter(name=get("name")).exists():
            raise serializers.ValidationError({"name": "This name is already used."})
        if others.filter(start_date__lte=end, end_date__gte=start).exists():
            raise serializers.ValidationError({"start_date": "Overlaps another period."})
        return attrs


class JournalLineSerializer(serializers.ModelSerializer):
    account_code = serializers.CharField(source="account.code", read_only=True)
    account_name = serializers.CharField(source="account.name", read_only=True)
    customer_name = serializers.CharField(source="customer.name", read_only=True, default=None)
    supplier_name = serializers.CharField(source="supplier.name", read_only=True, default=None)

    class Meta:
        model = JournalLine
        fields = [
            "id",
            "account",
            "account_code",
            "account_name",
            "description",
            "debit",
            "credit",
            "debit_base",
            "credit_base",
            "customer",
            "customer_name",
            "supplier",
            "supplier_name",
            "branch",
        ]
        read_only_fields = ["id", "debit_base", "credit_base"]


class JournalEntrySerializer(serializers.ModelSerializer):
    lines = JournalLineSerializer(many=True)
    journal_code = serializers.CharField(source="journal.code", read_only=True)
    total_debit = serializers.SerializerMethodField()
    total_credit = serializers.SerializerMethodField()
    reversed_by = serializers.SerializerMethodField()

    class Meta:
        model = JournalEntry
        fields = [
            "id",
            "company",
            "journal",
            "journal_code",
            "number",
            "date",
            "reference",
            "description",
            "currency",
            "exchange_rate",
            "status",
            "posted_at",
            "posted_by",
            "reversal_of",
            "reversed_by",
            "source_type",
            "source_id",
            "total_debit",
            "total_credit",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = [
            "number",
            "status",
            "posted_at",
            "posted_by",
            "reversal_of",
            "source_type",
            "source_id",
            *AUDIT_FIELDS,
        ]
        extra_kwargs = {"exchange_rate": {"required": False}}

    def get_total_debit(self, entry) -> str:
        return str(sum((line.debit for line in entry.lines.all()), Decimal(0)))

    def get_total_credit(self, entry) -> str:
        return str(sum((line.credit for line in entry.lines.all()), Decimal(0)))

    def get_reversed_by(self, entry) -> int | None:
        reversal = getattr(entry, "reversed_by", None)
        return reversal.pk if reversal else None

    def validate(self, attrs):
        if self.instance and self.instance.status != JournalEntry.Status.DRAFT:
            raise serializers.ValidationError(
                "Posted entries can't be changed. Reverse them instead."
            )
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, journal = get("company"), get("journal")
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError(
                {"company": "An entry cannot move to another company."}
            )
        _same_company(journal, company, "journal")

        currency = get("currency")
        if "exchange_rate" not in attrs or attrs.get("exchange_rate") in (None, ""):
            if not self.instance or "currency" in attrs or "date" in attrs:
                try:
                    attrs["exchange_rate"] = default_rate(company, currency.code, get("date"))
                except PostingError as exc:
                    raise serializers.ValidationError({"exchange_rate": exc.message}) from exc
        if currency.code == company.currency_id:
            attrs["exchange_rate"] = Decimal(1)

        decimals = currency_decimals(currency.code)
        quantum = Decimal(1).scaleb(-decimals)
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            _same_company(line["account"], company, "lines")
            for side in ("debit", "credit"):
                amount = line.get(side) or Decimal(0)
                if amount < 0:
                    raise serializers.ValidationError(
                        {"lines": f"Line {number}: amounts can't be negative."}
                    )
                if amount != amount.quantize(quantum):
                    raise serializers.ValidationError(
                        {
                            "lines": f"Line {number}: {currency.code} allows {decimals} decimal places."
                        }
                    )
            if (line.get("debit") or 0) > 0 and (line.get("credit") or 0) > 0:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: use either a debit or a credit, not both."}
                )
            _same_company(line.get("customer"), company, "lines")
            _same_company(line.get("supplier"), company, "lines")
            _same_company(line.get("branch"), company, "lines")
        return attrs

    def _save_lines(self, entry, lines):
        entry.lines.all().delete()
        JournalLine.objects.bulk_create(
            [JournalLine(entry=entry, sequence=index, **line) for index, line in enumerate(lines)]
        )

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        entry = super().create(validated_data)
        self._save_lines(entry, lines)
        return entry

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        entry = super().update(instance, validated_data)
        if lines is not None:
            self._save_lines(entry, lines)
        return entry


class AccountingSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = AccountingSettings
        exclude = ["created_by", "updated_by"]
        read_only_fields = ["company", "created_at", "updated_at"]

    def validate(self, attrs):
        company = self.instance.company
        for field, account in attrs.items():
            if isinstance(account, Account):
                _same_company(account, company, field)
                if account.is_group:
                    raise serializers.ValidationError({field: "Choose a detail account."})
        return attrs


class SetupSerializer(serializers.Serializer):
    company = serializers.IntegerField()
    year = serializers.IntegerField(min_value=2000, max_value=2100)


class ReverseSerializer(serializers.Serializer):
    date = serializers.DateField()
    description = serializers.CharField(required=False, allow_blank=True, max_length=255)
