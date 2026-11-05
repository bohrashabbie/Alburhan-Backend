
import pytest

from apps.accounting.models import Account
from apps.accounting.setup import set_up_company
from apps.catalog.models import Product, ProductCategory, UnitOfMeasure
from apps.installation.models import Technician
from apps.partners.models import Customer

pytestmark = pytest.mark.django_db

TECH = "/api/v1/installation/technicians/"
WO = "/api/v1/installation/work-orders/"
INV = "/api/v1/sales/invoices/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


@pytest.fixture
def customer(kuwait):
    return Customer.objects.create(company=kuwait, code="KW-CUS-00001", name="Ahmad Al-Sabah")


@pytest.fixture
def install_service(kuwait, books):
    family = ProductCategory.objects.create(code="svc", name="Services")
    return Product.objects.create(
        sku="INSTALL",
        name="Installation",
        category=family,
        uom=UnitOfMeasure.objects.get(code="HR"),
        product_type="service",
        income_account=Account.objects.get(company=kuwait, code="4300"),
    )


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


@pytest.fixture
def techs(admin_api, kuwait, showroom1):
    made = []
    for name, cost in (("Rajesh", "3"), ("Omar", "4")):
        response = admin_api.post(
            TECH,
            {
                "company": kuwait.id,
                "name": name,
                "branch": showroom1.id,
                "hourly_cost": cost,
                "skills": "electrical",
            },
            format="json",
        )
        assert response.status_code == 201, response.json()
        made.append(response.json())
    return made


@pytest.fixture
def job(admin_api, kuwait, showroom1, customer, techs):
    response = admin_api.post(
        WO,
        {
            "company": kuwait.id,
            "title": "Install 24 downlights",
            "customer": customer.id,
            "branch": showroom1.id,
            "site_address": "Salwa block 4",
            "scheduled_date": "2026-03-12",
            "scheduled_time": "09:00",
            "duration_hours": "3",
            "technicians": [techs[0]["id"], techs[1]["id"]],
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    return response.json()


def test_work_order_lifecycle_and_billing(admin_api, kuwait, job, install_service, techs):
    assert job["number"] == "KW-WO-2026-00001" and job["status"] == "scheduled"
    assert sorted(job["technician_names"]) == ["Omar", "Rajesh"]
    assert admin_api.get(f"{TECH}{techs[0]['id']}/").json()["open_jobs"] == 1

    cal = admin_api.get(
        f"{WO}calendar/", {"company": kuwait.id, "from": "2026-03-09", "to": "2026-03-15"}
    ).json()
    assert cal[0]["date"] == "2026-03-12" and cal[0]["jobs"][0]["time"] == "09:00"

    assert (
        admin_api.post(
            f"{WO}{job['id']}/bill/",
            {"product": install_service.id, "unit_price": "50"},
            format="json",
        ).status_code
        == 400
    )
    started = admin_api.post(f"{WO}{job['id']}/start/").json()
    assert started["status"] == "in_progress" and started["started_at"]
    done = admin_api.post(
        f"{WO}{job['id']}/complete/",
        {"labour_hours": "3.5", "signed_by": "Ahmad", "completion_notes": "All good"},
        format="json",
    )
    assert done.status_code == 200, done.json()
    assert done.json()["labour_cost"] == "24.500"  # 3.5 h × (3 + 4)
    assert admin_api.patch(f"{WO}{job['id']}/", {"title": "x"}, format="json").status_code == 400

    bill = admin_api.post(
        f"{WO}{job['id']}/bill/",
        {"product": install_service.id, "quantity": "3.5", "unit_price": "10"},
        format="json",
    )
    assert bill.status_code == 201, bill.json()
    invoice = bill.json()
    assert invoice["work_order"] == job["id"] and invoice["total"] == "35.000"
    posted = admin_api.post(f"{INV}{invoice['id']}/post/").json()
    from apps.accounting.models import JournalEntry

    entry = JournalEntry.objects.get(pk=posted["journal_entry"])
    assert {(ln.account.code, str(ln.credit)) for ln in entry.lines.filter(credit__gt=0)} == {
        ("4300", "35.000")
    }
    assert admin_api.get(f"{WO}{job['id']}/").json()["invoice_numbers"] == [posted["number"]]


def test_cancel_and_rules(admin_api, kuwait, showroom1, customer, job, techs, uae):
    assert (
        admin_api.post(f"{WO}{job['id']}/cancel/", {"reason": ""}, format="json").status_code == 400
    )
    cancelled = admin_api.post(
        f"{WO}{job['id']}/cancel/", {"reason": "Customer postponed"}, format="json"
    ).json()
    assert cancelled["status"] == "cancelled" and cancelled["cancel_reason"] == "Customer postponed"
    assert admin_api.post(f"{WO}{job['id']}/start/").status_code == 400

    Technician.objects.filter(pk=techs[0]["id"]).update(is_active=False)
    bad = admin_api.post(
        WO,
        {
            "company": kuwait.id,
            "title": "x",
            "customer": customer.id,
            "branch": showroom1.id,
            "scheduled_date": "2026-03-13",
            "technicians": [techs[0]["id"]],
        },
        format="json",
    )
    assert bad.status_code == 400 and "technicians" in bad.json()["fields"]
    no_tech = admin_api.post(
        WO,
        {
            "company": kuwait.id,
            "title": "y",
            "customer": customer.id,
            "branch": showroom1.id,
            "scheduled_date": "2026-03-13",
        },
        format="json",
    )
    assert no_tech.status_code == 201, no_tech.json()
    no_tech = no_tech.json()
    assert (
        admin_api.post(
            f"{WO}{no_tech['id']}/complete/", {"labour_hours": "1"}, format="json"
        ).status_code
        == 400
    )


def test_branch_scope(api, make_role, make_user, kuwait, showroom1, showroom2, customer, job):
    role = make_role("installer", "installation.work_order.view", "installation.technician.view")
    api.force_authenticate(make_user((role, kuwait, showroom2)))
    assert api.get(WO).json()["results"] == []
    assert (
        api.get(
            f"{WO}calendar/", {"company": kuwait.id, "from": "2026-03-09", "to": "2026-03-15"}
        ).json()
        == []
    )
    api.force_authenticate(make_user((role, kuwait, showroom1)))
    assert len(api.get(WO).json()["results"]) == 1
    assert len(api.get(TECH).json()["results"]) == 2
