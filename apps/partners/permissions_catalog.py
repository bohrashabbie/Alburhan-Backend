from apps.access.catalog import crud

PERMISSIONS = [
    *crud("partners.customer", "customers"),
    *crud("partners.supplier", "suppliers"),
]
