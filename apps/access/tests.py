import pytest

from apps.access.catalog import ADMIN_ROLE_CODE, sync_permissions
from apps.access.models import Permission, Role, UserRole

pytestmark = pytest.mark.django_db


def ids(response):
    return {row["id"] for row in response.json()["results"]}


def test_sync_creates_catalogue_and_admin_role_gets_everything():
    sync_permissions()
    admin = Role.objects.get(code=ADMIN_ROLE_CODE)
    active = Permission.objects.filter(is_active=True)
    assert active.filter(code="organization.branch.create").exists()
    assert admin.is_system
    assert set(admin.permissions.all()) == set(active)


def test_sync_deactivates_permissions_removed_from_code():
    Permission.objects.create(code="legacy.thing.view", module="legacy", name="Old")
    sync_permissions()
    assert not Permission.objects.get(code="legacy.thing.view").is_active


def test_company_wide_role_sees_only_its_company(api, make_role, make_user, kuwait, warehouses):
    role = make_role("storekeeper", "organization.warehouse.view")
    user = make_user((role, kuwait, None))
    api.force_authenticate(user)

    response = api.get("/api/v1/organization/warehouses/")

    assert response.status_code == 200
    assert ids(response) == {
        warehouses["kw_main"].id,
        warehouses["kw_sr1"].id,
        warehouses["kw_sr2"].id,
    }


def test_branch_role_sees_only_its_branch(api, make_role, make_user, kuwait, showroom1, warehouses):
    role = make_role("salesperson", "organization.warehouse.view")
    user = make_user((role, kuwait, showroom1))
    api.force_authenticate(user)

    response = api.get("/api/v1/organization/warehouses/")

    assert ids(response) == {warehouses["kw_sr1"].id}


def test_permission_is_checked_per_company(api, make_role, make_user, kuwait, uae, warehouses):
    viewer = make_role("viewer", "organization.warehouse.view")
    editor = make_role("editor", "organization.warehouse.view", "organization.warehouse.update")
    user = make_user((viewer, kuwait, None), (editor, uae, None))
    api.force_authenticate(user)

    kw = api.patch(f"/api/v1/organization/warehouses/{warehouses['kw_main'].id}/", {"name": "X"})
    ae = api.patch(f"/api/v1/organization/warehouses/{warehouses['ae_main'].id}/", {"name": "X"})

    assert kw.status_code == 404  # not visible for update in Kuwait
    assert ae.status_code == 200


def test_cannot_create_in_company_without_permission(api, make_role, make_user, kuwait, uae):
    role = make_role("creator", "organization.warehouse.create")
    user = make_user((role, kuwait, None))
    api.force_authenticate(user)
    payload = {"code": "NEW", "name": "New", "warehouse_type": "main"}

    allowed = api.post("/api/v1/organization/warehouses/", {**payload, "company": kuwait.id})
    denied = api.post("/api/v1/organization/warehouses/", {**payload, "company": uae.id})

    assert allowed.status_code == 201
    assert denied.status_code == 403


def test_branch_role_cannot_create_company_level_rows(
    api, make_role, make_user, kuwait, showroom1, showroom2
):
    role = make_role("branch-creator", "organization.warehouse.create")
    user = make_user((role, kuwait, showroom1))
    api.force_authenticate(user)
    base = {"company": kuwait.id, "warehouse_type": "showroom", "name": "W"}

    own = api.post(
        "/api/v1/organization/warehouses/", {**base, "code": "A", "branch": showroom1.id}
    )
    other = api.post(
        "/api/v1/organization/warehouses/", {**base, "code": "B", "branch": showroom2.id}
    )
    no_branch = api.post("/api/v1/organization/warehouses/", {**base, "code": "C"})

    assert own.status_code == 201
    assert other.status_code == 403
    assert no_branch.status_code == 403


def test_user_without_permission_is_denied(api, make_user, warehouses):
    api.force_authenticate(make_user())
    assert api.get("/api/v1/organization/warehouses/").status_code == 403


def test_reference_data_is_readable_by_any_signed_in_user(api, make_user):
    api.force_authenticate(make_user())
    assert api.get("/api/v1/organization/currencies/").status_code == 200
    assert (
        api.post("/api/v1/organization/currencies/", {"code": "GBP", "name": "Pound"}).status_code
        == 403
    )


def test_cannot_assign_role_with_permissions_you_do_not_hold(api, make_role, make_user, kuwait):
    sync_permissions()
    hr = make_role(
        "hr",
        "access.user_role.create",
        "access.user_role.view",
        "organization.warehouse.view",
    )
    actor = make_user((hr, kuwait, None))
    target = make_user()
    admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
    weaker = make_role("viewer", "organization.warehouse.view")
    api.force_authenticate(actor)

    escalate = api.post(
        "/api/v1/access/user-roles/",
        {"user": target.id, "role": admin_role.id, "company": kuwait.id},
    )
    ok = api.post(
        "/api/v1/access/user-roles/", {"user": target.id, "role": weaker.id, "company": kuwait.id}
    )

    assert escalate.status_code == 400
    assert ok.status_code == 201
    assert UserRole.objects.filter(user=target).count() == 1


def test_duplicate_assignment_rejected(api, make_role, make_user, kuwait):
    role = make_role("viewer", "organization.warehouse.view")
    target = make_user((role, kuwait, None))
    api.force_authenticate(make_user(is_superuser=True))

    response = api.post(
        "/api/v1/access/user-roles/", {"user": target.id, "role": role.id, "company": kuwait.id}
    )

    assert response.status_code == 400


def test_role_permissions_set_by_codes(api, make_user):
    sync_permissions()
    api.force_authenticate(make_user(is_superuser=True))

    created = api.post(
        "/api/v1/access/roles/",
        {"code": "accountant", "name": "Accountant", "permissions": ["organization.company.view"]},
        format="json",
    )
    role_id = created.json()["id"]
    updated = api.patch(
        f"/api/v1/access/roles/{role_id}/",
        {"permissions": ["organization.branch.view", "organization.warehouse.view"]},
        format="json",
    )

    assert created.status_code == 201
    assert sorted(updated.json()["permissions"]) == [
        "organization.branch.view",
        "organization.warehouse.view",
    ]


def test_system_role_cannot_be_edited(api, make_user):
    sync_permissions()
    admin_role = Role.objects.get(code=ADMIN_ROLE_CODE)
    api.force_authenticate(make_user(is_superuser=True))

    response = api.patch(f"/api/v1/access/roles/{admin_role.id}/", {"name": "x"})

    assert response.status_code == 400


def test_me_lists_access_per_assignment(api, make_role, make_user, kuwait, showroom1):
    role = make_role("salesperson", "organization.warehouse.view")
    user = make_user((role, kuwait, showroom1))
    api.force_authenticate(user)

    body = api.get("/api/v1/me/").json()

    assert body["companies"][0]["code"] == "KW"
    assert [b["code"] for b in body["branches"]] == ["SR1"]
    assert body["access"] == [
        {
            "company": kuwait.id,
            "branch": showroom1.id,
            "role": "salesperson",
            "role_name": "Salesperson",
            "permissions": ["organization.warehouse.view"],
        }
    ]


def test_cached_grants_follow_role_changes(
    api, make_role, make_user, kuwait, warehouses, django_capture_on_commit_callbacks
):
    viewer = make_role("viewer", "organization.warehouse.view")
    user = make_user()
    api.force_authenticate(user)

    assert api.get("/api/v1/organization/warehouses/").status_code == 403  # caches "no grants"

    with django_capture_on_commit_callbacks(execute=True):
        assignment = UserRole.objects.create(user=user, role=viewer, company=kuwait)
    user.__dict__.pop("_erp_grants", None)
    assert api.get("/api/v1/organization/warehouses/").status_code == 200

    # Removing the permission from the role reaches every holder at once.
    with django_capture_on_commit_callbacks(execute=True):
        viewer.role_permissions.all().delete()
    user.__dict__.pop("_erp_grants", None)
    assert api.get("/api/v1/organization/warehouses/").status_code == 403

    with django_capture_on_commit_callbacks(execute=True):
        assignment.delete()


def test_grants_are_served_from_cache(make_role, make_user, kuwait, django_assert_num_queries):
    from apps.access.services import load_grants

    role = make_role("viewer", "organization.warehouse.view")
    user = make_user((role, kuwait, None))
    load_grants(user)  # fills the cache
    user.__dict__.pop("_erp_grants", None)
    with django_assert_num_queries(0):
        assert load_grants(user)[0].company_id == kuwait.id
