import pytest

from apps.catalog.models import PriceList
from apps.partners.models import Contact, Customer

pytestmark = pytest.mark.django_db

CUSTOMERS = "/api/v1/partners/customers/"
SUPPLIERS = "/api/v1/partners/suppliers/"


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


def test_customer_codes_are_automatic_per_company(admin_api, kuwait, uae):
    first = admin_api.post(CUSTOMERS, {"company": kuwait.id, "name": "Ahmad"}).json()
    second = admin_api.post(CUSTOMERS, {"company": kuwait.id, "name": "Sara"}).json()
    other = admin_api.post(CUSTOMERS, {"company": uae.id, "name": "Omar"}).json()
    assert (first["code"], second["code"], other["code"]) == (
        "KW-CUS-00001",
        "KW-CUS-00002",
        "AE-CUS-00001",
    )


def test_manual_code_must_be_unique_in_company(admin_api, kuwait, uae):
    admin_api.post(CUSTOMERS, {"company": kuwait.id, "name": "A", "code": "vip-1"})
    clash = admin_api.post(CUSTOMERS, {"company": kuwait.id, "name": "B", "code": "VIP-1"})
    elsewhere = admin_api.post(CUSTOMERS, {"company": uae.id, "name": "C", "code": "VIP-1"})
    assert clash.status_code == 400
    assert "code" in clash.json()["fields"]
    assert elsewhere.status_code == 201


def test_contacts_saved_and_replaced(admin_api, kuwait):
    created = admin_api.post(
        CUSTOMERS,
        {
            "company": kuwait.id,
            "name": "Gulf Contracting",
            "customer_type": "contractor",
            "contacts": [
                {"name": "Khalid", "job_title": "Site engineer", "is_primary": True},
                {"name": "Mona", "phone": "+965 5000 0000"},
            ],
        },
        format="json",
    ).json()
    assert [c["name"] for c in created["contacts"]] == ["Khalid", "Mona"]

    updated = admin_api.patch(
        f"{CUSTOMERS}{created['id']}/", {"contacts": [{"name": "Yousef"}]}, format="json"
    ).json()
    assert [c["name"] for c in updated["contacts"]] == ["Yousef"]
    assert Contact.objects.count() == 1


def test_only_one_primary_contact(admin_api, kuwait):
    response = admin_api.post(
        CUSTOMERS,
        {
            "company": kuwait.id,
            "name": "X",
            "contacts": [{"name": "A", "is_primary": True}, {"name": "B", "is_primary": True}],
        },
        format="json",
    )
    assert response.status_code == 400
    assert "contacts" in response.json()["fields"]


def test_customer_price_list_must_be_same_company(admin_api, kuwait, uae):
    uae_list = PriceList.objects.create(company=uae, code="ae", name="AE", currency_id="AED")
    response = admin_api.post(
        CUSTOMERS, {"company": kuwait.id, "name": "X", "price_list": uae_list.id}
    )
    assert response.status_code == 400
    assert "price_list" in response.json()["fields"]


def test_negative_credit_limit_rejected(admin_api, kuwait):
    response = admin_api.post(CUSTOMERS, {"company": kuwait.id, "name": "X", "credit_limit": "-1"})
    assert response.status_code == 400
    assert "credit_limit" in response.json()["fields"]


def test_customer_cannot_move_company(admin_api, kuwait, uae):
    created = admin_api.post(CUSTOMERS, {"company": kuwait.id, "name": "X"}).json()
    response = admin_api.patch(f"{CUSTOMERS}{created['id']}/", {"company": uae.id})
    assert response.status_code == 400


def test_search_finds_customer_by_contact_and_mobile(admin_api, kuwait):
    admin_api.post(
        CUSTOMERS,
        {
            "company": kuwait.id,
            "name": "Al Noor Villas",
            "mobile": "99887766",
            "contacts": [{"name": "Fahad"}, {"name": "Fahad Jr"}],
        },
        format="json",
    )
    by_contact = admin_api.get(CUSTOMERS, {"search": "fahad"}).json()
    by_mobile = admin_api.get(CUSTOMERS, {"search": "998877"}).json()
    assert by_contact["count"] == 1  # one row even though two contacts match
    assert by_mobile["count"] == 1


def test_branch_salesperson_can_add_customers(api, make_role, make_user, kuwait, uae, showroom1):
    seller = make_role("seller", "partners.customer.view", "partners.customer.create")
    api.force_authenticate(make_user((seller, kuwait, showroom1)))

    mine = api.post(CUSTOMERS, {"company": kuwait.id, "name": "Walk-in buyer"})
    other_company = api.post(CUSTOMERS, {"company": uae.id, "name": "Not mine"})

    assert mine.status_code == 201
    assert other_company.status_code == 403
    assert api.get(CUSTOMERS).json()["count"] == 1


def test_customers_hidden_across_companies(api, make_role, make_user, kuwait, uae):
    Customer.objects.create(company=uae, code="AE-1", name="Dubai client")
    viewer = make_role("viewer", "partners.customer.view")
    api.force_authenticate(make_user((viewer, kuwait, None)))
    assert api.get(CUSTOMERS).json()["count"] == 0


def test_supplier_codes_and_intercompany(admin_api, kuwait, make_user):
    from apps.organization.models import Company

    china = Company.objects.create(
        code="CN", name="MS Lighting", country_id="CN", currency_id="CNY"
    )
    factory = admin_api.post(
        SUPPLIERS,
        {
            "company": kuwait.id,
            "name": "MS Lighting Jiangmen",
            "currency": "CNY",
            "country": "CN",
            "intercompany": china.id,
            "lead_time_days": 45,
        },
    )
    self_supply = admin_api.post(
        SUPPLIERS,
        {"company": kuwait.id, "name": "Self", "currency": "KWD", "intercompany": kuwait.id},
    )
    assert factory.status_code == 201
    assert factory.json()["code"] == "KW-SUP-00001"
    assert self_supply.status_code == 400
    assert "intercompany" in self_supply.json()["fields"]


def test_supplier_requires_company_wide_role(api, make_role, make_user, kuwait, showroom1):
    buyer = make_role("buyer", "partners.supplier.create")
    api.force_authenticate(make_user((buyer, kuwait, showroom1)))
    response = api.post(SUPPLIERS, {"company": kuwait.id, "name": "X", "currency": "KWD"})
    assert response.status_code == 403
