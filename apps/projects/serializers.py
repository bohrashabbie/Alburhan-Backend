from decimal import Decimal

from django.db import transaction
from rest_framework import serializers

from apps.catalog.models import Product
from apps.organization.models import Warehouse

from .models import BOQLine, MaterialIssue, MaterialIssueLine, Project
from .services import boq_progress, project_summary

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]
ZERO = Decimal("0")


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


class BOQLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    uom = serializers.CharField(source="product.uom.code", read_only=True)
    product_type = serializers.CharField(source="product.product_type", read_only=True)
    amount = serializers.SerializerMethodField()

    class Meta:
        model = BOQLine
        fields = [
            "id",
            "product",
            "sku",
            "product_name",
            "uom",
            "product_type",
            "description",
            "quantity",
            "unit_price",
            "unit_cost",
            "amount",
            "issued_quantity",
        ]
        read_only_fields = ["id", "issued_quantity"]

    def get_amount(self, line) -> str:
        return str(line.quantity * line.unit_price)


class ProjectSerializer(serializers.ModelSerializer):
    boq = BOQLineSerializer(many=True, required=False)
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    manager_name = serializers.SerializerMethodField()
    boq_value = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id",
            "company",
            "number",
            "name",
            "name_ar",
            "customer",
            "customer_name",
            "branch",
            "branch_name",
            "manager",
            "manager_name",
            "site_address",
            "status",
            "start_date",
            "end_date",
            "contract_value",
            "retention_percent",
            "notes",
            "boq_value",
            "summary",
            "boq",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["number", "status", *AUDIT_FIELDS]

    def get_manager_name(self, project) -> str | None:
        person = project.manager
        return (
            (f"{person.first_name} {person.last_name}".strip() or person.email) if person else None
        )

    def get_boq_value(self, project) -> str:
        return str(sum((line.quantity * line.unit_price for line in project.boq.all()), ZERO))

    def get_summary(self, project) -> dict | None:
        # Only on detail views: the list would otherwise run many queries per row.
        if self.context.get("view") and getattr(self.context["view"], "action", "") == "list":
            return None
        return project_summary(project)

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError(
                {"company": "A project cannot move to another company."}
            )
        for field in ("customer", "branch"):
            _same_company(get(field), company, field)
        manager = get("manager")
        if manager and company.pk not in manager.allowed_company_ids():
            raise serializers.ValidationError(
                {"manager": "This person has no role in the company."}
            )
        start, end = get("start_date"), get("end_date")
        if start and end and end < start:
            raise serializers.ValidationError({"end_date": "The end date is before the start."})
        for number, line in enumerate(attrs.get("boq") or [], start=1):
            product: Product = line["product"]
            if not product.is_active:
                raise serializers.ValidationError(
                    {"boq": f"Line {number}: {product.sku} is inactive."}
                )
            if line.get("quantity", Decimal(1)) <= 0:
                raise serializers.ValidationError(
                    {"boq": f"Line {number}: quantity must be positive."}
                )
            if (line.get("unit_price") or ZERO) < 0 or (line.get("unit_cost") or ZERO) < 0:
                raise serializers.ValidationError(
                    {"boq": f"Line {number}: prices can't be negative."}
                )
        return attrs

    def _save_boq(self, project, lines):
        existing = {line.pk: line for line in project.boq.all()}
        keep = set()
        for index, data in enumerate(lines):
            line_id = data.pop("id", None) if isinstance(data, dict) else None
            line = existing.get(line_id) if line_id else None
            if line is None:
                line = BOQLine(project=project)
            for key, value in data.items():
                setattr(line, key, value)
            line.sequence = index
            line.save()
            keep.add(line.pk)
        for line_id, line in existing.items():
            if line_id not in keep:
                if (
                    line.issued_quantity > 0
                    or line.invoicelines.exists()
                    or line.quotationlines.exists()
                ):
                    raise serializers.ValidationError(
                        {
                            "boq": f"{line.product.sku} has been issued or invoiced and can't be removed."
                        }
                    )
                line.delete()

    def to_internal_value(self, data):
        # Keep BOQ line ids so edits update rows instead of replacing them.
        value = super().to_internal_value(data)
        if "boq" in value:
            ids = [item.get("id") for item in data.get("boq", [])]
            for item, line_id in zip(value["boq"], ids, strict=False):
                if line_id:
                    item["id"] = line_id
        return value

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("boq", [])
        project = super().create(validated_data)
        from .services import ensure_number

        ensure_number(project)
        project.save(update_fields=["number"])
        self._save_boq(project, lines)
        return project

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("boq", None)
        project = super().update(instance, validated_data)
        if lines is not None:
            self._save_boq(project, lines)
        return project


class ProjectListSerializer(ProjectSerializer):
    class Meta(ProjectSerializer.Meta):
        fields = [f for f in ProjectSerializer.Meta.fields if f not in ("boq", "summary")]


class StatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=Project.Status.choices)


class ReserveSerializer(serializers.Serializer):
    warehouse = serializers.PrimaryKeyRelatedField(queryset=Warehouse.objects.all())


class InvoiceItemSerializer(serializers.Serializer):
    boq_line = serializers.IntegerField()
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4, required=False)


class InvoiceRequestSerializer(serializers.Serializer):
    lines = InvoiceItemSerializer(many=True)


class ProgressSerializer(serializers.Serializer):
    """Read-only helper for the schema: /projects/<id>/progress/."""

    def to_representation(self, project):
        return boq_progress(project)


class IssueLineSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    uom = serializers.CharField(source="product.uom.code", read_only=True)

    class Meta:
        model = MaterialIssueLine
        fields = [
            "id",
            "boq_line",
            "product",
            "sku",
            "product_name",
            "uom",
            "quantity",
            "unit_cost",
            "value",
        ]
        read_only_fields = ["id", "unit_cost", "value"]


class MaterialIssueSerializer(serializers.ModelSerializer):
    lines = IssueLineSerializer(many=True)
    project_number = serializers.CharField(source="project.number", read_only=True)
    project_name = serializers.CharField(source="project.name", read_only=True)
    customer_name = serializers.CharField(source="project.customer.name", read_only=True)
    warehouse_name = serializers.CharField(source="warehouse.name", read_only=True)
    journal_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )

    class Meta:
        model = MaterialIssue
        fields = [
            "id",
            "company",
            "number",
            "date",
            "project",
            "project_number",
            "project_name",
            "customer_name",
            "warehouse",
            "warehouse_name",
            "issued_to",
            "notes",
            "status",
            "cost_total",
            "journal_entry",
            "journal_number",
            "posted_at",
            "lines",
            *AUDIT_FIELDS,
        ]
        read_only_fields = [
            "number",
            "status",
            "cost_total",
            "journal_entry",
            "posted_at",
            *AUDIT_FIELDS,
        ]

    def validate(self, attrs):
        if self.instance and self.instance.status != MaterialIssue.Status.DRAFT:
            raise serializers.ValidationError("Posted issues can't be changed.")
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, project, warehouse = get("company"), get("project"), get("warehouse")
        _same_company(project, company, "project")
        _same_company(warehouse, company, "warehouse")
        for number, line in enumerate(attrs.get("lines") or [], start=1):
            boq_line = line.get("boq_line")
            if boq_line is not None and project is not None and boq_line.project_id != project.pk:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: BOQ line of another project."}
                )
            if boq_line is not None and boq_line.product_id != line["product"].pk:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: product differs from the BOQ line."}
                )
            if line["quantity"] <= 0:
                raise serializers.ValidationError(
                    {"lines": f"Line {number}: quantity must be positive."}
                )
        return attrs

    def _save_lines(self, issue, lines):
        issue.lines.all().delete()
        MaterialIssueLine.objects.bulk_create(
            [MaterialIssueLine(issue=issue, **line) for line in lines]
        )

    @transaction.atomic
    def create(self, validated_data):
        lines = validated_data.pop("lines", [])
        issue = super().create(validated_data)
        self._save_lines(issue, lines)
        return issue

    @transaction.atomic
    def update(self, instance, validated_data):
        lines = validated_data.pop("lines", None)
        issue = super().update(instance, validated_data)
        if lines is not None:
            self._save_lines(issue, lines)
        return issue
