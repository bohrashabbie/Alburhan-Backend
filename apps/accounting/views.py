from datetime import date

import django_filters
from django.db.models import Prefetch
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import OPEN, ErpPermission
from apps.core.viewsets import (
    AuditedViewSetMixin,
    CompanyScopedModelViewSet,
    CompanyScopedViewSetMixin,
)
from apps.organization.models import Company

from . import serializers
from .models import (
    Account,
    AccountingPeriod,
    AccountingSettings,
    Journal,
    JournalEntry,
    JournalLine,
)
from .services import PostingError, general_ledger, post_entry, reverse_entry, trial_balance
from .setup import create_year_periods, set_up_company


def _raise(exc: PostingError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


def _company_for(request, company_id, code: str) -> Company:
    """The company in the request, if the user holds `code` there (else 403/404)."""
    try:
        company = Company.objects.get(pk=company_id)
    except (Company.DoesNotExist, ValueError, TypeError) as exc:
        raise NotFound("Company not found.") from exc
    if not request.user.has_erp_permission(code, company_id=company.pk):
        raise PermissionDenied("You do not have this permission for that company.")
    return company


class AccountViewSet(CompanyScopedModelViewSet):
    queryset = Account.objects.select_related("parent", "company")
    serializer_class = serializers.AccountSerializer
    permission_resource = "accounting.account"
    filterset_fields = [
        "company",
        "account_type",
        "is_group",
        "is_active",
        "parent",
        "is_reconcilable",
    ]
    search_fields = ["code", "name", "name_ar"]
    ordering_fields = ["code", "name"]


class JournalViewSet(CompanyScopedModelViewSet):
    queryset = Journal.objects.select_related("company", "default_account")
    serializer_class = serializers.JournalSerializer
    permission_resource = "accounting.journal"
    # Reading the list is open to anyone who takes or pays money (receipts, supplier payments,
    # POS registers): they choose a cash or bank journal without needing accounting rights.
    action_permissions = {"list": OPEN, "retrieve": OPEN}
    READERS = (
        "accounting.journal.view",
        "sales.receipt.create",
        "purchasing.payment.create",
        "pos.register.create",
        "pos.register.update",
    )
    filterset_fields = ["company", "journal_type", "is_active"]
    search_fields = ["code", "name", "name_ar"]

    def get_queryset(self):
        user = self.request.user
        if self.action in ("list", "retrieve") and not user.is_superuser:
            from apps.access.services import companies_with

            companies = set()
            for code in self.READERS:
                companies |= set(companies_with(user, code))
            return self.queryset.filter(company__in=companies)
        return super().get_queryset()


class PeriodViewSet(CompanyScopedModelViewSet):
    queryset = AccountingPeriod.objects.select_related("company")
    serializer_class = serializers.PeriodSerializer
    permission_resource = "accounting.period"
    action_permissions = {
        "close": "accounting.period.close",
        "reopen": "accounting.period.close",
        "generate": "accounting.period.create",
    }
    filterset_fields = ["company", "status"]
    search_fields = ["name"]

    def perform_destroy(self, instance):
        if JournalEntry.objects.filter(
            company=instance.company, date__gte=instance.start_date, date__lte=instance.end_date
        ).exists():
            raise ValidationError({"detail": "This period has entries; close it instead."})
        super().perform_destroy(instance)

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        period = self.get_object()
        drafts = JournalEntry.objects.filter(
            company=period.company,
            status=JournalEntry.Status.DRAFT,
            date__gte=period.start_date,
            date__lte=period.end_date,
        ).count()
        if drafts:
            raise ValidationError(
                {"detail": f"Post or delete the {drafts} draft entries in this period first."}
            )
        period.status = AccountingPeriod.Status.CLOSED
        period.closed_at = timezone.now()
        period.closed_by = request.user
        period.updated_by = request.user
        period.save()
        return Response(self.get_serializer(period).data)

    @action(detail=True, methods=["post"])
    def reopen(self, request, pk=None):
        period = self.get_object()
        period.status = AccountingPeriod.Status.OPEN
        period.closed_at = None
        period.closed_by = None
        period.updated_by = request.user
        period.save()
        return Response(self.get_serializer(period).data)

    @extend_schema(request=serializers.SetupSerializer)
    @action(detail=False, methods=["post"])
    def generate(self, request):
        data = serializers.SetupSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        company = _company_for(request, data.validated_data["company"], "accounting.period.create")
        made = create_year_periods(company, data.validated_data["year"], request.user)
        return Response({"created": made}, status=status.HTTP_201_CREATED)


class EntryFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")
    account = django_filters.NumberFilter(field_name="lines__account", distinct=True)

    class Meta:
        model = JournalEntry
        fields = ["company", "journal", "status", "currency", "source_type"]


class JournalEntryViewSet(CompanyScopedModelViewSet):
    queryset = JournalEntry.objects.select_related(
        "journal", "company", "reversed_by"
    ).prefetch_related(
        Prefetch(
            "lines", queryset=JournalLine.objects.select_related("account", "customer", "supplier")
        )
    )
    serializer_class = serializers.JournalEntrySerializer
    permission_resource = "accounting.entry"
    action_permissions = {"post": "accounting.entry.post", "reverse": "accounting.entry.reverse"}
    filterset_class = EntryFilter
    search_fields = ["number", "reference", "description"]
    ordering_fields = ["date", "number", "created_at"]

    def perform_destroy(self, instance):
        if instance.status != JournalEntry.Status.DRAFT:
            raise ValidationError(
                {"detail": "Posted entries can't be deleted. Reverse them instead."}
            )
        super().perform_destroy(instance)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        entry = self.get_object()
        try:
            entry = post_entry(entry, request.user)
        except PostingError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=entry.pk)).data)

    @extend_schema(request=serializers.ReverseSerializer)
    @action(detail=True, methods=["post"])
    def reverse(self, request, pk=None):
        entry = self.get_object()
        data = serializers.ReverseSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            reversal = reverse_entry(
                entry,
                data.validated_data["date"],
                request.user,
                data.validated_data.get("description", ""),
            )
        except PostingError as exc:
            _raise(exc)
        return Response(
            self.get_serializer(self.get_queryset().get(pk=reversal.pk)).data,
            status=status.HTTP_201_CREATED,
        )


class AccountingSettingsViewSet(
    CompanyScopedViewSetMixin,
    AuditedViewSetMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """GET/PATCH /accounting/settings/<company id>/"""

    queryset = AccountingSettings.objects.select_related("company")
    serializer_class = serializers.AccountingSettingsSerializer
    permission_resource = "accounting.settings"
    lookup_field = "company"


class SetupView(APIView):
    """POST {company, year}: standard chart of accounts, journals, default accounts, periods."""

    permission_classes = [ErpPermission]
    action_permissions = {None: "accounting.account.create"}
    action = None

    @extend_schema(request=serializers.SetupSerializer)
    def post(self, request):
        data = serializers.SetupSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        company = _company_for(request, data.validated_data["company"], "accounting.account.create")
        return Response(set_up_company(company, data.validated_data["year"], request.user))


def _dates(request) -> tuple[date | None, date]:
    params = request.query_params
    date_to = parse_date(params.get("date_to") or "") or timezone.localdate()
    date_from = parse_date(params.get("date_from") or "")
    if date_from and date_from > date_to:
        raise ValidationError({"date_from": "Must be on or before the end date."})
    return date_from, date_to


class TrialBalanceView(APIView):
    permission_classes = [ErpPermission]
    action_permissions = {None: "accounting.report.view"}
    action = None

    @extend_schema(
        parameters=[
            OpenApiParameter("company", int, required=True),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
        ]
    )
    def get(self, request):
        company = _company_for(
            request, request.query_params.get("company"), "accounting.report.view"
        )
        date_from, date_to = _dates(request)
        report = trial_balance(company, date_from, date_to)
        report.update(date_from=date_from, date_to=date_to)
        return Response(report)


class GeneralLedgerView(APIView):
    permission_classes = [ErpPermission]
    action_permissions = {None: "accounting.report.view"}
    action = None

    @extend_schema(
        parameters=[
            OpenApiParameter("account", int, required=True),
            OpenApiParameter("date_from", str),
            OpenApiParameter("date_to", str),
        ]
    )
    def get(self, request):
        try:
            account = Account.objects.select_related("company").get(
                pk=request.query_params.get("account")
            )
        except (Account.DoesNotExist, ValueError, TypeError) as exc:
            raise NotFound("Account not found.") from exc
        _company_for(request, account.company_id, "accounting.report.view")
        date_from, date_to = _dates(request)
        report = general_ledger(account, date_from or date_to.replace(month=1, day=1), date_to)
        report.update(date_from=date_from or date_to.replace(month=1, day=1), date_to=date_to)
        return Response(report)
