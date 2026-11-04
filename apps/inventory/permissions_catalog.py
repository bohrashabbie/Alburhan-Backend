from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    PermissionDef("inventory.stock.view", "View stock levels and movements"),
    *crud("inventory.adjustment", "stock adjustments"),
    PermissionDef("inventory.adjustment.post", "Post stock adjustments"),
    *crud("inventory.transfer", "stock transfers"),
    PermissionDef("inventory.transfer.dispatch", "Send stock transfers (transfer out)"),
    PermissionDef("inventory.transfer.receive", "Receive stock transfers (transfer in)"),
    *crud("inventory.count", "stock counts"),
    PermissionDef("inventory.count.post", "Post stock counts"),
    *crud("inventory.reservation", "stock reservations"),
    *crud("inventory.reorder_rule", "reorder rules"),
    PermissionDef("inventory.report.view", "View reorder and slow-moving reports"),
]
