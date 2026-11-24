from apps.access.catalog import PermissionDef

PERMISSIONS = [
    PermissionDef("reports.dashboard.view", "View the management dashboard"),
    PermissionDef("reports.compliance.view", "View VAT returns and compliance exports"),
    PermissionDef("reports.legacy.view", "Look up documents from the previous system"),
]
