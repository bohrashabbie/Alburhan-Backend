import django_filters
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.viewsets import CompanyScopedModelViewSet
from apps.sales.serializers import InvoiceSerializer, QuotationSerializer

from . import serializers
from .models import BOQLine, MaterialIssue, MaterialIssueLine, Project
from .services import (
    ProjectError,
    boq_progress,
    make_invoice,
    make_quotation,
    post_material_issue,
    reserve_boq,
    set_status,
)


def _raise(exc: ProjectError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


class ProjectFilter(django_filters.FilterSet):
    active = django_filters.BooleanFilter(method="filter_active")

    class Meta:
        model = Project
        fields = ["company", "branch", "customer", "status", "manager"]

    def filter_active(self, queryset, name, value):
        live = (Project.Status.WON, Project.Status.IN_PROGRESS, Project.Status.COMPLETED)
        return queryset.filter(status__in=live) if value else queryset.exclude(status__in=live)


class ProjectViewSet(CompanyScopedModelViewSet):
    queryset = Project.objects.select_related("customer", "branch", "manager").prefetch_related(
        Prefetch("boq", queryset=BOQLine.objects.select_related("product", "product__uom"))
    )
    serializer_class = serializers.ProjectSerializer
    permission_resource = "projects.project"
    action_permissions = {
        "set_status": "projects.project.update",
        "reserve": "projects.project.update",
        "quote": "sales.quotation.create",
        "invoice": "sales.invoice.create",
        "progress": "projects.project.view",
    }
    branch_lookup = "branch"
    branch_field = "branch"
    filterset_class = ProjectFilter
    search_fields = ["number", "name", "name_ar", "customer__name", "site_address"]
    ordering_fields = ["created_at", "number", "name", "start_date", "contract_value"]

    def get_serializer_class(self):
        return (
            serializers.ProjectListSerializer
            if self.action == "list"
            else serializers.ProjectSerializer
        )

    def perform_destroy(self, instance):
        if instance.status not in (Project.Status.LEAD, Project.Status.LOST):
            raise ValidationError(
                {"detail": "Only leads can be deleted; close the project instead."}
            )
        super().perform_destroy(instance)

    def _fresh(self, pk):
        return serializers.ProjectSerializer(
            self.get_queryset().get(pk=pk), context=self.get_serializer_context()
        ).data

    @extend_schema(request=serializers.StatusSerializer)
    @action(detail=True, methods=["post"], url_path="status")
    def set_status(self, request, pk=None):
        data = serializers.StatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            set_status(self.get_object(), data.validated_data["status"], request.user)
        except ProjectError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @extend_schema(request=serializers.ReserveSerializer)
    @action(detail=True, methods=["post"])
    def reserve(self, request, pk=None):
        data = serializers.ReserveSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            made = reserve_boq(self.get_object(), data.validated_data["warehouse"], request.user)
        except ProjectError as exc:
            _raise(exc)
        return Response({"reserved": len(made), "project": self._fresh(pk)})

    @action(detail=True, methods=["post"])
    def quote(self, request, pk=None):
        try:
            quotation = make_quotation(self.get_object(), request.user)
        except ProjectError as exc:
            _raise(exc)
        return Response(
            QuotationSerializer(quotation, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(request=serializers.InvoiceRequestSerializer)
    @action(detail=True, methods=["post"])
    def invoice(self, request, pk=None):
        data = serializers.InvoiceRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            invoice = make_invoice(self.get_object(), data.validated_data["lines"], request.user)
        except ProjectError as exc:
            _raise(exc)
        return Response(
            InvoiceSerializer(invoice, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["get"])
    def progress(self, request, pk=None):
        return Response(boq_progress(self.get_object()))


class IssueFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")

    class Meta:
        model = MaterialIssue
        fields = ["company", "project", "warehouse", "status"]


class MaterialIssueViewSet(CompanyScopedModelViewSet):
    queryset = MaterialIssue.objects.select_related(
        "project", "project__customer", "warehouse", "journal_entry"
    ).prefetch_related(
        Prefetch(
            "lines",
            queryset=MaterialIssueLine.objects.select_related(
                "product", "product__uom", "boq_line"
            ),
        )
    )
    serializer_class = serializers.MaterialIssueSerializer
    permission_resource = "projects.issue"
    action_permissions = {"post": "projects.issue.post"}
    branch_lookup = "project__branch"
    filterset_class = IssueFilter
    search_fields = ["number", "project__number", "project__name", "issued_to"]
    ordering_fields = ["date", "number", "created_at"]

    def target_branch(self, data, instance=None):
        project = data.get("project", getattr(instance, "project", None))
        return project.branch if project else None

    def perform_destroy(self, instance):
        if instance.status != MaterialIssue.Status.DRAFT:
            raise ValidationError({"detail": "Posted issues can't be deleted."})
        super().perform_destroy(instance)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_material_issue(self.get_object(), request.user)
        except ProjectError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)
