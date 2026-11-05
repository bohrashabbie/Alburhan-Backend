from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("purchasing.order", "purchase orders"),
    PermissionDef("purchasing.order.send", "Send purchase orders to suppliers"),
    *crud("purchasing.receipt", "goods receipts"),
    PermissionDef("purchasing.receipt.post", "Post goods receipts (stock in)"),
    *crud("purchasing.invoice", "supplier bills"),
    PermissionDef("purchasing.invoice.post", "Post supplier bills"),
    PermissionDef("purchasing.invoice.cancel", "Cancel posted supplier bills"),
    *crud("purchasing.payment", "supplier payments"),
    PermissionDef("purchasing.payment.post", "Post supplier payments"),
]
