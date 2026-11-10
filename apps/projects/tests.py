from datetime import date
from decimal import Decimal

import pytest

from apps.accounting.models import JournalEntry
from apps.accounting.setup import set_up_company
from apps.catalog.models import Product, ProductCategory, UnitOfMeasure
from apps.inventory.models import Reservation, StockBalance, StockMove, StockStatus
from apps.inventory.services import move
from apps.organization.models import Branch
from apps.partners.models import Customer
from apps.projects.models import Project
from apps.sales.models import Invoice

pytestmark = pytest.mark.django_db

PRJ = "/api/v1/projects/projects/"
MI = "/api/v1/projects/issues/"
INV = "/api/v1/sales/invoices/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


@pytest.fixture
def projects_dept(kuwait):
    return Branch.objects.create(
        company=kuwait, code="PRJ", name="Projects", branch_type=Branch.BranchType.PROJECTS
    )


@pytest.fixture
def customer(kuwait):
    return Customer.objects.create(
        company=kuwait, code="KW-CUS-00001", name="Salwa Villas Co.", customer_type="business"
    )


@pytest.fixture
def downlight(db):
    family = ProductCategory.objects.create(code="dl", name="Downlights")
    return Product.objects.create(
        sku="MS-DL-10W",
        name="Downlight 10W",
        category=family,
        uom=UnitOfMeasure.objects.get(code="PCS"),
    )


@pytest.fixture
def install_service(downlight):
    return Product.objects.create(
        sku="INSTALL",
        name="Installation",
        category=downlight.category,
        uom=UnitOfMeasure.objects.get(code="HR"),
        product_type="service",
    )


@pytest.fixture
def stocked(kuwait, books, warehouses, downlight):
    move(
        kuwait,
        warehouses["kw_main"],
        downlight,
        StockStatus.AVAILABLE,
        Decimal(100),
        on_date=date(2026, 3, 1),
        move_type=StockMove.MoveType.ADJUSTMENT,
        affects_cost=True,
        unit_cost=Decimal("2"),
    )
    return warehouses["kw_main"]


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


@pytest.fixture
def project(admin_api, kuwait, books, projects_dept, customer, downlight, install_service):
    response = admin_api.post(
        PRJ,
        {
            "company": kuwait.id,
            "name": "Salwa villa lighting",
            "customer": customer.id,
            "branch": projects_dept.id,
            "site_address": "Block 4, Salwa",
            "contract_value": "1500",
            "boq": [
                {"product": downlight.id, "quantity": "40", "unit_price": "25", "unit_cost": "2"},
                {
                    "product": install_service.id,
                    "quantity": "10",
                    "unit_price": "15",
                    "unit_cost": "5",
                },
            ],
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    return response.json()


def journal_lines(entry_id):
    entry = JournalEntry.objects.get(pk=entry_id)
    return {(ln.account.code, str(ln.debit), str(ln.credit)) for ln in entry.lines.all()}


def qty(warehouse, product):
    row = StockBalance.objects.filter(
        warehouse=warehouse, product=product, status="available"
    ).first()
    return row.quantity if row else Decimal(0)


def test_project_gets_number_and_boq(project):
    assert project["number"] == "KW-PRJ-2026-00001" and project["status"] == "lead"
    assert project["boq_value"] == "1150.0000000" or Decimal(project["boq_value"]) == Decimal(
        "1150"
    )
    assert len(project["boq"]) == 2 and project["summary"]["boq_cost"] == "130.000"


def test_boq_edit_keeps_line_ids(admin_api, project, downlight):
    first = project["boq"][0]
    updated = admin_api.patch(
        f"{PRJ}{project['id']}/",
        {
            "boq": [
                {
                    "id": first["id"],
                    "product": downlight.id,
                    "quantity": "50",
                    "unit_price": "24",
                    "unit_cost": "2",
                }
            ]
        },
        format="json",
    )
    assert updated.status_code == 200, updated.json()
    body = updated.json()
    assert [line["id"] for line in body["boq"]] == [first["id"]] and body["boq"][0][
        "quantity"
    ] == "50.0000"


def test_quote_then_win_then_invoice(admin_api, kuwait, project, customer):
    quote = admin_api.post(f"{PRJ}{project['id']}/quote/")
    assert quote.status_code == 201, quote.json()
    assert quote.json()["project"] == project["id"] and quote.json()["total"] == "1150.000"
    assert admin_api.get(f"{PRJ}{project['id']}/").json()["status"] == "quoted"

    too_early = admin_api.post(
        f"{PRJ}{project['id']}/invoice/",
        {"lines": [{"boq_line": project["boq"][0]["id"]}]},
        format="json",
    )
    assert too_early.status_code == 400

    won = admin_api.post(f"{PRJ}{project['id']}/status/", {"status": "won"}, format="json")
    assert won.status_code == 200 and won.json()["status"] == "won"
    bad = admin_api.post(f"{PRJ}{project['id']}/status/", {"status": "closed"}, format="json")
    assert bad.status_code == 400

    invoice = admin_api.post(
        f"{PRJ}{project['id']}/invoice/",
        {"lines": [{"boq_line": project["boq"][0]["id"], "quantity": "20"}]},
        format="json",
    )
    assert invoice.status_code == 201, invoice.json()
    body = invoice.json()
    assert (
        body["status"] == "draft"
        and body["total"] == "500.000"
        and body["project"] == project["id"]
    )
    assert body["lines"][0]["boq_line"] == project["boq"][0]["id"]
    posted = admin_api.post(f"{INV}{body['id']}/post/")
    assert posted.status_code == 200, posted.json()
    # Project invoices book their goods to Project sales, not showroom sales.
    from apps.accounting.models import JournalLine
    from apps.sales.models import Invoice

    entry = Invoice.objects.get(pk=body["id"]).journal_entry_id
    credits = {
        line.account.code: line.credit
        for line in JournalLine.objects.filter(entry_id=entry, credit__gt=0)
    }
    assert set(credits) == {"4200"}, credits

    progress = admin_api.get(f"{PRJ}{project['id']}/progress/").json()
    assert (
        progress[0]["invoiced_quantity"] == "20.0000"
        and progress[1]["invoiced_quantity"] == "0.0000"
    )
    summary = admin_api.get(f"{PRJ}{project['id']}/").json()["summary"]
    assert (
        summary["invoiced"] == "500.000"
        and summary["outstanding"] == "500.000"
        and summary["invoice_count"] == 1
    )


def test_reserve_and_issue_materials(admin_api, kuwait, project, stocked, downlight, warehouses):
    admin_api.post(f"{PRJ}{project['id']}/status/", {"status": "won"}, format="json")
    reserved = admin_api.post(
        f"{PRJ}{project['id']}/reserve/", {"warehouse": stocked.id}, format="json"
    )
    assert reserved.status_code == 200, reserved.json()
    assert reserved.json()["reserved"] == 1
    assert Reservation.objects.filter(status="active").get().quantity == Decimal(40)
    # Reserving again adds nothing.
    assert (
        admin_api.post(
            f"{PRJ}{project['id']}/reserve/", {"warehouse": stocked.id}, format="json"
        ).json()["reserved"]
        == 0
    )

    issue = admin_api.post(
        MI,
        {
            "company": kuwait.id,
            "date": "2026-03-10",
            "project": project["id"],
            "warehouse": stocked.id,
            "issued_to": "Site foreman",
            "lines": [
                {"boq_line": project["boq"][0]["id"], "product": downlight.id, "quantity": "30"}
            ],
        },
        format="json",
    )
    assert issue.status_code == 201, issue.json()
    posted = admin_api.post(f"{MI}{issue.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["number"] == "KW-MI-2026-00001" and body["cost_total"] == "60.000"
    assert journal_lines(body["journal_entry"]) == {
        ("5300", "60.000", "0.000"),
        ("1140", "0.000", "60.000"),
    }
    assert qty(stocked, downlight) == Decimal(70)
    assert Reservation.objects.filter(status="active").get().quantity == Decimal(10)
    detail = admin_api.get(f"{PRJ}{project['id']}/").json()
    assert detail["status"] == "in_progress" and detail["boq"][0]["issued_quantity"] == "30.0000"
    assert detail["summary"]["materials_cost"] == "60.000"

    # More than free (70 on hand, 10 held for us, 0 for others) is fine up to 70; 80 is not.
    too_many = admin_api.post(
        MI,
        {
            "company": kuwait.id,
            "date": "2026-03-11",
            "project": project["id"],
            "warehouse": stocked.id,
            "lines": [{"product": downlight.id, "quantity": "80"}],
        },
        format="json",
    ).json()
    assert admin_api.post(f"{MI}{too_many['id']}/post/").status_code == 400


def test_close_releases_reservations_and_blocks_issues(
    admin_api, kuwait, project, stocked, downlight
):
    admin_api.post(f"{PRJ}{project['id']}/status/", {"status": "won"}, format="json")
    admin_api.post(f"{PRJ}{project['id']}/reserve/", {"warehouse": stocked.id}, format="json")
    for status in ("in_progress", "completed", "closed"):
        assert (
            admin_api.post(
                f"{PRJ}{project['id']}/status/", {"status": status}, format="json"
            ).status_code
            == 200
        )
    assert Reservation.objects.filter(status="released").count() == 1
    issue = admin_api.post(
        MI,
        {
            "company": kuwait.id,
            "date": "2026-03-12",
            "project": project["id"],
            "warehouse": stocked.id,
            "lines": [{"product": downlight.id, "quantity": "1"}],
        },
        format="json",
    ).json()
    assert admin_api.post(f"{MI}{issue['id']}/post/").status_code == 400
    assert admin_api.delete(f"{PRJ}{project['id']}/").status_code == 400


def test_branch_scope(
    api, make_role, make_user, kuwait, books, projects_dept, showroom1, customer, project
):
    Project.objects.create(
        company=kuwait, name="Showroom job", customer=customer, branch=showroom1, number="X"
    )
    viewer = make_role("pm", "projects.project.view")
    api.force_authenticate(make_user((viewer, kuwait, projects_dept)))
    rows = api.get(PRJ).json()["results"]
    assert [row["name"] for row in rows] == ["Salwa villa lighting"]
    assert "summary" not in rows[0]
    assert Invoice.objects.count() == 0
