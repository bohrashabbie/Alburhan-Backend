from apps.access.catalog import crud

PERMISSIONS = [
    *crud("users.user", "users"),
]
