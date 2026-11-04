import django_filters
from django.db.models import Prefetch, Q
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import mixins, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.access.services import companies_with
from apps.core.permissions import ErpPermission
from apps.core.viewsets import CompanyScopedModelViewSet, CompanyScopedViewSetMixin
from apps.organization.models import Company, Warehouse

from . import serializers
from .models import (
    DocumentStatus,
    ReorderRule,
    Reservation,
    StockAdjustment,
    StockAdjustmentLine,
    StockCount,
    StockCountLine,
    StockMove,
    StockTransfer,
    StockTransferLine,
)
from .services import (
    StockError,
    dispatch_transfer,
    fill_count,
    post_adjustment,
    post_count,
    receive_transfer,
    reorder_suggestions,
    reserve,
    slow_moving,
    stock_levels,
)


def _raise(exc: StockError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


def _company_for(request, company_id, code: str) -> Company:
    try:
        company = Company.objects.get(pk=company_id)
    except (Company.DoesNotExist, ValueError, TypeError) as exc:
        raise NotFound("Company not found.") from exc
    # Held anywhere in the company: branch staff then see their own warehouses only.
    if not request.user.is_superuser and company.pk not in companies_with(request.user, code):
        raise PermissionDenied("You do not have this permission for that company.")
    return company


def _warehouse_ids(request, company, code) -> list[int] | None:
    """Warehouses the user may see: all of the company for a company-wide role, else the
    branches' warehouses. A `warehouse` query param narrows further."""
    user = request.user
    queryset = Warehouse.objects.filter(company=company)
    if not user.is_superuser:
        company_ids, branch_ids = user.permission_scope(code)
        if company.pk not in company_ids:
            queryset = queryset.filter(branch_id__in=branch_ids)
    chosen = request.query_params.get("warehouse")
    if chosen:
        queryset = queryset.filter(pk=chosen)
    return list(queryset.values_list("pk", flat=True))


class WarehouseDocumentViewSet(CompanyScopedModelViewSet):
    """Documents that belong to one warehouse: branch staff see their branch's warehouses."""

    branch_lookup = "warehouse__branch"

    def target_branch(self, data, instance=None):
        warehouse = data.get("warehouse", getattr(instance, "warehouse", None))
        return warehouse.branch if warehouse else None

    def perform_destroy(self, instance):
        if instance.status != DocumentStatus.DRAFT:
            raise ValidationError({"detail": "Posted documents can't be deleted."})
        super().perform_destroy(instance)


class AdjustmentViewSet(WarehouseDocumentViewSet):
    queryset = StockAdjustment.objects.select_related(
        "warehouse", "journal_entry"
    ).prefetch_related(
        Prefetch(
            "lines", queryset=StockAdjustmentLine.objects.select_related("product", "product__uom")
        )
    )
    serializer_class = serializers.AdjustmentSerializer
    permission_resource = "inventory.adjustment"
    action_permissions = {"post": "inventory.adjustment.post"}
    filterset_fields = ["company", "warehouse", "status", "reason"]
    search_fields = ["number", "note", "lines__product__sku"]
    distinct_scope = True

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_adjustment(self.get_object(), request.user)
        except StockError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)


class TransferViewSet(CompanyScopedModelViewSet):
    queryset = StockTransfer.objects.select_related(
        "from_warehouse", "to_warehouse"
    ).prefetch_related(
        Prefetch(
            "lines", queryset=StockTransferLine.objects.select_related("product", "product__uom")
        )
    )
    serializer_class = serializers.TransferSerializer
    permission_resource = "inventory.transfer"
    action_permissions = {
        "send": "inventory.transfer.dispatch",
        "receive": "inventory.transfer.receive",
    }
    branch_lookup = ("from_warehouse__branch", "to_warehouse__branch")
    filterset_fields = ["company", "from_warehouse", "to_warehouse", "status"]
    search_fields = ["number", "note", "lines__product__sku"]
    distinct_scope = True

    def target_branch(self, data, instance=None):
        warehouse = data.get("from_warehouse", getattr(instance, "from_warehouse", None))
        return warehouse.branch if warehouse else None

    def perform_destroy(self, instance):
        if instance.status != StockTransfer.Status.DRAFT:
            raise ValidationError({"detail": "A transfer that has been sent can't be deleted."})
        super().perform_destroy(instance)

    # Named `send` because `dispatch` is Django's request entry point.
    @action(detail=True, methods=["post"], url_path="dispatch")
    def send(self, request, pk=None):
        transfer = self.get_object()
        if not request.user.has_erp_permission(
            "inventory.transfer.dispatch", transfer.company_id, transfer.from_warehouse.branch_id
        ):
            raise PermissionDenied("You can't send stock from that warehouse.")
        try:
            dispatch_transfer(transfer, request.user)
        except StockError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)

    @extend_schema(request=serializers.ReceiveSerializer)
    @action(detail=True, methods=["post"])
    def receive(self, request, pk=None):
        transfer = self.get_object()
        if not request.user.has_erp_permission(
            "inventory.transfer.receive", transfer.company_id, transfer.to_warehouse.branch_id
        ):
            raise PermissionDenied("You can't receive stock into that warehouse.")
        data = serializers.ReceiveSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            receive_transfer(
                transfer,
                data.validated_data["quantities"],
                data.validated_data["date"],
                request.user,
            )
        except StockError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)


class CountViewSet(WarehouseDocumentViewSet):
    queryset = StockCount.objects.select_related("warehouse", "journal_entry").prefetch_related(
        Prefetch("lines", queryset=StockCountLine.objects.select_related("product", "product__uom"))
    )
    serializer_class = serializers.CountSerializer
    permission_resource = "inventory.count"
    action_permissions = {"post": "inventory.count.post", "fill": "inventory.count.update"}
    filterset_fields = ["company", "warehouse", "status"]
    search_fields = ["number", "note"]
    distinct_scope = True

    @action(detail=True, methods=["post"])
    def fill(self, request, pk=None):
        count = self.get_object()
        if count.status != DocumentStatus.DRAFT:
            raise ValidationError({"detail": "Only draft counts can be filled."})
        added = fill_count(count)
        return Response(
            {"added": added, **self.get_serializer(self.get_queryset().get(pk=pk)).data}
        )

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_count(self.get_object(), request.user)
        except StockError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)


class ReservationViewSet(
    CompanyScopedViewSetMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Reservation.objects.select_related("warehouse", "product", "product__uom")
    serializer_class = serializers.ReservationSerializer
    permission_resource = "inventory.reservation"
    action_permissions = {"release": "inventory.reservation.delete"}
    branch_lookup = "warehouse__branch"
    filterset_fields = ["company", "warehouse", "product", "status"]
    search_fields = ["reference", "product__sku", "product__name"]

    def target_branch(self, data, instance=None):
        warehouse = data.get("warehouse", getattr(instance, "warehouse", None))
        return warehouse.branch if warehouse else None

    def perform_create(self, serializer):
        self._assert_allowed(serializer)
        data = serializer.validated_data
        try:
            reservation = reserve(
                data["company"],
                data["warehouse"],
                data["product"],
                data["quantity"],
                reference=data.get("reference", ""),
                user=self.request.user,
            )
        except StockError as exc:
            _raise(exc)
        serializer.instance = reservation

    @action(detail=True, methods=["post"])
    def release(self, request, pk=None):
        reservation = self.get_object()
        if reservation.status != Reservation.Status.ACTIVE:
            raise ValidationError({"detail": "This reservation is no longer active."})
        reservation.status = Reservation.Status.RELEASED
        reservation.updated_by = request.user
        reservation.save()
        return Response(self.get_serializer(reservation).data)


class ReorderRuleViewSet(CompanyScopedModelViewSet):
    queryset = ReorderRule.objects.select_related("warehouse", "product", "product__uom")
    serializer_class = serializers.ReorderRuleSerializer
    permission_resource = "inventory.reorder_rule"
    branch_lookup = "warehouse__branch"
    filterset_fields = ["company", "warehouse", "product", "is_active"]
    search_fields = ["product__sku", "product__name"]

    def target_branch(self, data, instance=None):
        warehouse = data.get("warehouse", getattr(instance, "warehouse", None))
        return warehouse.branch if warehouse else None


class MoveFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")

    class Meta:
        model = StockMove
        fields = ["company", "warehouse", "product", "status", "move_type"]


class MoveViewSet(CompanyScopedViewSetMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Stock history: every movement, newest first."""

    queryset = StockMove.objects.select_related("warehouse", "product", "product__uom")
    serializer_class = serializers.MoveSerializer
    action_permissions = {"list": "inventory.stock.view"}
    branch_lookup = "warehouse__branch"
    filterset_class = MoveFilter
    search_fields = ["product__sku", "product__name", "reference"]


class StockLevelsView(APIView):
    permission_classes = [ErpPermission]
    action_permissions = {None: "inventory.stock.view"}
    action = None

    @extend_schema(
        parameters=[
            OpenApiParameter("company", int, required=True),
            OpenApiParameter("warehouse", int),
            OpenApiParameter("search", str),
            OpenApiParameter(
                "only", str, description="low = below reorder level; negative_free = over-reserved"
            ),
        ]
    )
    def get(self, request):
        company = _company_for(request, request.query_params.get("company"), "inventory.stock.view")
        warehouses = _warehouse_ids(request, company, "inventory.stock.view")
        term = (request.query_params.get("search") or "").strip()
        product_filter = (
            Q(product__sku__icontains=term)
            | Q(product__name__icontains=term)
            | Q(product__name_ar__icontains=term)
            if term
            else None
        )
        rows = stock_levels(company, warehouses, product_filter)
        return Response(
            {"currency": company.currency_id, "as_of": timezone.localdate(), "rows": rows}
        )


class ReorderReportView(APIView):
    permission_classes = [ErpPermission]
    action_permissions = {None: "inventory.report.view"}
    action = None

    @extend_schema(
        parameters=[
            OpenApiParameter("company", int, required=True),
            OpenApiParameter("warehouse", int),
        ]
    )
    def get(self, request):
        company = _company_for(
            request, request.query_params.get("company"), "inventory.report.view"
        )
        warehouses = _warehouse_ids(request, company, "inventory.report.view")
        return Response({"rows": reorder_suggestions(company, warehouses)})


class SlowMovingReportView(APIView):
    permission_classes = [ErpPermission]
    action_permissions = {None: "inventory.report.view"}
    action = None

    @extend_schema(
        parameters=[OpenApiParameter("company", int, required=True), OpenApiParameter("days", int)]
    )
    def get(self, request):
        company = _company_for(
            request, request.query_params.get("company"), "inventory.report.view"
        )
        try:
            days = max(1, int(request.query_params.get("days") or 90))
        except ValueError as exc:
            raise ValidationError({"days": "Give a number of days."}) from exc
        return Response(
            {"currency": company.currency_id, "days": days, "rows": slow_moving(company, days)}
        )
