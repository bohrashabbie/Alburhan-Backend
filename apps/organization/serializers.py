from rest_framework import serializers

from .models import Branch, Company, Country, Currency, DocumentSequence, ExchangeRate, Warehouse

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


class CurrencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Currency
        fields = ["code", "name", "name_ar", "symbol", "decimal_places", "is_active"]


class CountrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Country
        fields = ["code", "name", "name_ar", "default_currency", "is_active"]


class CompanySerializer(serializers.ModelSerializer):
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Company
        fields = [
            "id",
            "code",
            "name",
            "name_ar",
            "legal_name",
            "country",
            "currency",
            "timezone",
            "tax_registration_number",
            "commercial_registration_number",
            "address",
            "phone",
            "email",
            "logo_url",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS

    def get_logo_url(self, company) -> str | None:
        # A signed link that expires (private bucket); the frontend re-fetches it with the data.
        return company.logo.url if company.logo else None


class BranchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Branch
        fields = [
            "id",
            "company",
            "code",
            "name",
            "name_ar",
            "branch_type",
            "address",
            "phone",
            "email",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS


class WarehouseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Warehouse
        fields = [
            "id",
            "company",
            "branch",
            "code",
            "name",
            "name_ar",
            "warehouse_type",
            "address",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS

    def validate(self, attrs):
        company = attrs.get("company", getattr(self.instance, "company", None))
        branch = attrs.get("branch", getattr(self.instance, "branch", None))
        if branch and company and branch.company_id != company.pk:
            raise serializers.ValidationError({"branch": "Branch must belong to the same company."})
        return attrs


class ExchangeRateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ExchangeRate
        fields = [
            "id",
            "currency",
            "to_currency",
            "rate",
            "effective_date",
            "source",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS

    def validate(self, attrs):
        currency = attrs.get("currency", getattr(self.instance, "currency", None))
        to_currency = attrs.get("to_currency", getattr(self.instance, "to_currency", None))
        if currency == to_currency:
            raise serializers.ValidationError({"to_currency": "Must differ from currency."})
        if attrs.get("rate") is not None and attrs["rate"] <= 0:
            raise serializers.ValidationError({"rate": "Must be greater than zero."})
        return attrs


class ConvertQuerySerializer(serializers.Serializer):
    amount = serializers.DecimalField(max_digits=18, decimal_places=3)
    from_currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.all())
    to_currency = serializers.SlugRelatedField(slug_field="code", queryset=Currency.objects.all())
    date = serializers.DateField(required=False)


class DocumentSequenceSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentSequence
        fields = [
            "id",
            "company",
            "doc_type",
            "prefix",
            "padding",
            "reset_yearly",
            "current_year",
            "next_number",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["current_year", *AUDIT_FIELDS]
