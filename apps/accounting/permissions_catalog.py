from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("accounting.account", "chart of accounts"),
    *crud("accounting.journal", "journals"),
    *crud("accounting.period", "accounting periods"),
    PermissionDef("accounting.period.close", "Close and reopen accounting periods"),
    *crud("accounting.entry", "journal entries (drafts)"),
    PermissionDef("accounting.entry.post", "Post journal entries"),
    PermissionDef("accounting.entry.reverse", "Reverse posted journal entries"),
    PermissionDef("accounting.settings.view", "View default accounts"),
    PermissionDef("accounting.settings.update", "Change default accounts"),
    PermissionDef("accounting.report.view", "View financial reports"),
]
