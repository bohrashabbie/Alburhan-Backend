from apps.access.catalog import crud

PERMISSIONS = [
    *crud("organization.currency", "currencies"),
    *crud("organization.country", "countries"),
    *crud("organization.company", "companies"),
    *crud("organization.branch", "branches"),
    *crud("organization.warehouse", "warehouses"),
    *crud("organization.exchange_rate", "exchange rates"),
    *crud("organization.document_sequence", "document numbering"),
]
