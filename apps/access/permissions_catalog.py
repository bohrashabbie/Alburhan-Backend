from .catalog import crud

PERMISSIONS = [
    *crud("access.role", "roles"),
    *crud("access.user_role", "user role assignments"),
]
