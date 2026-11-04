"""Answers "may this user do X, and where?" from their active UserRole assignments.

Grants are loaded once per user object (i.e. once per request) and cached on it.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Grant:
    company_id: int
    branch_id: int | None  # None = whole company
    permissions: frozenset[str]


def load_grants(user) -> list[Grant]:
    """The user's active grants: from the request-level attribute, else the shared cache
    (Redis in production), else the database."""
    cached = getattr(user, "_erp_grants", None)
    if cached is not None:
        return cached

    from .cache import get_cached_grants, set_cached_grants

    grants = get_cached_grants(user.pk)
    if grants is None:
        grants = _query_grants(user)
        set_cached_grants(user.pk, grants)
    user._erp_grants = grants
    return grants


def _query_grants(user) -> list[Grant]:
    from .models import RolePermission, UserRole

    assignments = list(
        UserRole.objects.filter(
            user=user, is_active=True, role__is_active=True, company__is_active=True
        ).values_list("role_id", "company_id", "branch_id")
    )
    role_ids = {role_id for role_id, _, _ in assignments}
    codes_by_role: dict[int, set[str]] = {role_id: set() for role_id in role_ids}
    for role_id, code in RolePermission.objects.filter(
        role_id__in=role_ids, permission__is_active=True
    ).values_list("role_id", "permission__code"):
        codes_by_role[role_id].add(code)

    return [
        Grant(company_id, branch_id, frozenset(codes_by_role[role_id]))
        for role_id, company_id, branch_id in assignments
    ]


def has_permission(user, code: str, company_id: int | None = None, branch_id: int | None = None):
    """True if the user holds `code`:
    - anywhere, when no company is given;
    - company-wide in that company, when only a company is given;
    - company-wide or for that exact branch, when a branch is given too.
    """
    if not user.is_active:
        return False
    if user.is_superuser:
        return True
    for grant in load_grants(user):
        if code not in grant.permissions:
            continue
        if company_id is None:
            return True
        if grant.company_id != company_id:
            continue
        if grant.branch_id is None or grant.branch_id == branch_id:
            return True
    return False


def permission_scope(user, code: str) -> tuple[set[int], set[int]]:
    """(company ids where `code` is held company-wide, branch ids where it is held per-branch)."""
    companies: set[int] = set()
    branches: set[int] = set()
    for grant in load_grants(user):
        if code in grant.permissions:
            if grant.branch_id is None:
                companies.add(grant.company_id)
            else:
                branches.add(grant.branch_id)
    return companies, branches


def companies_with(user, code: str) -> set[int]:
    """Companies where `code` is held at all, company-wide or for any one branch."""
    return {grant.company_id for grant in load_grants(user) if code in grant.permissions}


def clear_cache(user) -> None:
    for attr in ("_erp_grants", "_allowed_company_ids", "_allowed_branch_ids"):
        if hasattr(user, attr):
            delattr(user, attr)
