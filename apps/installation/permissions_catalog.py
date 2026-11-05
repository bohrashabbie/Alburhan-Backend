from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("installation.technician", "technicians"),
    *crud("installation.work_order", "installation work orders"),
    PermissionDef("installation.work_order.complete", "Complete installation jobs"),
]
