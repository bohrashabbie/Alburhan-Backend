from apps.access.catalog import crud

PERMISSIONS = [
    *crud("catalog.brand", "brands"),
    *crud("catalog.category", "product categories"),
    *crud("catalog.unit", "units of measure"),
    *crud("catalog.product", "products"),
    *crud("catalog.price_list", "price lists and prices"),
]
