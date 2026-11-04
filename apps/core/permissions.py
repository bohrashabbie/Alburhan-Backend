"""API permission checks driven by the ERP's own Role / Permission / UserRole tables (apps.access).

Each viewset names a `permission_resource` such as "organization.branch"; standard actions map to
"<resource>.view|create|update|delete". Custom actions must be listed in `action_permissions`,
otherwise they are denied. Which company/branch rows a user sees is enforced separately by
`CompanyScopedViewSetMixin` using the same permission code.
"""

from rest_framework.permissions import BasePermission

ACTION_VERBS = {
    "list": "view",
    "retrieve": "view",
    "create": "create",
    "update": "update",
    "partial_update": "update",
    "destroy": "delete",
}

# Marker for actions any signed-in user may call (e.g. reading shared reference data).
OPEN = "__open__"


def required_permission(view) -> str | None:
    """The permission code the current action needs, OPEN, or None when undeclared (deny)."""
    action = getattr(view, "action", None)
    explicit = getattr(view, "action_permissions", {})
    if action in explicit:
        return explicit[action]
    if action is None:  # OPTIONS / metadata requests
        return OPEN
    resource = getattr(view, "permission_resource", None)
    verb = ACTION_VERBS.get(action)
    if resource and verb:
        return f"{resource}.{verb}"
    return None


class ErpPermission(BasePermission):
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        code = required_permission(view)
        if code == OPEN:
            return True
        if code is None:
            return False
        # Holding it in at least one company; row-level company scope is applied by the viewset.
        return user.has_erp_permission(code)
