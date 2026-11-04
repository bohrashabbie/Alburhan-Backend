from decimal import Decimal

from rest_framework import serializers
from rest_framework.validators import UniqueValidator

from apps.core.images import image_url

from .models import (
    IK_RATING,
    IP_RATING,
    Brand,
    PriceList,
    PriceListItem,
    Product,
    ProductCategory,
    UnitOfMeasure,
)

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


class BrandSerializer(serializers.ModelSerializer):
    class Meta:
        model = Brand
        fields = [
            "id",
            "code",
            "name",
            "name_ar",
            "is_own_brand",
            "country",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS


class ProductCategorySerializer(serializers.ModelSerializer):
    product_count = serializers.IntegerField(read_only=True, default=0)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductCategory
        fields = [
            "id",
            "code",
            "name",
            "name_ar",
            "group",
            "parent",
            "image_url",
            "is_active",
            "product_count",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS

    def get_image_url(self, category) -> str | None:
        return image_url(category, "image")

    def validate_parent(self, parent):
        node = parent
        while node is not None:
            if self.instance and node.pk == self.instance.pk:
                raise serializers.ValidationError("A category cannot be inside itself.")
            node = node.parent
        return parent


class UnitOfMeasureSerializer(serializers.ModelSerializer):
    class Meta:
        model = UnitOfMeasure
        fields = ["id", "code", "name", "name_ar", "allow_decimal", "is_active", *AUDIT_FIELDS]
        read_only_fields = AUDIT_FIELDS


class UpperCharField(serializers.CharField):
    """Trims and upper-cases input before validation, so "ip44" is checked as "IP44"."""

    def to_internal_value(self, data):
        return super().to_internal_value(data).strip().upper()


class ProductSerializer(serializers.ModelSerializer):
    sku = UpperCharField(
        max_length=50,
        validators=[
            UniqueValidator(
                queryset=Product.objects.all(), message="Another product already has this SKU."
            )
        ],
    )
    ip_rating = UpperCharField(
        max_length=4, required=False, allow_blank=True, validators=[IP_RATING]
    )
    ik_rating = UpperCharField(
        max_length=4, required=False, allow_blank=True, validators=[IK_RATING]
    )
    category_name = serializers.CharField(source="category.name", read_only=True)
    brand_name = serializers.CharField(source="brand.name", read_only=True, default=None)
    uom_code = serializers.CharField(source="uom.code", read_only=True)
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id",
            "sku",
            "model_code",
            "barcode",
            "name",
            "name_ar",
            "description",
            "product_type",
            "category",
            "category_name",
            "brand",
            "brand_name",
            "uom",
            "uom_code",
            "application",
            "wattage",
            "lumens",
            "cct",
            "cri",
            "beam_angle",
            "ip_rating",
            "ik_rating",
            "input_voltage",
            "dimming",
            "driver",
            "led_source",
            "finish",
            "body_material",
            "dimensions",
            "cutout",
            "lifetime_hours",
            "warranty_months",
            "weight_kg",
            "image_url",
            "is_active",
            "notes",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {"barcode": {"allow_null": True, "required": False}}

    def get_image_url(self, product) -> str | None:
        return image_url(product, "image")

    def validate_barcode(self, value):
        value = (value or "").strip() or None
        if (
            value
            and Product.objects.exclude(pk=getattr(self.instance, "pk", None))
            .filter(barcode=value)
            .exists()
        ):
            raise serializers.ValidationError("Another product already has this barcode.")
        return value


class PriceListSerializer(serializers.ModelSerializer):
    item_count = serializers.IntegerField(read_only=True, default=0)

    class Meta:
        model = PriceList
        fields = [
            "id",
            "company",
            "code",
            "name",
            "name_ar",
            "currency",
            "is_default",
            "valid_from",
            "valid_to",
            "is_active",
            "item_count",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        validators = []  # uniqueness checked below for clean messages

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company, code = get("company"), get("code")
        start, end = get("valid_from"), get("valid_to")
        others = PriceList.objects.filter(company=company).exclude(
            pk=getattr(self.instance, "pk", None)
        )
        if others.filter(code=code).exists():
            raise serializers.ValidationError({"code": "This code is already used in the company."})
        if get("is_default") and others.filter(is_default=True).exists():
            raise serializers.ValidationError(
                {"is_default": "The company already has a default price list."}
            )
        if start and end and end < start:
            raise serializers.ValidationError({"valid_to": "Must be on or after the start date."})
        if self.instance and "currency" in attrs and attrs["currency"] != self.instance.currency:
            if self.instance.items.exists():
                raise serializers.ValidationError(
                    {"currency": "Remove the prices before changing the currency."}
                )
        return attrs


class PriceListItemSerializer(serializers.ModelSerializer):
    sku = serializers.CharField(source="product.sku", read_only=True)
    product_name = serializers.CharField(source="product.name", read_only=True)
    currency = serializers.CharField(source="price_list.currency_id", read_only=True)

    class Meta:
        model = PriceListItem
        fields = [
            "id",
            "price_list",
            "product",
            "sku",
            "product_name",
            "min_quantity",
            "unit_price",
            "currency",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        validators = []

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        price_list, product = get("price_list"), get("product")
        min_quantity, unit_price = get("min_quantity") or Decimal(1), get("unit_price")

        if unit_price is not None:
            if unit_price < 0:
                raise serializers.ValidationError({"unit_price": "Cannot be negative."})
            decimals = price_list.currency.decimal_places
            if unit_price != unit_price.quantize(Decimal(1).scaleb(-decimals)):
                raise serializers.ValidationError(
                    {"unit_price": f"{price_list.currency_id} allows {decimals} decimal places."}
                )
        if min_quantity <= 0:
            raise serializers.ValidationError({"min_quantity": "Must be greater than zero."})
        if product.product_type != product.ProductType.SERVICE and not product.uom.allow_decimal:
            if min_quantity != min_quantity.to_integral_value():
                raise serializers.ValidationError(
                    {"min_quantity": f"{product.uom.code} is counted in whole units."}
                )
        duplicate = PriceListItem.objects.filter(
            price_list=price_list, product=product, min_quantity=min_quantity
        ).exclude(pk=getattr(self.instance, "pk", None))
        if duplicate.exists():
            raise serializers.ValidationError(
                {"product": "This product already has a price for that quantity in this list."}
            )
        return attrs
