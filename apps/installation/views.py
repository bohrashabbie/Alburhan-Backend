from datetime import date, timedelta

import django_filters
from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.viewsets import CompanyScopedModelViewSet
from apps.organization.models import Company
from apps.sales.serializers import InvoiceSerializer

from . import serializers
from .models import Technician, WorkOrder
from .services import (
    InstallationError,
    calendar,
    cancel_work,
    complete_work,
    ensure_number,
    make_invoice,
    schedule_work,
    sign_off,
    start_work,
)

LIVE = (WorkOrder.Status.REQUESTED, WorkOrder.Status.SCHEDULED, WorkOrder.Status.IN_PROGRESS)


def _raise(exc: InstallationError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


class TechnicianViewSet(CompanyScopedModelViewSet):
    queryset = (
        Technician.objects.select_related("branch")
        .annotate(
            open_job_count=Count(
                "work_orders",
                filter=Q(
                    work_orders__status__in=(
                        WorkOrder.Status.SCHEDULED,
                        WorkOrder.Status.IN_PROGRESS,
                    )
                ),
            )
        )
        .order_by("name", "pk")
    )  # an aggregate drops Meta.ordering; keep pages stable
    serializer_class = serializers.TechnicianSerializer
    permission_resource = "installation.technician"
    branch_roles_cover_company = True
    filterset_fields = ["company", "branch", "is_active"]
    search_fields = ["name", "name_ar", "phone", "skills"]
    ordering_fields = ["name"]


class WorkOrderFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="scheduled_date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="scheduled_date", lookup_expr="lte")
    open = django_filters.BooleanFilter(method="filter_open")
    technician = django_filters.NumberFilter(field_name="technicians__id")
    mine = django_filters.BooleanFilter(method="filter_mine", label="Only my jobs")
    awaiting_signature = django_filters.BooleanFilter(method="filter_awaiting_signature")

    class Meta:
        model = WorkOrder
        fields = ["company", "branch", "customer", "project", "status"]

    def filter_open(self, queryset, name, value):
        return queryset.filter(status__in=LIVE) if value else queryset.exclude(status__in=LIVE)

    def filter_mine(self, queryset, name, value):
        """Jobs the signed-in user is assigned to as a technician."""
        if not value:
            return queryset
        return queryset.filter(technicians__user=self.request.user).distinct()

    def filter_awaiting_signature(self, queryset, name, value):
        waiting = Q(status=WorkOrder.Status.COMPLETED, signed_at__isnull=True)
        return queryset.filter(waiting) if value else queryset.exclude(waiting)


class WorkOrderViewSet(CompanyScopedModelViewSet):
    queryset = WorkOrder.objects.select_related(
        "customer",
        "branch",
        "project",
        "source_invoice",
        "created_by",
        "scheduled_by",
        "completed_by",
    ).prefetch_related("technicians", "invoices")
    serializer_class = serializers.WorkOrderSerializer
    permission_resource = "installation.work_order"
    action_permissions = {
        "schedule": "installation.work_order.schedule",
        "start": "installation.work_order.complete",
        "complete": "installation.work_order.complete",
        "sign": "installation.work_order.complete",
        "cancel": "installation.work_order.update",
        "bill": "sales.invoice.create",
        "calendar": "installation.work_order.view",
    }
    branch_lookup = "branch"
    branch_field = "branch"
    distinct_scope = True
    filterset_class = WorkOrderFilter
    search_fields = ["number", "title", "customer__name", "site_address", "project__number"]
    ordering_fields = ["scheduled_date", "number", "created_at"]

    def perform_create(self, serializer):
        self._assert_allowed(serializer)
        order = serializer.save(created_by=self.request.user, updated_by=self.request.user)
        ensure_number(order)
        order.save(update_fields=["number"])

    def perform_destroy(self, instance):
        if instance.status not in (WorkOrder.Status.REQUESTED, WorkOrder.Status.SCHEDULED):
            raise ValidationError(
                {"detail": "Only jobs that have not started can be deleted; cancel it instead."}
            )
        super().perform_destroy(instance)

    def _fresh(self, pk):
        return self.get_serializer(self.get_queryset().get(pk=pk)).data

    @extend_schema(request=serializers.ScheduleSerializer)
    @action(detail=True, methods=["post"])
    def schedule(self, request, pk=None):
        """The installation supervisor sets the date, time and technicians (or moves the job)."""
        data = serializers.ScheduleSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        try:
            schedule_work(
                self.get_object(),
                request.user,
                values["scheduled_date"],
                values.get("scheduled_time"),
                values["duration_hours"],
                list(values["technicians"]),
            )
        except InstallationError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @extend_schema(request=serializers.SignSerializer)
    @action(detail=True, methods=["post"])
    def sign(self, request, pk=None):
        """The customer signs the completion form on the technician's screen."""
        data = serializers.SignSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            sign_off(
                self.get_object(),
                request.user,
                data.validated_data["signed_by"],
                data.validated_data["signature"],
            )
        except InstallationError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @action(detail=True, methods=["post"])
    def start(self, request, pk=None):
        try:
            start_work(self.get_object(), request.user)
        except InstallationError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @extend_schema(request=serializers.CompleteSerializer)
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        data = serializers.CompleteSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            complete_work(
                self.get_object(),
                request.user,
                data.validated_data["labour_hours"],
                data.validated_data.get("completion_notes", ""),
                data.validated_data.get("signed_by", ""),
            )
        except InstallationError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @extend_schema(request=serializers.CancelSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        data = serializers.CancelSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            cancel_work(self.get_object(), request.user, data.validated_data["reason"])
        except InstallationError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @extend_schema(request=serializers.BillSerializer)
    @action(detail=True, methods=["post"])
    def bill(self, request, pk=None):
        data = serializers.BillSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            invoice = make_invoice(
                self.get_object(),
                data.validated_data["product"],
                data.validated_data["quantity"],
                data.validated_data["unit_price"],
                request.user,
            )
        except InstallationError as exc:
            _raise(exc)
        return Response(
            InvoiceSerializer(invoice, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=False, methods=["get"])
    def calendar(self, request):
        """GET /installation/work-orders/calendar/?company=&from=&to=: jobs per day."""
        try:
            company = Company.objects.get(pk=request.query_params.get("company"))
        except (Company.DoesNotExist, ValueError, TypeError):
            raise ValidationError({"company": "Choose a company."}) from None
        today = date.today()
        try:
            start = date.fromisoformat(
                request.query_params.get("from")
                or (today - timedelta(days=today.weekday())).isoformat()
            )
            end = date.fromisoformat(
                request.query_params.get("to") or (start + timedelta(days=13)).isoformat()
            )
        except ValueError:
            raise ValidationError({"from": "Use YYYY-MM-DD."}) from None
        user = request.user
        companies, branches = user.permission_scope("installation.work_order.view")
        if not user.is_superuser and company.pk not in companies:
            return Response([])
        scope = None if user.is_superuser or not branches else list(branches)
        return Response(calendar(company, start, end, scope))
