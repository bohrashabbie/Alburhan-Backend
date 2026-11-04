import pytest
from rest_framework.test import APIClient

from apps.access.models import Permission, Role, RolePermission, UserRole
from apps.organization.models import Branch, Company, Warehouse
from apps.users.models import User


@pytest.fixture(autouse=True)
def _fresh_cache():
    """Each test starts with an empty cache, like a freshly started server."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def kuwait(db):
    return Company.objects.create(code="KW", name="Kuwait", country_id="KW", currency_id="KWD")


@pytest.fixture
def uae(db):
    return Company.objects.create(code="AE", name="UAE", country_id="AE", currency_id="AED")


@pytest.fixture
def showroom1(kuwait):
    return Branch.objects.create(
        company=kuwait, code="SR1", name="Showroom 1", branch_type=Branch.BranchType.SHOWROOM
    )


@pytest.fixture
def showroom2(kuwait):
    return Branch.objects.create(
        company=kuwait, code="SR2", name="Showroom 2", branch_type=Branch.BranchType.SHOWROOM
    )


@pytest.fixture
def warehouses(kuwait, uae, showroom1, showroom2):
    return {
        "kw_main": Warehouse.objects.create(
            company=kuwait, code="MAIN", name="Main", warehouse_type="main"
        ),
        "kw_sr1": Warehouse.objects.create(
            company=kuwait, code="SR1", name="SR1", warehouse_type="showroom", branch=showroom1
        ),
        "kw_sr2": Warehouse.objects.create(
            company=kuwait, code="SR2", name="SR2", warehouse_type="showroom", branch=showroom2
        ),
        "ae_main": Warehouse.objects.create(
            company=uae, code="MAIN", name="Dubai", warehouse_type="main"
        ),
    }


@pytest.fixture
def make_role(db):
    def _make(code, *permission_codes):
        role = Role.objects.create(code=code, name=code.title())
        for permission_code in permission_codes:
            RolePermission.objects.create(
                role=role, permission=Permission.objects.get(code=permission_code)
            )
        return role

    return _make


@pytest.fixture
def make_user(db):
    counter = {"n": 0}

    def _make(*assignments, **fields):
        """assignments: (role, company, branch_or_None) tuples."""
        counter["n"] += 1
        user = User.objects.create_user(
            username=fields.pop("username", f"user{counter['n']}"),
            email=fields.pop("email", f"user{counter['n']}@example.com"),
            password="pass-12345",
            **fields,
        )
        for role, company, branch in assignments:
            UserRole.objects.create(user=user, role=role, company=company, branch=branch)
        return user

    return _make
