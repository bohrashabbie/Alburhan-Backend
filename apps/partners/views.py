from django.db.models import Count
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from apps.core.csv_import import read_rows, run_import
from apps.core.viewsets import CompanyScopedModelViewSet
from apps.organization.models import Company
from apps.organization.services import next_document_number

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

    action_permissions = {"import_csv": "partners.customer.create"}

    @action(detail=False, methods=["post"], url_path="import")
    def import_csv(self, request):
        """Add or update customers of ?company= from a CSV file, matched by mobile (or by name
        when there is no mobile).

        Columns: name*, name_ar, type (individual, business, contractor, government), mobile,
        phone, email, area, address, group."""
        company = Company.objects.filter(pk=request.query_params.get("company") or 0).first()
        if company is None:
            raise ValidationError({"company": "Choose a company."})
        user = request.user
        if not user.has_erp_permission("partners.customer.create", company_id=company.pk):
            raise PermissionDenied("You can't add customers in that company.")
        rows = read_rows(request, ["name"])
        groups = {
            group.name.lower(): group for group in CustomerGroup.objects.filter(company=company)
        }
        types = {choice for choice, _label in Customer.CustomerType.choices}

        def save_row(row):
            name = row.get("name")
            if not name:
                raise ValueError("name is required.")
            kind = (row.get("type") or "individual").lower()
            if kind not in types:
                raise ValueError(f"Unknown type {row.get('type')!r}.")
            group = None
            if row.get("group"):
                group = groups.get(row["group"].lower())
                if group is None:
                    group = CustomerGroup.objects.create(
                        company=company, name=row["group"][:100], created_by=user, updated_by=user
                    )
                    groups[group.name.lower()] = group
            values = {
                "name": name[:200],
                "name_ar": row.get("name_ar", "")[:200],
                "customer_type": kind,
                "mobile": row.get("mobile", "")[:32],
                "phone": row.get("phone", "")[:32],
                "email": row.get("email", ""),
                "area": row.get("area", "")[:80],
                "address": row.get("address", ""),
                "group": group,
                "updated_by": user,
            }
            match = (
                Customer.objects.filter(company=company, mobile=values["mobile"])
                if values["mobile"]
                else Customer.objects.filter(company=company, name__iexact=name, mobile="")
            )
            existing = match.first()
            if existing:
                for field, value in values.items():
                    setattr(existing, field, value)
                existing.save()
                return "updated"
            Customer.objects.create(
                company=company,
                code=next_document_number(company, "CUS", yearly=False),
                created_by=user,
                **values,
            )
            return "created"

        return Response(run_import(rows, save_row))


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
