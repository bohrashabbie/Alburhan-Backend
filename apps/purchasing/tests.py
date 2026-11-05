from datetime import date
from decimal import Decimal

import pytest

from apps.accounting.models import JournalEntry
from apps.accounting.setup import set_up_company
from apps.catalog.models import Product, ProductCategory, UnitOfMeasure
from apps.inventory.models import ProductCost, StockBalance
from apps.organization.models import ExchangeRate
from apps.partners.models import Supplier
from apps.purchasing.models import PurchaseOrder, SupplierInvoice

pytestmark = pytest.mark.django_db

DAY = "2026-03-10"
PO = "/api/v1/purchasing/orders/"
GRN = "/api/v1/purchasing/receipts/"
BILL = "/api/v1/purchasing/bills/"
PAY = "/api/v1/purchasing/payments/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


@pytest.fixture
def china_supplier(kuwait):
    ExchangeRate.objects.create(
        currency_id="USD",
        to_currency_id="KWD",
        rate=Decimal("0.300"),
        effective_date=date(2026, 1, 1),
    )
    return Supplier.objects.create(
        company=kuwait,
        code="KW-SUP-00001",
        name="MS Lighting",
        currency_id="USD",
        payment_terms_days=30,
    )


@pytest.fixture
def local_supplier(kuwait):
    return Supplier.objects.create(
        company=kuwait, code="KW-SUP-00002", name="Gulf Freight", currency_id="KWD"
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
def freight_service(downlight):
    return Product.objects.create(
        sku="FREIGHT",
        name="Sea freight",
        category=downlight.category,
        uom=UnitOfMeasure.objects.get(code="PCS"),
        product_type="service",
    )


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


def order_payload(kuwait, supplier, warehouse, lines, **extra):
    return {
        "company": kuwait.id,
        "date": DAY,
        "supplier": supplier.id,
        "warehouse": warehouse.id,
        "currency": supplier.currency_id,
        "lines": lines,
        **extra,
    }


def line(product, qty="1", cost="10.000", discount="0", tax="0"):
    return {
        "product": product.id,
        "quantity": qty,
        "unit_cost": cost,
        "discount_percent": discount,
        "tax_rate": tax,
    }


def journal_lines(entry_id):
    entry = JournalEntry.objects.get(pk=entry_id)
    return {(ln.account.code, str(ln.debit), str(ln.credit)) for ln in entry.lines.all()}


def qty(warehouse, product, status="available"):
    row = StockBalance.objects.filter(warehouse=warehouse, product=product, status=status).first()
    return row.quantity if row else Decimal(0)


@pytest.fixture
def sent_order(admin_api, kuwait, books, warehouses, china_supplier, downlight):
    """100 downlights at USD 10 each (KWD 3.000) from China, sent."""
    draft = admin_api.post(
        PO,
        order_payload(
            kuwait, china_supplier, warehouses["kw_main"], [line(downlight, "100", "10")]
        ),
        format="json",
    )
    assert draft.status_code == 201, draft.json()
    sent = admin_api.post(f"{PO}{draft.json()['id']}/send/")
    assert sent.status_code == 200, sent.json()
    return sent.json()


# --- Orders -------------------------------------------------------------------------------------


def test_order_in_supplier_currency_gets_rate_and_number(sent_order):
    assert sent_order["number"] == "KW-PO-2026-00001" and sent_order["status"] == "sent"
    assert sent_order["currency"] == "USD" and Decimal(sent_order["exchange_rate"]) == Decimal(
        "0.3"
    )
    assert sent_order["total"] == "1000.000" and sent_order["receipt_status"] == "pending"


def test_order_rules(admin_api, kuwait, books, warehouses, china_supplier, downlight, uae):
    bad = admin_api.post(
        PO,
        order_payload(kuwait, china_supplier, warehouses["ae_main"], [line(downlight)]),
        format="json",
    )
    assert bad.status_code == 400 and "warehouse" in bad.json()["fields"]
    bad = admin_api.post(
        PO,
        order_payload(kuwait, china_supplier, warehouses["kw_main"], [line(downlight, "0")]),
        format="json",
    )
    assert bad.status_code == 400 and "lines" in bad.json()["fields"]
    empty = admin_api.post(
        PO, order_payload(kuwait, china_supplier, warehouses["kw_main"], []), format="json"
    ).json()
    assert admin_api.post(f"{PO}{empty['id']}/send/").status_code == 400


# --- Goods receipt --------------------------------------------------------------------------------


def test_receive_with_landed_cost_books_inventory_and_grni(
    admin_api, kuwait, warehouses, sent_order, downlight
):
    rows = admin_api.get(f"{PO}{sent_order['id']}/receivable/").json()
    assert rows[0]["quantity"] == "100.0000"
    receipt = admin_api.post(
        GRN,
        {
            "company": kuwait.id,
            "date": DAY,
            "order": sent_order["id"],
            "warehouse": warehouses["kw_main"].id,
            "supplier_reference": "PL-778",
            "freight_cost": "50",
            "customs_cost": "10",
            "lines": [{"order_line": rows[0]["order_line"], "quantity": "60"}],
        },
        format="json",
    )
    assert receipt.status_code == 201, receipt.json()
    posted = admin_api.post(f"{GRN}{receipt.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["number"] == "KW-GRN-2026-00001" and body["status"] == "posted"
    assert body["goods_value"] == "180.000"  # 60 × USD 10 × 0.3
    assert body["lines"][0]["unit_cost"] == "4.000"  # (180 + 60 landed) / 60
    assert qty(warehouses["kw_main"], downlight) == Decimal(60)
    assert ProductCost.objects.get(company=kuwait, product=downlight).average_cost == Decimal("4")
    assert journal_lines(body["journal_entry"]) == {
        ("1140", "240.000", "0.000"),  # inventory at landed cost
        ("2170", "0.000", "180.000"),  # goods received not invoiced
        ("5200", "0.000", "60.000"),  # landed-cost clearing
    }
    order = admin_api.get(f"{PO}{sent_order['id']}/").json()
    assert order["status"] == "partial" and order["lines"][0]["received_quantity"] == "60.0000"

    too_many = admin_api.post(
        GRN,
        {
            "company": kuwait.id,
            "date": DAY,
            "order": sent_order["id"],
            "warehouse": warehouses["kw_main"].id,
            "lines": [{"order_line": rows[0]["order_line"], "quantity": "50"}],
        },
        format="json",
    ).json()
    response = admin_api.post(f"{GRN}{too_many['id']}/post/")
    assert response.status_code == 400 and "between 0 and 40" in response.json()["fields"]["lines"]


def test_full_receipt_marks_order_received_and_close_works(
    admin_api, kuwait, warehouses, sent_order
):
    rows = admin_api.get(f"{PO}{sent_order['id']}/receivable/").json()
    receipt = admin_api.post(
        GRN,
        {
            "company": kuwait.id,
            "date": DAY,
            "order": sent_order["id"],
            "warehouse": warehouses["kw_main"].id,
            "lines": [{"order_line": rows[0]["order_line"], "quantity": "100"}],
        },
        format="json",
    ).json()
    admin_api.post(f"{GRN}{receipt['id']}/post/")
    assert admin_api.get(f"{PO}{sent_order['id']}/").json()["status"] == "received"
    assert admin_api.post(f"{PO}{sent_order['id']}/cancel/").status_code == 400
    assert admin_api.post(f"{PO}{sent_order['id']}/close/").json()["status"] == "closed"


# --- Supplier bill ---------------------------------------------------------------------------------


@pytest.fixture
def received(admin_api, kuwait, warehouses, sent_order):
    rows = admin_api.get(f"{PO}{sent_order['id']}/receivable/").json()
    receipt = admin_api.post(
        GRN,
        {
            "company": kuwait.id,
            "date": DAY,
            "order": sent_order["id"],
            "warehouse": warehouses["kw_main"].id,
            "lines": [{"order_line": rows[0]["order_line"], "quantity": "100"}],
        },
        format="json",
    ).json()
    return admin_api.post(f"{GRN}{receipt['id']}/post/").json()


def test_bill_clears_grni_and_books_payable(
    admin_api, kuwait, china_supplier, sent_order, received, downlight
):
    billable = admin_api.get(f"{PO}{sent_order['id']}/billable/").json()
    assert billable[0]["quantity"] == "100.0000" and billable[0]["unit_cost"] == "10.000"
    # The supplier bills USD 10.50 instead of 10: the difference is a price variance.
    draft = admin_api.post(
        BILL,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": china_supplier.id,
            "currency": "USD",
            "supplier_number": "MSL-2026-55",
            "order": sent_order["id"],
            "lines": [
                {
                    "product": downlight.id,
                    "quantity": "100",
                    "unit_cost": "10.50",
                    "receipt_line": billable[0]["receipt_line"],
                }
            ],
        },
        format="json",
    )
    assert draft.status_code == 201, draft.json()
    posted = admin_api.post(f"{BILL}{draft.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["number"] == "KW-BILL-2026-00001" and body["status"] == "posted"
    assert body["due_date"] == "2026-04-09" and body["amount_due"] == "1050.000"
    assert journal_lines(body["journal_entry"]) == {
        ("2170", "300.000", "0.000"),  # GRNI cleared at order value
        ("5200", "15.000", "0.000"),  # price variance 50 USD × 0.3
        ("2110", "0.000", "315.000"),  # payable 1050 USD × 0.3
    }
    again = admin_api.get(f"{PO}{sent_order['id']}/billable/").json()
    assert again == []
    order = admin_api.get(f"{PO}{sent_order['id']}/").json()
    assert order["lines"][0]["invoiced_quantity"] == "100.0000"


def test_bill_rules(
    admin_api, kuwait, books, china_supplier, local_supplier, downlight, freight_service
):
    stock_without_receipt = admin_api.post(
        BILL,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": china_supplier.id,
            "currency": "USD",
            "lines": [{"product": downlight.id, "quantity": "1", "unit_cost": "10"}],
        },
        format="json",
    ).json()
    response = admin_api.post(f"{BILL}{stock_without_receipt['id']}/post/")
    assert response.status_code == 400 and "receive it first" in response.json()["fields"]["lines"]

    service = admin_api.post(
        BILL,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": local_supplier.id,
            "currency": "KWD",
            "lines": [
                {"product": freight_service.id, "quantity": "1", "unit_cost": "75", "tax_rate": "0"}
            ],
        },
        format="json",
    ).json()
    posted = admin_api.post(f"{BILL}{service['id']}/post/").json()
    assert journal_lines(posted["journal_entry"]) == {
        ("5200", "75.000", "0.000"),  # freight & customs expense (default for services)
        ("2110", "0.000", "75.000"),
    }
    assert (
        admin_api.patch(f"{BILL}{service['id']}/", {"reference": "x"}, format="json").status_code
        == 400
    )


def test_cancel_unpaid_bill_reverses(admin_api, kuwait, books, local_supplier, freight_service):
    bill = admin_api.post(
        BILL,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": local_supplier.id,
            "currency": "KWD",
            "lines": [{"product": freight_service.id, "quantity": "1", "unit_cost": "20"}],
        },
        format="json",
    ).json()
    admin_api.post(f"{BILL}{bill['id']}/post/")
    cancelled = admin_api.post(f"{BILL}{bill['id']}/cancel/", {"date": DAY}, format="json")
    assert cancelled.status_code == 200, cancelled.json()
    assert cancelled.json()["status"] == "cancelled"
    assert JournalEntry.objects.filter(reversal_of__isnull=False).count() == 1


# --- Payments ---------------------------------------------------------------------------------------


def test_payment_allocates_and_holds_advance(
    admin_api, kuwait, books, local_supplier, freight_service
):
    bill = admin_api.post(
        BILL,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": local_supplier.id,
            "currency": "KWD",
            "lines": [{"product": freight_service.id, "quantity": "1", "unit_cost": "100"}],
        },
        format="json",
    ).json()
    admin_api.post(f"{BILL}{bill['id']}/post/")
    from apps.accounting.models import Journal

    bank = Journal.objects.get(company=kuwait, code="BK")
    payment = admin_api.post(
        PAY,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": local_supplier.id,
            "method": "bank_transfer",
            "journal": bank.id,
            "currency": "KWD",
            "amount": "140",
            "reference": "TT-1",
            "allocations": [{"invoice": bill["id"], "amount": "100"}],
        },
        format="json",
    )
    assert payment.status_code == 201, payment.json()
    posted = admin_api.post(f"{PAY}{payment.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["number"] == "KW-SP-2026-00001" and body["unallocated_amount"] == "40.000"
    assert journal_lines(body["journal_entry"]) == {
        ("2110", "100.000", "0.000"),
        ("1150", "40.000", "0.000"),  # advance to supplier
        ("1120", "0.000", "140.000"),
    }
    assert SupplierInvoice.objects.get(pk=bill["id"]).status == "paid"
    balance = admin_api.get(f"/api/v1/purchasing/suppliers/{local_supplier.id}/balance/").json()
    assert balance == {
        "open_bills": 0,
        "amount_due": "0.000",
        "advances": "40.000",
        "currency": "KWD",
    }

    over = admin_api.post(
        PAY,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": local_supplier.id,
            "journal": bank.id,
            "currency": "KWD",
            "amount": "10",
            "allocations": [{"invoice": bill["id"], "amount": "10"}],
        },
        format="json",
    ).json()
    assert admin_api.post(f"{PAY}{over['id']}/post/").status_code == 400


# --- Access ---------------------------------------------------------------------------------------


def test_branch_buyer_sees_own_branch_orders(
    api,
    make_role,
    make_user,
    kuwait,
    books,
    showroom1,
    showroom2,
    warehouses,
    local_supplier,
    downlight,
):
    PurchaseOrder.objects.create(
        company=kuwait,
        date=DAY,
        supplier=local_supplier,
        warehouse=warehouses["kw_sr1"],
        branch=showroom1,
        currency_id="KWD",
    )
    PurchaseOrder.objects.create(
        company=kuwait,
        date=DAY,
        supplier=local_supplier,
        warehouse=warehouses["kw_sr2"],
        branch=showroom2,
        currency_id="KWD",
    )
    buyer = make_role("buyer", "purchasing.order.view", "purchasing.order.create")
    api.force_authenticate(make_user((buyer, kuwait, showroom1)))
    rows = api.get(PO).json()["results"]
    assert [row["branch"] for row in rows] == [showroom1.id]
    denied = api.post(
        PO,
        order_payload(
            kuwait, local_supplier, warehouses["kw_sr2"], [line(downlight)], branch=showroom2.id
        ),
        format="json",
    )
    assert denied.status_code == 403


def test_bill_in_foreign_currency_with_uneven_amounts(
    admin_api, kuwait, china_supplier, sent_order, received, downlight
):
    """KWD has 3 decimals and USD 2: converted amounts like 0.3 × 10.37 must still post."""
    billable = admin_api.get(f"{PO}{sent_order['id']}/billable/").json()
    draft = admin_api.post(
        BILL,
        {
            "company": kuwait.id,
            "date": DAY,
            "supplier": china_supplier.id,
            "currency": "USD",
            "lines": [
                {
                    "product": downlight.id,
                    "quantity": "37",
                    "unit_cost": "10.37",
                    "receipt_line": billable[0]["receipt_line"],
                }
            ],
        },
        format="json",
    ).json()
    posted = admin_api.post(f"{BILL}{draft['id']}/post/")
    assert posted.status_code == 200, posted.json()
    lines = journal_lines(posted.json()["journal_entry"])
    debit = sum(Decimal(dr) for _, dr, _ in lines)
    credit = sum(Decimal(cr) for _, _, cr in lines)
    assert debit == credit == Decimal("115.107")  # 37 × 10.37 USD × 0.3
