from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("sales.quotation", "quotations"),
    *crud("sales.invoice", "invoices"),
    PermissionDef("sales.invoice.post", "Post invoices"),
    PermissionDef("sales.invoice.cancel", "Cancel posted invoices"),
    *crud("sales.delivery", "delivery notes"),
    PermissionDef("sales.delivery.post", "Post delivery notes (issue stock)"),
    *crud("sales.receipt", "customer receipts"),
    PermissionDef("sales.receipt.post", "Post customer receipts"),
    *crud("sales.credit_note", "credit notes"),
    PermissionDef("sales.credit_note.post", "Post credit notes"),
    PermissionDef("sales.discount.approve", "Give discounts above the company limit"),
    PermissionDef("sales.settings.view", "View sales settings"),
    PermissionDef("sales.settings.update", "Change sales settings"),
]
