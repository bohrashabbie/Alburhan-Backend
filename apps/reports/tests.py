from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounting.setup import set_up_company
from apps.catalog.models import Product, ProductCategory, UnitOfMeasure
from apps.inventory.models import StockMove, StockStatus
from apps.inventory.services import move
from apps.partners.models import Customer, Supplier
from apps.purchasing.models import SupplierInvoice, SupplierInvoiceLine
from apps.purchasing.services import post_supplier_invoice
from apps.purchasing.services import recompute as recompute_purchase
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.services import post_invoice, recompute

pytestmark = pytest.mark.django_db

REPORTS = "/api/v1/reports/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, timezone.localdate().year)
    set_up_company(kuwait, 2026)


@pytest.fixture
def customer(kuwait):
    return Customer.objects.create(
        company=kuwait, code="KW-CUS-00001", name="Ahmad", payment_terms_days=0
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
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


@pytest.fixture
def booked(kuwait, books, showroom1, warehouses, customer, downlight, make_user):
    """One posted sale today (100 net + 5 VAT), stock bought at 2.000, one supplier bill of 30 + 1.5 VAT."""
    today = timezone.localdate()
    move(
        kuwait,
        warehouses["kw_main"],
        downlight,
        StockStatus.AVAILABLE,
        Decimal(10),
        on_date=today,
        move_type=StockMove.MoveType.ADJUSTMENT,
        affects_cost=True,
        unit_cost=Decimal("2"),
    )
    invoice = Invoice.objects.create(
        company=kuwait,
        date=today,
        due_date=today,
        branch=showroom1,
        customer=customer,
        currency_id="KWD",
    )
    line = InvoiceLine(
        invoice=invoice,
        product=downlight,
        quantity=Decimal(10),
        unit_price=Decimal("10"),
        tax_rate=Decimal("5"),
    )
    recompute(invoice, [line])
    line.save()
    invoice.save()
    post_invoice(invoice)
    supplier = Supplier.objects.create(
        company=kuwait, code="KW-SUP-00001", name="Freight Co", currency_id="KWD"
    )
    service = Product.objects.create(
        sku="FREIGHT",
        name="Freight",
        category=downlight.category,
        uom=downlight.uom,
        product_type="service",
    )
    bill = SupplierInvoice.objects.create(
        company=kuwait, date=today, supplier=supplier, currency_id="KWD"
    )
    bill_line = SupplierInvoiceLine(
        invoice=bill,
        product=service,
        quantity=Decimal(1),
        unit_cost=Decimal("30"),
        tax_rate=Decimal("5"),
    )
    recompute_purchase(bill, [bill_line])
    bill_line.save()
    bill.save()
    post_supplier_invoice(bill)
    invoice.refresh_from_db()
    return invoice


def test_dashboard_figures(admin_api, kuwait, booked):
    body = admin_api.get(f"{REPORTS}dashboard/", {"company": kuwait.id}).json()
    assert body["sales"]["today"] == "100.000" and body["sales"]["month_count"] == 1
    assert body["sales"]["series"][-1]["amount"] == "100.000" and len(body["sales"]["series"]) == 30
    assert body["sales"]["by_branch"][0]["name"] == "Showroom 1"
    assert body["sales"]["top_products"][0]["sku"] == "MS-DL-10W"
    assert body["receivables"] == {
        "total": "105.000",
        "count": 1,
        "overdue": "0.000",
        "overdue_count": 0,
    }
    assert body["payables"]["total"] == "31.500"
    assert body["stock_value"] == "20.000"
    # The home page of the previous system: totals by period, by month, latest and best sellers.
    for period in ("today", "week", "month", "year"):
        assert body["periods"][period]["sale"] == "100.000"
        assert body["periods"][period]["purchase"] == "30.000"
        assert body["periods"][period]["sale_return"] == "0.000"
    assert len(body["cash_flow"]) == 7 and len(body["yearly"]) == 12
    this_month = timezone.localdate().strftime("%Y-%m")
    assert {"month": this_month, "purchased": "30.000", "sold": "100.000"} in body["yearly"]
    assert body["month_mix"]["revenue"] == "100.000"
    assert body["recent"]["sales"][0]["party"] and body["recent"]["sales"][0]["number"]
    assert body["best_sellers"]["year_quantity"][0]["sku"] == "MS-DL-10W"
    assert body["best_sellers"]["year_amount"][0]["amount"] == "100.000"


def test_profit_and_loss_and_balance_sheet(admin_api, kuwait, booked):
    today = timezone.localdate().isoformat()
    pnl = admin_api.get(
        f"{REPORTS}profit-and-loss/", {"company": kuwait.id, "date_to": today}
    ).json()
    assert pnl["totals"]["income"] == "100.000"
    assert pnl["totals"]["cost_of_sales"] == "30.000"  # the freight bill went to 5200
    assert pnl["totals"]["gross_profit"] == "70.000" and pnl["totals"]["net_profit"] == "70.000"
    assert pnl["income"][0]["rows"][0]["code"] == "4100"

    bs = admin_api.get(f"{REPORTS}balance-sheet/", {"company": kuwait.id, "date_to": today}).json()
    assert bs["totals"]["balanced"] is True
    assert bs["current_earnings"] == "70.000"
    assets = {row["code"]: row["amount"] for group in bs["assets"] for row in group["rows"]}
    assert assets["1130"] == "105.000" and assets["1160"] == "1.500"
    liabilities = {
        row["code"]: row["amount"] for group in bs["liabilities"] for row in group["rows"]
    }
    assert liabilities["2110"] == "31.500" and liabilities["2130"] == "5.000"


def test_ageing(admin_api, kuwait, booked):
    body = admin_api.get(f"{REPORTS}ageing/", {"company": kuwait.id, "kind": "receivable"}).json()
    assert body["rows"][0]["name"] == "Ahmad" and body["rows"][0]["current"] == "105.000"
    later = (timezone.localdate().replace(year=timezone.localdate().year + 1)).isoformat()
    aged = admin_api.get(
        f"{REPORTS}ageing/", {"company": kuwait.id, "kind": "receivable", "date_to": later}
    ).json()
    assert aged["rows"][0]["over_90"] == "105.000" and aged["totals"]["total"] == "105.000"
    payables = admin_api.get(f"{REPORTS}ageing/", {"company": kuwait.id, "kind": "payable"}).json()
    assert payables["totals"]["total"] == "31.500"


def test_vat_return_xbrl_and_egypt_document(admin_api, kuwait, booked):
    today = timezone.localdate()
    params = {
        "company": kuwait.id,
        "date_from": today.replace(day=1).isoformat(),
        "date_to": today.isoformat(),
    }
    vat = admin_api.get(f"{REPORTS}compliance/vat-return/", params).json()
    assert (
        vat["sales"]["standard_rated_net"] == "100.000" and vat["totals"]["output_tax"] == "5.000"
    )
    assert vat["totals"]["input_tax"] == "1.500" and vat["totals"]["net_payable"] == "3.500"

    check = admin_api.get(f"{REPORTS}compliance/xbrl/check/", params).json()
    results = {c["name"]: c for c in check["checks"]}
    assert all(c["ok"] for name, c in results.items() if name != "Entity identifier"), results
    assert results["Entity identifier"]["level"] == "warning"  # no CR number in the test company
    assert {f["concept"] for f in check["facts"]} >= {"Revenue", "Assets", "ProfitLoss"}

    xbrl = admin_api.get(f"{REPORTS}compliance/xbrl/", params)
    assert xbrl.status_code == 200 and xbrl["Content-Type"] == "application/xml"
    text = xbrl.content.decode()
    assert (
        '<ifrs-full:Revenue contextRef="D" unitRef="KWD" decimals="3">100.000</ifrs-full:Revenue>'
        in text
    )
    assert "<ifrs-full:Assets" in text

    doc = admin_api.get(f"{REPORTS}compliance/egypt-einvoice/{booked.id}/").json()
    assert doc["documentType"] == "I" and doc["internalID"] == booked.number
    assert doc["invoiceLines"][0]["taxableItems"][0]["rate"] == 5.0 and doc["totalAmount"] == 105.0


def test_reports_need_permission(api, make_role, make_user, kuwait, booked):
    role = make_role("viewer", "accounting.report.view")
    api.force_authenticate(make_user((role, kuwait, None)))
    assert api.get(f"{REPORTS}profit-and-loss/", {"company": kuwait.id}).status_code == 200
    assert api.get(f"{REPORTS}dashboard/", {"company": kuwait.id}).status_code == 403
    assert api.get(f"{REPORTS}compliance/vat-return/", {"company": kuwait.id}).status_code == 403


def test_customer_statement_and_balances(admin_api, kuwait, booked, customer):
    today = timezone.localdate().isoformat()
    statement = admin_api.get(
        f"{REPORTS}customer-statement/", {"company": kuwait.id, "customer": customer.id}
    ).json()
    assert statement["opening"] == "0.000" and statement["closing"] == "105.000"
    assert statement["rows"] == [
        {
            "date": today,
            "kind": "invoice",
            "number": booked.number,
            "id": booked.id,
            "debit": "105.000",
            "credit": "0.000",
            "balance": "105.000",
        }
    ]
    balances = admin_api.get(f"{REPORTS}customer-balances/", {"company": kuwait.id}).json()
    assert balances["totals"]["balance"] == "105.000"
    assert balances["rows"][0]["name"] == customer.name
    assert admin_api.get(f"{REPORTS}customer-statement/", {"company": kuwait.id}).status_code == 404


def test_sales_and_stock_reports(admin_api, kuwait, booked, customer):
    today = timezone.localdate()
    best = admin_api.get(f"{REPORTS}best-sellers/", {"company": kuwait.id}).json()
    assert best["rows"][0]["sku"] == "MS-DL-10W" and best["rows"][0]["quantity"] == "10"

    month = admin_api.get(
        f"{REPORTS}trade-calendar/",
        {"company": kuwait.id, "kind": "sale", "year": today.year, "month": today.month},
    ).json()
    day = next(row for row in month["rows"] if row["date"] == today.isoformat())
    assert (day["count"], day["net"], day["tax"], day["total"]) == (
        1,
        "100.000",
        "5.000",
        "105.000",
    )
    assert month["totals"]["total"] == "105.000"
    year = admin_api.get(
        f"{REPORTS}trade-calendar/", {"company": kuwait.id, "kind": "purchase", "year": today.year}
    ).json()
    assert len(year["rows"]) == 12 and year["totals"]["net"] == "30.000"

    # Nothing is left in the warehouse value after the sale was not delivered: 10 pieces at 2.000.
    stock = admin_api.get(f"{REPORTS}warehouse-stock/", {"company": kuwait.id}).json()
    assert stock["rows"][0]["quantity"] == "10" and stock["total_value"] == "20.000"

    targets = admin_api.get(
        f"{REPORTS}sales-targets/", {"company": kuwait.id, "year": today.year, "month": today.month}
    ).json()
    assert targets["unassigned"] == "100.000" and targets["rows"] == []


def test_customer_group_discount_shows_on_the_customer(admin_api, kuwait, customer):
    group = admin_api.post(
        "/api/v1/partners/customer-groups/",
        {"company": kuwait.id, "name": "Contractors", "discount_percent": "7.5"},
        format="json",
    )
    assert group.status_code == 201, group.json()
    changed = admin_api.patch(
        f"/api/v1/partners/customers/{customer.id}/", {"group": group.json()["id"]}, format="json"
    )
    assert changed.status_code == 200, changed.json()
    assert changed.json()["group_discount_percent"] == "7.50"
    listed = admin_api.get("/api/v1/partners/customer-groups/", {"company": kuwait.id}).json()
    assert listed["results"][0]["customer_count"] == 1
    assert (
        admin_api.delete(f"/api/v1/partners/customer-groups/{group.json()['id']}/").status_code
        == 400
    )
