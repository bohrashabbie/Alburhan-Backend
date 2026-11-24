from decimal import Decimal

from rest_framework import serializers

from apps.catalog.models import Product
from apps.core.images import image_url

from .models import Technician, WorkOrder

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


class TechnicianSerializer(serializers.ModelSerializer):
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    open_jobs = serializers.SerializerMethodField()

    class Meta:
        model = Technician
        fields = [
            "id",
            "company",
            "name",
            "name_ar",
            "phone",
            "user",
            "branch",
            "branch_name",
            "skills",
            "hourly_cost",
            "is_active",
            "open_jobs",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {"is_active": {"default": True}}

    def get_open_jobs(self, tech) -> int:
        jobs = getattr(tech, "open_job_count", None)
        if jobs is not None:
            return jobs
        return tech.work_orders.filter(
            status__in=(WorkOrder.Status.SCHEDULED, WorkOrder.Status.IN_PROGRESS)
        ).count()

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        _same_company(get("branch"), get("company"), "branch")
        if (get("hourly_cost") or Decimal(0)) < 0:
            raise serializers.ValidationError({"hourly_cost": "The cost can't be negative."})
        return attrs


class WorkOrderSerializer(serializers.ModelSerializer):
    customer_name = serializers.CharField(source="customer.name", read_only=True)
    customer_mobile = serializers.CharField(source="customer.mobile", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)
    project_number = serializers.CharField(source="project.number", read_only=True, default=None)
    project_name = serializers.CharField(source="project.name", read_only=True, default=None)
    source_invoice_number = serializers.CharField(
        source="source_invoice.number", read_only=True, default=None
    )
    technician_names = serializers.SerializerMethodField()
    invoice_numbers = serializers.SerializerMethodField()
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    awaiting_signature = serializers.BooleanField(read_only=True)
    signature_url = serializers.SerializerMethodField()
    requested_by_name = serializers.SerializerMethodField()
    scheduled_by_name = serializers.SerializerMethodField()
    completed_by_name = serializers.SerializerMethodField()

    class Meta:
        model = WorkOrder
        fields = [
            "id",
            "company",
            "number",
            "title",
            "customer",
            "customer_name",
            "customer_mobile",
            "project",
            "project_number",
            "project_name",
            "source_invoice",
            "source_invoice_number",
            "branch",
            "branch_name",
            "site_address",
            "contact_name",
            "contact_phone",
            "location_link",
            "requested_date",
            "scheduled_date",
            "scheduled_time",
            "duration_hours",
            "technicians",
            "technician_names",
            "description",
            "status",
            "status_label",
            "requested_by_name",
            "scheduled_by_name",
            "scheduled_at",
            "started_at",
            "completed_at",
            "completed_by_name",
            "completion_notes",
            "signed_by",
            "signed_at",
            "signature_url",
            "awaiting_signature",
            "labour_hours",
            "labour_cost",
            "cancel_reason",
            "invoice_numbers",
            *AUDIT_FIELDS,
        ]
        # Date, time, hours and technicians are the supervisor's: set through POST .../schedule/.
        read_only_fields = [
            "number",
            "scheduled_date",
            "scheduled_time",
            "duration_hours",
            "technicians",
            "status",
            "scheduled_at",
            "started_at",
            "completed_at",
            "completion_notes",
            "signed_by",
            "signed_at",
            "labour_hours",
            "labour_cost",
            "cancel_reason",
            *AUDIT_FIELDS,
        ]

    def get_technician_names(self, order) -> list[str]:
        return [tech.name for tech in order.technicians.all()]

    def get_invoice_numbers(self, order) -> list[str]:
        return [inv.number for inv in order.invoices.all() if inv.number]

    def get_signature_url(self, order) -> str | None:
        return image_url(order, "signature")

    @staticmethod
    def _person(user) -> str | None:
        if user is None:
            return None
        return user.get_full_name() or user.username

    def get_requested_by_name(self, order) -> str | None:
        return self._person(order.created_by)

    def get_scheduled_by_name(self, order) -> str | None:
        return self._person(order.scheduled_by)

    def get_completed_by_name(self, order) -> str | None:
        return self._person(order.completed_by)

    def validate(self, attrs):
        if self.instance and self.instance.status in (
            WorkOrder.Status.COMPLETED,
            WorkOrder.Status.CANCELLED,
        ):
            raise serializers.ValidationError("Closed jobs can't be changed.")
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        if self.instance and "company" in attrs and attrs["company"] != self.instance.company:
            raise serializers.ValidationError({"company": "A job cannot move to another company."})
        for field in ("customer", "project", "source_invoice", "branch"):
            _same_company(get(field), company, field)
        project = get("project")
        customer = get("customer")
        if project is not None and customer is not None and project.customer_id != customer.pk:
            raise serializers.ValidationError(
                {"project": "The project belongs to another customer."}
            )
        return attrs


class ScheduleSerializer(serializers.Serializer):
    scheduled_date = serializers.DateField()
    scheduled_time = serializers.TimeField(required=False, allow_null=True, default=None)
    duration_hours = serializers.DecimalField(
        max_digits=5, decimal_places=2, required=False, default=Decimal(2)
    )
    technicians = serializers.PrimaryKeyRelatedField(
        many=True, queryset=Technician.objects.all(), allow_empty=True
    )


class SignSerializer(serializers.Serializer):
    signed_by = serializers.CharField(max_length=120, allow_blank=True)
    signature = serializers.CharField(help_text="The drawing as a PNG data URL.")


class CompleteSerializer(serializers.Serializer):
    labour_hours = serializers.DecimalField(max_digits=7, decimal_places=2, min_value=Decimal(0))
    completion_notes = serializers.CharField(allow_blank=True, required=False, default="")
    signed_by = serializers.CharField(allow_blank=True, required=False, default="", max_length=120)


class CancelSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=255)


class BillSerializer(serializers.Serializer):
    product = serializers.PrimaryKeyRelatedField(queryset=Product.objects.filter(is_active=True))
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4, default=Decimal(1))
    unit_price = serializers.DecimalField(max_digits=18, decimal_places=3)
