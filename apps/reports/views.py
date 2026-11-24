from datetime import timedelta

from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ErpPermission
from apps.organization.models import Company
from apps.sales.models import Invoice

from .services import (
    ageing,
    balance_sheet,
    best_sellers,
    customer_balances,
    customer_statement,
    dashboard,
    egypt_einvoice,
    profit_and_loss,
    sales_targets,
    trade_calendar,
    vat_return,
    warehouse_stock,
    xbrl_instance,
)

COMPANY = OpenApiParameter("company", int, required=True)
DATE_FROM = OpenApiParameter("date_from", str)
DATE_TO = OpenApiParameter("date_to", str)


def _company_for(request, code: str) -> Company:
    try:
        company = Company.objects.get(pk=request.query_params.get("company"))
    except (Company.DoesNotExist, ValueError, TypeError) as exc:
        raise NotFound("Company not found.") from exc
    if not request.user.has_erp_permission(code, company_id=company.pk):
        raise PermissionDenied("You do not have this permission for that company.")
    return company


def _range(request, default_from_year_start=True):
    params = request.query_params
    date_to = parse_date(params.get("date_to") or "") or timezone.localdate()
    date_from = parse_date(params.get("date_from") or "")
    if date_from is None and default_from_year_start:
        date_from = date_to.replace(month=1, day=1)
    if date_from and date_from > date_to:
        raise ValidationError({"date_from": "Must be on or before the end date."})
    return date_from, date_to


class ReportView(APIView):
    permission_classes = [ErpPermission]
    action_permissions = {"api": "accounting.report.view"}
    action = "api"  # a fixed key into action_permissions (also lets the API schema build)
    code = "accounting.report.view"


class DashboardView(ReportView):
    action_permissions = {"api": "reports.dashboard.view"}

    @extend_schema(parameters=[COMPANY, OpenApiParameter("branch", int)])
    def get(self, request):
        company = _company_for(request, "reports.dashboard.view")
        user = request.user
        _, branches = user.permission_scope("reports.dashboard.view")
        scope = None if user.is_superuser or not branches else list(branches)
        chosen = request.query_params.get("branch")
        if chosen:
            scope = [int(chosen)] if scope is None or int(chosen) in scope else scope
        return Response(dashboard(company, branch_ids=scope))


class ProfitAndLossView(ReportView):
    @extend_schema(parameters=[COMPANY, DATE_FROM, DATE_TO])
    def get(self, request):
        company = _company_for(request, self.code)
        date_from, date_to = _range(request)
        return Response(profit_and_loss(company, date_from, date_to))


class BalanceSheetView(ReportView):
    @extend_schema(parameters=[COMPANY, DATE_TO])
    def get(self, request):
        company = _company_for(request, self.code)
        _, date_to = _range(request, default_from_year_start=False)
        return Response(balance_sheet(company, date_to))


class AgeingView(ReportView):
    @extend_schema(parameters=[COMPANY, OpenApiParameter("kind", str), DATE_TO])
    def get(self, request):
        company = _company_for(request, self.code)
        kind = request.query_params.get("kind", "receivable")
        if kind not in ("receivable", "payable"):
            raise ValidationError({"kind": "receivable or payable"})
        _, date_to = _range(request, default_from_year_start=False)
        return Response(ageing(company, kind, date_to))


class VatReturnView(ReportView):
    action_permissions = {"api": "reports.compliance.view"}

    @extend_schema(parameters=[COMPANY, DATE_FROM, DATE_TO])
    def get(self, request):
        company = _company_for(request, "reports.compliance.view")
        date_from, date_to = _range(request, default_from_year_start=False)
        if date_from is None:
            date_from = (date_to.replace(day=1) - timedelta(days=1)).replace(day=1)
        return Response(vat_return(company, date_from, date_to))


class XbrlView(ReportView):
    action_permissions = {"api": "reports.compliance.view"}

    @extend_schema(parameters=[COMPANY, DATE_FROM, DATE_TO])
    def get(self, request):
        company = _company_for(request, "reports.compliance.view")
        date_from, date_to = _range(request)
        xml = xbrl_instance(company, date_from, date_to)
        response = HttpResponse(xml, content_type="application/xml")
        response["Content-Disposition"] = (
            f'attachment; filename="{company.code}-{date_to.year}-xbrl.xml"'
        )
        return response


class EgyptEInvoiceView(ReportView):
    action_permissions = {"api": "reports.compliance.view"}

    def get(self, request, pk):
        try:
            invoice = Invoice.objects.select_related("company", "customer").get(pk=pk)
        except Invoice.DoesNotExist as exc:
            raise NotFound("Invoice not found.") from exc
        if not request.user.has_erp_permission(
            "reports.compliance.view", company_id=invoice.company_id
        ):
            raise PermissionDenied("You do not have this permission for that company.")
        if invoice.status == Invoice.Status.DRAFT:
            raise ValidationError({"detail": "Post the invoice first."})
        return Response(egypt_einvoice(invoice))


class XbrlCheckView(ReportView):
    """GET /reports/compliance/xbrl/check/: build the XBRL file for the period and check it."""

    action_permissions = {"api": "reports.compliance.view"}

    @extend_schema(parameters=[COMPANY, DATE_FROM, DATE_TO])
    def get(self, request):
        from .services import check_xbrl

        company = _company_for(request, "reports.compliance.view")
        date_from, date_to = _range(request)
        return Response(
            check_xbrl(xbrl_instance(company, date_from, date_to), company, date_from, date_to)
        )


class CustomerStatementView(ReportView):
    action_permissions = {"api": "sales.invoice.view"}

    @extend_schema(parameters=[COMPANY, OpenApiParameter("customer", int, required=True)])
    def get(self, request):
        from apps.partners.models import Customer

        company = _company_for(request, "sales.invoice.view")
        try:
            customer = Customer.objects.get(
                pk=request.query_params.get("customer"), company=company
            )
        except (Customer.DoesNotExist, ValueError, TypeError) as exc:
            raise NotFound("Choose a customer.") from exc
        date_from, date_to = _range(request)
        return Response(customer_statement(company, customer, date_from, date_to))


class CustomerBalancesView(ReportView):
    action_permissions = {"api": "sales.invoice.view"}

    @extend_schema(parameters=[COMPANY])
    def get(self, request):
        company = _company_for(request, "sales.invoice.view")
        _, as_of = _range(request)
        return Response(customer_balances(company, as_of))


def _year_month(request, month_required=False):
    today = timezone.localdate()
    try:
        year = int(request.query_params.get("year") or today.year)
        raw = request.query_params.get("month")
        month = int(raw) if raw else (today.month if month_required else None)
    except ValueError as exc:
        raise ValidationError({"year": "Choose a year and month."}) from exc
    if not 2000 <= year <= 2100 or (month is not None and not 1 <= month <= 12):
        raise ValidationError({"year": "Choose a year and month."})
    return year, month


def _dashboard_scope(request):
    user = request.user
    _, branches = user.permission_scope("reports.dashboard.view")
    return None if user.is_superuser or not branches else list(branches)


class BestSellersView(ReportView):
    action_permissions = {"api": "reports.dashboard.view"}

    @extend_schema(parameters=[COMPANY, DATE_FROM, DATE_TO])
    def get(self, request):
        company = _company_for(request, "reports.dashboard.view")
        date_from, date_to = _range(request)
        return Response(best_sellers(company, date_from, date_to, _dashboard_scope(request)))


class TradeCalendarView(ReportView):
    action_permissions = {"api": "reports.dashboard.view"}

    @extend_schema(
        parameters=[
            COMPANY,
            OpenApiParameter("kind", str),
            OpenApiParameter("year", int),
            OpenApiParameter("month", int),
        ]
    )
    def get(self, request):
        company = _company_for(request, "reports.dashboard.view")
        kind = "purchase" if request.query_params.get("kind") == "purchase" else "sale"
        year, month = _year_month(request)
        return Response(trade_calendar(company, kind, year, month, _dashboard_scope(request)))


class WarehouseStockView(ReportView):
    action_permissions = {"api": "inventory.report.view"}

    @extend_schema(parameters=[COMPANY])
    def get(self, request):
        return Response(warehouse_stock(_company_for(request, "inventory.report.view")))


class SalesTargetsView(ReportView):
    action_permissions = {"api": "reports.dashboard.view"}

    @extend_schema(
        parameters=[COMPANY, OpenApiParameter("year", int), OpenApiParameter("month", int)]
    )
    def get(self, request):
        company = _company_for(request, "reports.dashboard.view")
        year, month = _year_month(request, month_required=True)
        return Response(sales_targets(company, year, month))
