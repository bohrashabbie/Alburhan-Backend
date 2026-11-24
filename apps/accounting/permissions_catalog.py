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
    *crud("accounting.expense_category", "expense categories"),
    PermissionDef("accounting.expense.view", "View expenses"),
    PermissionDef("accounting.expense.create", "Record expenses"),
    PermissionDef("accounting.expense.cancel", "Cancel expenses"),
    PermissionDef(
        "accounting.money_transfer.view", "View cash and bank accounts and money transfers"
    ),
    PermissionDef("accounting.money_transfer.create", "Move money between cash and bank accounts"),
    PermissionDef("accounting.money_transfer.cancel", "Cancel money transfers"),
]
