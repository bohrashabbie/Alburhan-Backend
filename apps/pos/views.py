import django_filters
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.core.viewsets import CompanyScopedModelViewSet, CompanyScopedViewSetMixin

from . import serializers
from .models import Register, Sale, SalePayment, Shift
from .services import (
    PosError,
    close_shift,
    make_sale,
    open_shift,
    register_catalog,
    void_sale,
)


def _raise(exc: PosError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


class RegisterViewSet(CompanyScopedModelViewSet):
    queryset = Register.objects.select_related(
        "branch", "warehouse", "price_list", "cash_journal", "card_journal", "walk_in_customer"
    ).prefetch_related(
        Prefetch(
            "shifts",
            queryset=Shift.objects.filter(status=Shift.Status.OPEN).select_related("opened_by"),
            to_attr="open_shifts",
        )
    )
    serializer_class = serializers.RegisterSerializer
    permission_resource = "pos.register"
    action_permissions = {"catalog": "pos.sale.create"}
    branch_lookup = "branch"
    branch_field = "branch"
    filterset_fields = ["company", "branch", "is_active"]
    search_fields = ["code", "name", "name_ar"]
    ordering_fields = ["code", "name"]

    @action(detail=True, methods=["get"])
    def catalog(self, request, pk=None):
        """Products this register can sell, with price, photo and free stock."""
        # Not get_object(): the list search filter must not apply to the register itself.
        register = get_object_or_404(self.get_queryset(), pk=pk)
        category = request.query_params.get("category") or None
        try:
            category_id = int(category) if category else None
        except ValueError:
            category_id = None
        return Response(
            register_catalog(
                register,
                search=request.query_params.get("search", ""),
                category_id=category_id,
                limit=min(int(request.query_params.get("limit", 60) or 60), 200),
            )
        )


class ShiftFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="opened_at", lookup_expr="date__gte")
    date_to = django_filters.DateFilter(field_name="opened_at", lookup_expr="date__lte")

    class Meta:
        model = Shift
        fields = ["company", "register", "status", "opened_by"]


class ShiftViewSet(
    CompanyScopedViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """Shifts are opened and closed through actions, never edited."""

    queryset = Shift.objects.select_related(
        "register", "register__branch", "opened_by", "closed_by"
    )
    serializer_class = serializers.ShiftSerializer
    permission_resource = "pos.shift"
    action_permissions = {"open": "pos.shift.open", "close": "pos.shift.close"}
    branch_lookup = "register__branch"
    filterset_class = ShiftFilter
    search_fields = ["number", "register__code", "register__name"]
    ordering_fields = ["opened_at", "closed_at", "number"]

    def _fresh(self, pk):
        return self.get_serializer(self.get_queryset().get(pk=pk)).data

    @extend_schema(request=serializers.OpenShiftSerializer)
    @action(detail=False, methods=["post"])
    def open(self, request):
        data = serializers.OpenShiftSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        register = data.validated_data["register"]
        user = request.user
        if not user.is_superuser and not user.has_erp_permission(
            "pos.shift.open", company_id=register.company_id, branch_id=register.branch_id
        ):
            raise PermissionDenied("You can't open a shift on this register.")
        try:
            shift = open_shift(register, user, data.validated_data["opening_cash"])
        except PosError as exc:
            _raise(exc)
        return Response(self._fresh(shift.pk), status=status.HTTP_201_CREATED)

    @extend_schema(request=serializers.CloseShiftSerializer)
    @action(detail=True, methods=["post"])
    def close(self, request, pk=None):
        shift = self.get_object()
        data = serializers.CloseShiftSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            close_shift(
                shift,
                request.user,
                data.validated_data["closing_cash"],
                data.validated_data.get("notes", ""),
            )
        except PosError as exc:
            _raise(exc)
        return Response(self._fresh(pk))


class SaleFilter(django_filters.FilterSet):
    date_from = django_filters.DateFilter(field_name="date", lookup_expr="gte")
    date_to = django_filters.DateFilter(field_name="date", lookup_expr="lte")
    register = django_filters.NumberFilter(field_name="shift__register_id")

    class Meta:
        model = Sale
        fields = ["company", "branch", "shift", "customer", "status"]


class SaleViewSet(
    CompanyScopedViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Sale.objects.select_related(
        "customer",
        "branch",
        "shift",
        "shift__register",
        "invoice",
        "credit_note",
        "created_by",
    ).prefetch_related(
        "invoice__lines__product__uom",
        Prefetch("payments", queryset=SalePayment.objects.select_related("receipt")),
    )
    serializer_class = serializers.SaleSerializer
    permission_resource = "pos.sale"
    action_permissions = {"checkout": "pos.sale.create", "void": "pos.sale.void"}
    branch_lookup = "branch"
    filterset_class = SaleFilter
    search_fields = ["number", "customer__name", "customer__mobile"]
    ordering_fields = ["date", "number", "total", "created_at"]

    def _fresh(self, pk):
        return self.get_serializer(self.get_queryset().get(pk=pk)).data

    @extend_schema(request=serializers.CartSerializer)
    @action(detail=False, methods=["post"])
    def checkout(self, request):
        data = serializers.CartSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        cart = data.validated_data
        shift: Shift = cart["shift"]
        user = request.user
        if not user.is_superuser and not user.has_erp_permission(
            "pos.sale.create", company_id=shift.company_id, branch_id=shift.register.branch_id
        ):
            raise PermissionDenied("You can't sell on this register.")
        try:
            sale = make_sale(
                shift,
                user,
                [dict(line) for line in cart["lines"]],
                [dict(payment) for payment in cart["payments"]],
                customer=cart.get("customer"),
                reference=cart.get("reference", ""),
            )
        except PosError as exc:
            _raise(exc)
        return Response(self._fresh(sale.pk), status=status.HTTP_201_CREATED)

    @extend_schema(request=serializers.VoidSerializer)
    @action(detail=True, methods=["post"])
    def void(self, request, pk=None):
        sale = self.get_object()
        data = serializers.VoidSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            void_sale(sale, request.user, data.validated_data["reason"])
        except PosError as exc:
            _raise(exc)
        return Response(self._fresh(pk))
