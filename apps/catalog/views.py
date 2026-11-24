from decimal import Decimal, InvalidOperation

from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

from apps.core.csv_import import read_rows, run_import
from apps.core.images import image_action
from apps.core.viewsets import AuditedModelViewSet, CompanyScopedModelViewSet

from . import serializers
from .models import Brand, PriceList, PriceListItem, Product, ProductCategory, UnitOfMeasure
from .services import get_unit_price


class BrandViewSet(AuditedModelViewSet):
    queryset = Brand.objects.all()
    serializer_class = serializers.BrandSerializer
    permission_resource = "catalog.brand"
    filterset_fields = ["is_active", "is_own_brand"]
    search_fields = ["code", "name", "name_ar"]


class ProductCategoryViewSet(AuditedModelViewSet):
    queryset = ProductCategory.objects.annotate(product_count=Count("products"))
    serializer_class = serializers.ProductCategorySerializer
    permission_resource = "catalog.category"
    action_permissions = {"image": "catalog.category.update"}
    filterset_fields = ["is_active", "group", "parent"]
    search_fields = ["code", "name", "name_ar"]

    image = image_action("image", "catalog.category.update")


class UnitOfMeasureViewSet(AuditedModelViewSet):
    queryset = UnitOfMeasure.objects.all()
    serializer_class = serializers.UnitOfMeasureSerializer
    permission_resource = "catalog.unit"
    filterset_fields = ["is_active"]
    search_fields = ["code", "name", "name_ar"]


class ProductViewSet(AuditedModelViewSet):
    queryset = Product.objects.select_related("category", "brand", "uom")
    serializer_class = serializers.ProductSerializer
    permission_resource = "catalog.product"
    filterset_fields = [
        "is_active",
        "product_type",
        "category",
        "brand",
        "application",
        "dimming",
        "ip_rating",
        "model_code",
    ]
    action_permissions = {"image": "catalog.product.update", "import_csv": "catalog.product.create"}
    search_fields = ["sku", "model_code", "barcode", "name", "name_ar"]
    ordering_fields = ["sku", "name", "wattage", "created_at"]

    image = image_action("image", "catalog.product.update")

    @extend_schema(request=None, responses=None)
    @action(detail=False, methods=["post"], url_path="import")
    def import_csv(self, request):
        """Add or update products from a CSV file, matched by SKU.

        Columns: sku*, name*, category*, name_ar, brand, unit, barcode, model_code, rack_no.
        A category or brand that doesn't exist yet is created. `price` fills the default price
        list of the company given as ?company=."""
        rows = read_rows(request, ["sku", "name", "category"])
        units = {unit.code.lower(): unit for unit in UnitOfMeasure.objects.all()}
        price_list = None
        if request.query_params.get("company"):
            price_list = PriceList.objects.filter(
                company_id=request.query_params["company"], is_default=True
            ).first()
        user = request.user

        def lookup(model, label, max_length):
            if not label:
                return None
            found = (
                model.objects.filter(name__iexact=label).first()
                or model.objects.filter(code__iexact=label).first()
            )
            if found:
                return found
            code = slugify(label)[:max_length] or f"x{model.objects.count() + 1}"
            while model.objects.filter(code=code).exists():
                code = f"{code[: max_length - 4]}-{model.objects.count() + 1}"
            return model.objects.create(code=code, name=label, created_by=user, updated_by=user)

        def save_row(row):
            sku, name = row.get("sku"), row.get("name")
            if not sku or not name:
                raise ValueError("sku and name are required.")
            if not row.get("category"):
                raise ValueError("category is required.")
            unit = units.get((row.get("unit") or "pcs").lower())
            if unit is None:
                raise ValueError(
                    f"Unknown unit {row.get('unit')!r}. Use one of: {', '.join(sorted(units))}."
                )
            values = {
                "name": name[:200],
                "name_ar": row.get("name_ar", "")[:200],
                "category": lookup(ProductCategory, row["category"], 40),
                "brand": lookup(Brand, row.get("brand"), 30),
                "uom": unit,
                "barcode": row.get("barcode") or None,
                "model_code": row.get("model_code", "")[:50],
                "rack_no": row.get("rack_no", "")[:40],
                "updated_by": user,
            }
            product, created = Product.objects.update_or_create(
                sku=sku[:50], defaults=values, create_defaults={**values, "created_by": user}
            )
            if row.get("price") and price_list is not None:
                try:
                    price = Decimal(row["price"])
                except InvalidOperation as exc:
                    raise ValueError(f"price {row['price']!r} is not a number.") from exc
                PriceListItem.objects.update_or_create(
                    price_list=price_list,
                    product=product,
                    min_quantity=1,
                    defaults={"unit_price": price, "updated_by": user},
                    create_defaults={"unit_price": price, "created_by": user, "updated_by": user},
                )
            return "created" if created else "updated"

        return Response(run_import(rows, save_row))


class PriceListViewSet(CompanyScopedModelViewSet):
    branch_roles_cover_company = True
    queryset = PriceList.objects.select_related("company", "currency").annotate(
        item_count=Count("items")
    )
    serializer_class = serializers.PriceListSerializer
    permission_resource = "catalog.price_list"
    action_permissions = {"price": "catalog.price_list.view"}
    filterset_fields = ["company", "currency", "is_active", "is_default"]
    search_fields = ["code", "name", "name_ar"]

    @extend_schema(
        parameters=[
            OpenApiParameter("product", int, required=True),
            OpenApiParameter("quantity", str, description="Defaults to 1"),
            OpenApiParameter("date", str, description="YYYY-MM-DD, defaults to today"),
        ]
    )
    @action(detail=True, methods=["get"])
    def price(self, request, pk=None):
        """The unit price this list gives a product for a quantity on a date."""
        price_list = self.get_object()
        params = request.query_params
        try:
            product = Product.objects.get(pk=params.get("product"))
            quantity = Decimal(params.get("quantity") or "1")
        except (Product.DoesNotExist, ValueError, InvalidOperation) as exc:
            raise ValidationError({"product": "Give a valid product and quantity."}) from exc
        on_date = parse_date(params.get("date") or "") or timezone.localdate()
        unit_price = get_unit_price(price_list, product, quantity, on_date)
        return Response(
            {
                "price_list": price_list.pk,
                "product": product.pk,
                "quantity": str(quantity),
                "date": on_date.isoformat(),
                "currency": price_list.currency_id,
                "unit_price": None if unit_price is None else str(unit_price),
            }
        )


class PriceListItemViewSet(CompanyScopedModelViewSet):
    """Prices inside a price list. Viewing needs price_list.view; changing needs price_list.update."""

    branch_roles_cover_company = True

    queryset = PriceListItem.objects.select_related(
        "price_list", "price_list__currency", "product", "product__uom"
    )
    serializer_class = serializers.PriceListItemSerializer
    company_lookup = "price_list__company"
    action_permissions = {
        "list": "catalog.price_list.view",
        "retrieve": "catalog.price_list.view",
        "create": "catalog.price_list.update",
        "update": "catalog.price_list.update",
        "partial_update": "catalog.price_list.update",
        "destroy": "catalog.price_list.update",
    }
    filterset_fields = ["price_list", "product"]
    search_fields = ["product__sku", "product__name"]

    def target_company(self, data, instance=None):
        price_list = data.get("price_list") or getattr(instance, "price_list", None)
        return price_list.company if price_list else None
