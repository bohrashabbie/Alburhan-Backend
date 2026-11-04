from apps.core.viewsets import CompanyScopedModelViewSet

from .models import Customer, Supplier
from .serializers import CustomerSerializer, SupplierSerializer


class CustomerViewSet(CompanyScopedModelViewSet):
    branch_roles_cover_company = True
    queryset = Customer.objects.select_related("company", "price_list").prefetch_related("contacts")
    serializer_class = CustomerSerializer
    permission_resource = "partners.customer"
    filterset_fields = ["company", "customer_type", "is_active", "salesperson", "price_list"]
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
