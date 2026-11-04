from django.contrib import admin

from .models import Brand, PriceList, PriceListItem, Product, ProductCategory, UnitOfMeasure


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "is_own_brand", "is_active"]
    search_fields = ["code", "name"]


@admin.register(ProductCategory)
class ProductCategoryAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "group", "parent", "is_active"]
    list_filter = ["group", "is_active"]
    search_fields = ["code", "name"]


@admin.register(UnitOfMeasure)
class UnitOfMeasureAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "allow_decimal", "is_active"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["sku", "name", "category", "brand", "wattage", "cct", "ip_rating", "is_active"]
    list_filter = ["product_type", "category", "brand", "application", "is_active"]
    search_fields = ["sku", "model_code", "barcode", "name", "name_ar"]


class PriceListItemInline(admin.TabularInline):
    model = PriceListItem
    extra = 0
    autocomplete_fields = ["product"]


@admin.register(PriceList)
class PriceListAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "company", "currency", "is_default", "is_active"]
    list_filter = ["company", "is_active"]
    inlines = [PriceListItemInline]
