from django.db import transaction
from rest_framework import serializers

from apps.organization.services import next_document_number

from .models import Contact, Customer, Supplier

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = ["id", "name", "job_title", "phone", "email", "is_primary"]
        read_only_fields = ["id"]


class PartnerSerializer(serializers.ModelSerializer):
    """Shared behaviour: auto-numbered code, unique per company, and nested contacts that are
    replaced as a whole on each save."""

    doc_type = ""  # CUS / SUP
    contacts = ContactSerializer(many=True, required=False)

    def validate(self, attrs):
        company = attrs.get("company", getattr(self.instance, "company", None))
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError(
                {"company": "A record cannot move to another company."}
            )
        code = (attrs.get("code") or "").strip()
        if code:
            attrs["code"] = code.upper()
            taken = self.Meta.model.objects.filter(company=company, code=attrs["code"]).exclude(
                pk=getattr(self.instance, "pk", None)
            )
            if taken.exists():
                raise serializers.ValidationError({"code": "This code is already used."})
        contacts = attrs.get("contacts") or []
        if sum(1 for c in contacts if c.get("is_primary")) > 1:
            raise serializers.ValidationError({"contacts": "Only one contact can be primary."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        contacts = validated_data.pop("contacts", [])
        if not validated_data.get("code"):
            validated_data["code"] = next_document_number(
                validated_data["company"], self.doc_type, yearly=False
            )
        instance = super().create(validated_data)
        self._save_contacts(instance, contacts)
        return instance

    @transaction.atomic
    def update(self, instance, validated_data):
        contacts = validated_data.pop("contacts", None)
        if not validated_data.get("code", instance.code):
            validated_data["code"] = instance.code
        instance = super().update(instance, validated_data)
        if contacts is not None:
            instance.contacts.all().delete()
            self._save_contacts(instance, contacts)
        return instance

    def _save_contacts(self, instance, contacts):
        owner = {self.contact_owner: instance}
        Contact.objects.bulk_create([Contact(**owner, **data) for data in contacts])


class CustomerSerializer(PartnerSerializer):
    doc_type = "CUS"
    contact_owner = "customer"
    price_list_name = serializers.CharField(source="price_list.name", read_only=True, default=None)

    class Meta:
        model = Customer
        fields = [
            "id",
            "company",
            "code",
            "name",
            "name_ar",
            "customer_type",
            "phone",
            "mobile",
            "email",
            "civil_id",
            "commercial_registration",
            "tax_number",
            "address",
            "area",
            "credit_limit",
            "payment_terms_days",
            "price_list",
            "price_list_name",
            "salesperson",
            "notes",
            "is_active",
            "contacts",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {"code": {"required": False, "allow_blank": True}}
        validators = []

    def validate(self, attrs):
        attrs = super().validate(attrs)
        company = attrs.get("company", getattr(self.instance, "company", None))
        price_list = attrs.get("price_list", getattr(self.instance, "price_list", None))
        if price_list and price_list.company_id != company.pk:
            raise serializers.ValidationError(
                {"price_list": "Choose a price list of the same company."}
            )
        salesperson = attrs.get("salesperson")
        if salesperson and company.pk not in salesperson.allowed_company_ids():
            raise serializers.ValidationError(
                {"salesperson": "This person has no role in the customer's company."}
            )
        return attrs


class SupplierSerializer(PartnerSerializer):
    doc_type = "SUP"
    contact_owner = "supplier"

    class Meta:
        model = Supplier
        fields = [
            "id",
            "company",
            "code",
            "name",
            "name_ar",
            "country",
            "currency",
            "phone",
            "email",
            "website",
            "address",
            "tax_number",
            "payment_terms_days",
            "lead_time_days",
            "intercompany",
            "notes",
            "is_active",
            "contacts",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {"code": {"required": False, "allow_blank": True}}
        validators = []

    def validate(self, attrs):
        attrs = super().validate(attrs)
        company = attrs.get("company", getattr(self.instance, "company", None))
        intercompany = attrs.get("intercompany", getattr(self.instance, "intercompany", None))
        if intercompany and intercompany.pk == company.pk:
            raise serializers.ValidationError(
                {"intercompany": "A company cannot be its own supplier."}
            )
        return attrs
