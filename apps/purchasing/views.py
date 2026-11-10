import django_filters
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ErpPermission
from apps.core.viewsets import CompanyScopedModelViewSet
from apps.partners.models import Supplier

from . import serializers
from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PaymentAllocation,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierInvoice,
    SupplierInvoiceLine,
    SupplierPayment,
)
from .services import (
    PurchaseError,
    billable_lines,
    cancel_order,
    cancel_supplier_invoice,
    close_order,
    post_goods_receipt,
    post_supplier_invoice,
    post_supplier_payment,
    receivable_lines,
    send_order,
    supplier_balance,
)


def _raise(exc: PurchaseError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


class PurchaseDocumentViewSet(CompanyScopedModelViewSet):
    """Purchasing is company-level: branch roles need a company-wide assignment to buy, but a
    branch's own orders (branch set) are visible to that branch."""

    def perform_destroy(self, instance):
        if instance.status != "draft":
            raise ValidationError({"detail": "Only drafts can be deleted."})
        super().perform_destroy(instance)

    def _fresh(self, pk):
        return self.get_serializer(self.get_queryset().get(pk=pk)).data


class DateRangeFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")


class OrderFilter(DateRangeFilter):
    open = django_filters.BooleanFilter(method="filter_open")

    class Meta:
        model = PurchaseOrder
        fields = ["company", "branch", "supplier", "warehouse", "status"]

    def filter_open(self, queryset, name, value):
        statuses = (PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL)
        return (
            queryset.filter(status__in=statuses) if value else queryset.exclude(status__in=statuses)
        )


class PurchaseOrderViewSet(PurchaseDocumentViewSet):
    queryset = PurchaseOrder.objects.select_related(
        "supplier", "branch", "warehouse"
    ).prefetch_related(
        Prefetch(
            "lines", queryset=PurchaseOrderLine.objects.select_related("product", "product__uom")
        )
    )
    serializer_class = serializers.PurchaseOrderSerializer
    permission_resource = "purchasing.order"
    action_permissions = {
        "send": "purchasing.order.send",
        "cancel": "purchasing.order.update",
        "close": "purchasing.order.update",
        "receivable": "purchasing.receipt.create",
        "billable": "purchasing.invoice.create",
    }
    branch_lookup = "branch"
    branch_field = "branch"
    filterset_class = OrderFilter
    search_fields = ["number", "reference", "supplier__name", "supplier__code"]
    ordering_fields = ["date", "number", "total", "expected_date", "created_at"]

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        try:
            send_order(self.get_object(), request.user)
        except PurchaseError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        try:
            cancel_order(self.get_object(), request.user)
        except PurchaseError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        try:
            close_order(self.get_object(), request.user)
        except PurchaseError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @action(detail=True, methods=["get"])
    def receivable(self, request, pk=None):
        return Response(receivable_lines(self.get_object()))

    @action(detail=True, methods=["get"])
    def billable(self, request, pk=None):
        return Response(billable_lines(self.get_object()))


class GoodsReceiptViewSet(PurchaseDocumentViewSet):
    queryset = GoodsReceipt.objects.select_related(
        "order", "order__supplier", "warehouse", "journal_entry"
    ).prefetch_related(
        Prefetch(
            "lines",
            queryset=GoodsReceiptLine.objects.select_related(
                "order_line", "order_line__product", "order_line__product__uom"
            ),
        )
    )
    serializer_class = serializers.GoodsReceiptSerializer
    permission_resource = "purchasing.receipt"
    action_permissions = {"post": "purchasing.receipt.post"}
    branch_lookup = "order__branch"
    filterset_fields = ["company", "order", "warehouse", "status"]
    search_fields = ["number", "order__number", "order__supplier__name", "supplier_reference"]
    ordering_fields = ["date", "number", "created_at"]

    def target_branch(self, data, instance=None):
        order = data.get("order", getattr(instance, "order", None))
        return order.branch if order else None

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_goods_receipt(self.get_object(), request.user)
        except PurchaseError as exc:
            _raise(exc)
        return Response(self._fresh(pk))


class BillFilter(DateRangeFilter):
    open = django_filters.BooleanFilter(method="filter_open")

    class Meta:
        model = SupplierInvoice
        fields = ["company", "supplier", "status", "order"]

    def filter_open(self, queryset, name, value):
        statuses = (SupplierInvoice.Status.POSTED, SupplierInvoice.Status.PARTIALLY_PAID)
        return (
            queryset.filter(status__in=statuses) if value else queryset.exclude(status__in=statuses)
        )


class SupplierInvoiceViewSet(PurchaseDocumentViewSet):
    queryset = SupplierInvoice.objects.select_related(
        "supplier", "order", "journal_entry"
    ).prefetch_related(
        Prefetch(
            "lines",
            queryset=SupplierInvoiceLine.objects.select_related(
                "product", "product__uom", "receipt_line", "receipt_line__receipt", "account"
            ),
        )
    )
    serializer_class = serializers.SupplierInvoiceSerializer
    permission_resource = "purchasing.invoice"
    action_permissions = {"post": "purchasing.invoice.post", "cancel": "purchasing.invoice.cancel"}
    filterset_class = BillFilter
    search_fields = ["number", "supplier_number", "reference", "supplier__name"]
    ordering_fields = ["date", "number", "total", "due_date", "created_at"]

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_supplier_invoice(self.get_object(), request.user)
        except PurchaseError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @extend_schema(request=serializers.DateSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        data = serializers.DateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            cancel_supplier_invoice(self.get_object(), data.validated_data["date"], request.user)
        except PurchaseError as exc:
            _raise(exc)
        return Response(self._fresh(pk))


class PaymentFilter(DateRangeFilter):
    class Meta:
        model = SupplierPayment
        fields = ["company", "supplier", "status", "method", "journal"]


class SupplierPaymentViewSet(PurchaseDocumentViewSet):
    queryset = SupplierPayment.objects.select_related(
        "supplier", "journal", "journal_entry"
    ).prefetch_related(
        Prefetch("allocations", queryset=PaymentAllocation.objects.select_related("invoice"))
    )
    serializer_class = serializers.SupplierPaymentSerializer
    permission_resource = "purchasing.payment"
    action_permissions = {"post": "purchasing.payment.post"}
    filterset_class = PaymentFilter
    search_fields = ["number", "reference", "supplier__name", "supplier__code"]
    ordering_fields = ["date", "number", "amount", "created_at"]

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_supplier_payment(self.get_object(), request.user)
        except PurchaseError as exc:
            _raise(exc)
        return Response(self._fresh(pk))


class SupplierBalanceView(APIView):
    """GET /purchasing/suppliers/<id>/balance/: open bills, amount due, advances."""

    permission_classes = [ErpPermission]
    action_permissions = {"api": "purchasing.invoice.view"}
    action = "api"  # a fixed key into action_permissions (also lets the API schema build)

    def get(self, request, pk):
        try:
            supplier = Supplier.objects.select_related("company").get(pk=pk)
        except Supplier.DoesNotExist as exc:
            raise NotFound("Supplier not found.") from exc
        user = request.user
        allowed = user.is_superuser or (
            user.has_erp_permission("purchasing.invoice.view")
            and supplier.company_id in user.allowed_company_ids()
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this supplier.")
        return Response(supplier_balance(supplier))


__all__ = ["status"]
