from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("installation.technician", "technicians"),
    *crud("installation.work_order", "installation requests"),
    PermissionDef(
        "installation.work_order.schedule",
        "Schedule installation jobs",
        "Set the date and time and choose the technicians (installation supervisor).",
    ),
    PermissionDef(
        "installation.work_order.complete",
        "Complete installation jobs",
        "Start and complete own jobs and take the customer's signature (technician).",
    ),
]
