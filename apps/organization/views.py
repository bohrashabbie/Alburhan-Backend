from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.permissions import OPEN
from apps.core.viewsets import AuditedModelViewSet, CompanyScopedModelViewSet

from . import serializers
from .models import Branch, Company, Country, Currency, DocumentSequence, ExchangeRate, Warehouse
from .services import ExchangeRateMissing, convert_amount, get_exchange_rate


class CurrencyViewSet(viewsets.ModelViewSet):
    queryset = Currency.objects.all()
    serializer_class = serializers.CurrencySerializer
    permission_resource = "organization.currency"
    # Shared reference data: any signed-in user may read it.
    action_permissions = {"list": OPEN, "retrieve": OPEN}
    filterset_fields = ["is_active"]
    search_fields = ["code", "name", "name_ar"]


class CountryViewSet(viewsets.ModelViewSet):
    queryset = Country.objects.select_related("default_currency")
    serializer_class = serializers.CountrySerializer
    permission_resource = "organization.country"
    # Shared reference data: any signed-in user may read it.
    action_permissions = {"list": OPEN, "retrieve": OPEN}
    filterset_fields = ["is_active"]
    search_fields = ["code", "name", "name_ar"]


class CompanyViewSet(CompanyScopedModelViewSet):
    queryset = Company.objects.select_related("country", "currency")
    serializer_class = serializers.CompanySerializer
    permission_resource = "organization.company"
    action_permissions = {"logo": "organization.company.update"}
    branch_lookup = "branches"
    distinct_scope = True
    company_lookup = "pk"
    filterset_fields = ["country", "is_active"]
    search_fields = ["code", "name", "name_ar", "legal_name"]
    ordering_fields = ["code", "name"]

    @extend_schema(request={"multipart/form-data": serializers.CompanyLogoSerializer})
    @action(detail=True, methods=["post", "delete"], url_path="logo")
    def logo(self, request, pk=None):
        """POST a multipart `logo` file to set the company logo; DELETE to remove it."""
        company = self.get_object()
        if request.method == "POST":
            upload = serializers.CompanyLogoSerializer(data=request.data)
            upload.is_valid(raise_exception=True)
        if company.logo:
            company.logo.delete(save=False)  # the old file leaves the bucket
        company.logo = upload.validated_data["logo"] if request.method == "POST" else None
        company.updated_by = request.user
        company.save()
        return Response(self.get_serializer(company).data)


class BranchViewSet(CompanyScopedModelViewSet):
    queryset = Branch.objects.select_related("company")
    serializer_class = serializers.BranchSerializer
    permission_resource = "organization.branch"
    branch_lookup = "pk"
    filterset_fields = ["company", "branch_type", "is_active"]
    search_fields = ["code", "name", "name_ar"]
    ordering_fields = ["code", "name"]


class WarehouseViewSet(CompanyScopedModelViewSet):
    queryset = Warehouse.objects.select_related("company", "branch")
    serializer_class = serializers.WarehouseSerializer
    permission_resource = "organization.warehouse"
    branch_lookup = "branch"
    branch_field = "branch"
    filterset_fields = ["company", "branch", "warehouse_type", "is_active"]
    search_fields = ["code", "name", "name_ar"]
    ordering_fields = ["code", "name"]


class DocumentSequenceViewSet(CompanyScopedModelViewSet):
    queryset = DocumentSequence.objects.select_related("company")
    serializer_class = serializers.DocumentSequenceSerializer
    permission_resource = "organization.document_sequence"
    filterset_fields = ["company", "doc_type"]


class ExchangeRateViewSet(AuditedModelViewSet):
    queryset = ExchangeRate.objects.all()
    serializer_class = serializers.ExchangeRateSerializer
    permission_resource = "organization.exchange_rate"
    # Shared reference data: any signed-in user may read it.
    action_permissions = {"list": OPEN, "retrieve": OPEN, "convert": OPEN}
    filterset_fields = ["currency", "to_currency", "effective_date"]
    ordering_fields = ["effective_date"]

    @extend_schema(
        parameters=[
            OpenApiParameter("amount", float, required=True),
            OpenApiParameter("from_currency", str, required=True),
            OpenApiParameter("to_currency", str, required=True),
            OpenApiParameter("date", str, description="YYYY-MM-DD, defaults to today"),
        ],
    )
    @action(detail=False, methods=["get"])
    def convert(self, request):
        query = serializers.ConvertQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        data = query.validated_data
        on_date = data.get("date") or timezone.localdate()
        source, target = data["from_currency"].code, data["to_currency"].code
        try:
            rate = get_exchange_rate(source, target, on_date)
            converted = convert_amount(data["amount"], source, target, on_date)
        except ExchangeRateMissing as exc:
            raise ValidationError({"detail": str(exc)}) from exc
        return Response(
            {
                "amount": str(data["amount"]),
                "from_currency": source,
                "to_currency": target,
                "date": on_date.isoformat(),
                "rate": str(rate),
                "converted_amount": str(converted),
            }
        )
