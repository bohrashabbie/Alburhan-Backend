from django.db.models import Count
from rest_framework.exceptions import ValidationError

from apps.core.viewsets import CompanyScopedModelViewSet

from .models import Customer, CustomerGroup, Supplier
from .serializers import CustomerGroupSerializer, CustomerSerializer, SupplierSerializer


class CustomerViewSet(CompanyScopedModelViewSet):
    branch_roles_cover_company = True
    queryset = Customer.objects.select_related("company", "price_list", "group").prefetch_related(
        "contacts"
    )
    serializer_class = CustomerSerializer
    permission_resource = "partners.customer"
    filterset_fields = [
        "company",
        "customer_type",
        "is_active",
        "salesperson",
        "price_list",
        "group",
    ]
    search_fields = ["code", "name", "name_ar", "phone", "mobile", "email", "contacts__name"]
    ordering_fields = ["code", "name", "created_at"]

    def get_queryset(self):
        # Searching by contact name joins contacts; keep one row per customer.
        queryset = super().get_queryset()
        return queryset.distinct() if self.request.query_params.get("search") else queryset


class SupplierViewSet(CompanyScopedModelViewSet):
    queryset = Supplier.objects.select_related("company", "currency").prefetch_related("contacts")
    serializer_class = SupplierSerializer
    permission_resource = "partners.supplier"
    filterset_fields = ["company", "country", "currency", "is_active", "intercompany"]
    search_fields = ["code", "name", "name_ar", "phone", "email", "contacts__name"]
    ordering_fields = ["code", "name", "created_at"]

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.distinct() if self.request.query_params.get("search") else queryset


class CustomerGroupViewSet(CompanyScopedModelViewSet):
    branch_roles_cover_company = True
    queryset = CustomerGroup.objects.select_related("company").annotate(
        customer_count=Count("customers")
    )
    serializer_class = CustomerGroupSerializer
    permission_resource = "partners.customer_group"
    filterset_fields = ["company", "is_active"]
    search_fields = ["name", "name_ar"]

    def perform_destroy(self, instance):
        if instance.customers.exists():
            raise ValidationError(
                {"detail": "Customers are in this group. Move them or switch the group off."}
            )
        super().perform_destroy(instance)
