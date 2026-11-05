import django_filters
from django.db.models import Prefetch
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.permissions import ErpPermission
from apps.core.viewsets import (
    AuditedViewSetMixin,
    CompanyScopedModelViewSet,
    CompanyScopedViewSetMixin,
)
from apps.partners.models import Customer

from . import serializers
from .models import (
    CreditNote,
    CreditNoteLine,
    DeliveryLine,
    DeliveryNote,
    Invoice,
    InvoiceLine,
    Quotation,
    QuotationLine,
    Receipt,
    ReceiptAllocation,
    SalesSettings,
)
from .services import (
    SalesError,
    cancel_invoice,
    convert_quotation,
    customer_balance,
    post_credit_note,
    post_delivery,
    post_invoice,
    post_receipt,
    send_quotation,
)


def _raise(exc: SalesError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


class BranchDocumentViewSet(CompanyScopedModelViewSet):
    """Documents made at a branch (showroom): branch staff see their own branch's documents."""

    branch_lookup = "branch"
    branch_field = "branch"

    def perform_destroy(self, instance):
        if instance.status != "draft":
            raise ValidationError({"detail": "Only drafts can be deleted."})
        super().perform_destroy(instance)

    def _fresh(self, pk):
        return self.get_serializer(self.get_queryset().get(pk=pk)).data


class DateRangeFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")


class QuotationFilter(DateRangeFilter):
    class Meta:
        model = Quotation
        fields = ["company", "branch", "customer", "status", "salesperson"]


class QuotationViewSet(BranchDocumentViewSet):
    queryset = Quotation.objects.select_related(
        "customer", "branch", "salesperson", "invoice"
    ).prefetch_related(
        Prefetch("lines", queryset=QuotationLine.objects.select_related("product", "product__uom"))
    )
    serializer_class = serializers.QuotationSerializer
    permission_resource = "sales.quotation"
    action_permissions = {
        "send": "sales.quotation.update",
        "convert": "sales.invoice.create",
        "cancel": "sales.quotation.update",
    }
    filterset_class = QuotationFilter
    search_fields = ["number", "reference", "customer__name", "customer__code"]
    ordering_fields = ["date", "number", "total", "created_at"]

    @action(detail=True, methods=["post"])
    def send(self, request, pk=None):
        try:
            send_quotation(self.get_object(), request.user)
        except SalesError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @action(detail=True, methods=["post"])
    def convert(self, request, pk=None):
        try:
            invoice = convert_quotation(self.get_object(), request.user)
        except SalesError as exc:
            _raise(exc)
        return Response(
            serializers.InvoiceSerializer(invoice, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        quotation = self.get_object()
        if quotation.status in (Quotation.Status.INVOICED, Quotation.Status.CANCELLED):
            raise ValidationError({"detail": "This quotation can't be cancelled."})
        quotation.status = Quotation.Status.CANCELLED
        quotation.updated_by = request.user
        quotation.save()
        return Response(self._fresh(pk))


class InvoiceFilter(DateRangeFilter):
    open = django_filters.BooleanFilter(method="filter_open")

    class Meta:
        model = Invoice
        fields = ["company", "branch", "customer", "status", "salesperson"]

    def filter_open(self, queryset, name, value):
        open_statuses = (Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID)
        return (
            queryset.filter(status__in=open_statuses)
            if value
            else queryset.exclude(status__in=open_statuses)
        )


class InvoiceViewSet(BranchDocumentViewSet):
    queryset = Invoice.objects.select_related(
        "customer", "branch", "salesperson", "journal_entry", "warehouse"
    ).prefetch_related(
        Prefetch("lines", queryset=InvoiceLine.objects.select_related("product", "product__uom"))
    )
    serializer_class = serializers.InvoiceSerializer
    permission_resource = "sales.invoice"
    action_permissions = {"post": "sales.invoice.post", "cancel": "sales.invoice.cancel"}
    filterset_class = InvoiceFilter
    search_fields = ["number", "reference", "customer__name", "customer__code"]
    ordering_fields = ["date", "number", "total", "due_date", "created_at"]

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_invoice(self.get_object(), request.user)
        except SalesError as exc:
            _raise(exc)
        return Response(self._fresh(pk))

    @extend_schema(request=serializers.DateSerializer)
    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        data = serializers.DateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            cancel_invoice(self.get_object(), data.validated_data["date"], request.user)
        except SalesError as exc:
            _raise(exc)
        return Response(self._fresh(pk))


class DeliveryViewSet(CompanyScopedModelViewSet):
    queryset = DeliveryNote.objects.select_related(
        "invoice", "invoice__customer", "warehouse", "journal_entry"
    ).prefetch_related(
        Prefetch(
            "lines",
            queryset=DeliveryLine.objects.select_related(
                "invoice_line", "invoice_line__product", "invoice_line__product__uom"
            ),
        )
    )
    serializer_class = serializers.DeliveryNoteSerializer
    permission_resource = "sales.delivery"
    action_permissions = {"post": "sales.delivery.post"}
    branch_lookup = "invoice__branch"
    filterset_fields = ["company", "invoice", "warehouse", "status"]
    search_fields = ["number", "invoice__number", "invoice__customer__name", "delivered_to"]

    def target_branch(self, data, instance=None):
        invoice = data.get("invoice", getattr(instance, "invoice", None))
        return invoice.branch if invoice else None

    def perform_destroy(self, instance):
        if instance.status != DeliveryNote.Status.DRAFT:
            raise ValidationError({"detail": "Posted delivery notes can't be deleted."})
        super().perform_destroy(instance)

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_delivery(self.get_object(), request.user)
        except SalesError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)


class ReceiptFilter(DateRangeFilter):
    class Meta:
        model = Receipt
        fields = ["company", "branch", "customer", "status", "method", "journal"]


class ReceiptViewSet(BranchDocumentViewSet):
    queryset = Receipt.objects.select_related(
        "customer", "branch", "journal", "journal_entry"
    ).prefetch_related(
        Prefetch("allocations", queryset=ReceiptAllocation.objects.select_related("invoice"))
    )
    serializer_class = serializers.ReceiptSerializer
    permission_resource = "sales.receipt"
    action_permissions = {"post": "sales.receipt.post"}
    filterset_class = ReceiptFilter
    search_fields = ["number", "reference", "customer__name", "customer__code"]
    ordering_fields = ["date", "number", "amount", "created_at"]

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_receipt(self.get_object(), request.user)
        except SalesError as exc:
            _raise(exc)
        return Response(self._fresh(pk))


class CreditNoteFilter(DateRangeFilter):
    class Meta:
        model = CreditNote
        fields = ["company", "branch", "customer", "status", "invoice"]


class CreditNoteViewSet(BranchDocumentViewSet):
    queryset = CreditNote.objects.select_related(
        "customer", "branch", "invoice", "warehouse", "journal_entry"
    ).prefetch_related(
        Prefetch(
            "lines",
            queryset=CreditNoteLine.objects.select_related(
                "product", "product__uom", "invoice_line"
            ),
        )
    )
    serializer_class = serializers.CreditNoteSerializer
    permission_resource = "sales.credit_note"
    action_permissions = {"post": "sales.credit_note.post"}
    filterset_class = CreditNoteFilter
    search_fields = ["number", "invoice__number", "customer__name"]

    @action(detail=True, methods=["post"])
    def post(self, request, pk=None):
        try:
            post_credit_note(self.get_object(), request.user)
        except SalesError as exc:
            _raise(exc)
        return Response(self._fresh(pk))


class SalesSettingsViewSet(
    CompanyScopedViewSetMixin,
    AuditedViewSetMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """GET/PATCH /sales/settings/<company id>/ (created on first read)."""

    queryset = SalesSettings.objects.select_related("company")
    serializer_class = serializers.SalesSettingsSerializer
    permission_resource = "sales.settings"
    lookup_field = "company"

    def get_object(self):
        from apps.organization.models import Company

        company_id = self.kwargs["company"]
        if not Company.objects.filter(pk=company_id).exists():
            raise NotFound("Company not found.")
        SalesSettings.objects.get_or_create(company_id=company_id)
        return super().get_object()


class CustomerBalanceView(APIView):
    """GET /sales/customers/<id>/balance/: open invoices, amount due, advances, credit limit."""

    permission_classes = [ErpPermission]
    action_permissions = {None: "sales.invoice.view"}
    action = None

    def get(self, request, pk):
        try:
            customer = Customer.objects.select_related("company").get(pk=pk)
        except Customer.DoesNotExist as exc:
            raise NotFound("Customer not found.") from exc
        user = request.user
        allowed = user.is_superuser or (
            user.has_erp_permission("sales.invoice.view")
            and customer.company_id in user.allowed_company_ids()
        )
        if not allowed:
            raise PermissionDenied("You do not have access to this customer.")
        return Response(customer_balance(customer))
