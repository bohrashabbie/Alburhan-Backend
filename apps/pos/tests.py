from datetime import date
from decimal import Decimal

import pytest

from apps.accounting.models import Account, Journal, JournalEntry
from apps.accounting.setup import set_up_company
from apps.catalog.models import PriceList, PriceListItem, Product, ProductCategory, UnitOfMeasure
from apps.inventory.models import StockBalance, StockMove, StockStatus
from apps.inventory.services import move
from apps.partners.models import Customer
from apps.pos.models import Register, Sale, SalePayment, Shift
from apps.sales.models import Invoice, SalesSettings

pytestmark = pytest.mark.django_db

REG = "/api/v1/pos/registers/"
SHIFTS = "/api/v1/pos/shifts/"
SALES = "/api/v1/pos/sales/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


@pytest.fixture
def walk_in(kuwait):
    return Customer.objects.create(company=kuwait, code="KW-CUS-00001", name="Walk-in customer")


@pytest.fixture
def downlight(db):
    family = ProductCategory.objects.create(code="dl", name="Downlights")
    return Product.objects.create(
        sku="MS-DL-10W",
        name="Downlight 10W",
        barcode="6291234567890",
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
def retail(kuwait, downlight, install_service):
    price_list = PriceList.objects.create(
        company=kuwait, code="retail", name="Retail", currency_id="KWD", is_default=True
    )
    PriceListItem.objects.create(
        price_list=price_list, product=downlight, unit_price=Decimal("4.500")
    )
    PriceListItem.objects.create(
        price_list=price_list, product=install_service, unit_price=Decimal("10")
    )
    return price_list


@pytest.fixture
def stocked(kuwait, books, warehouses, downlight):
    """20 downlights in the Showroom 1 warehouse at 2.000 each."""
    move(
        kuwait,
        warehouses["kw_sr1"],
        downlight,
        StockStatus.AVAILABLE,
        Decimal(20),
        on_date=date(2026, 3, 1),
        move_type=StockMove.MoveType.ADJUSTMENT,
        affects_cost=True,
        unit_cost=Decimal("2"),
    )
    return warehouses["kw_sr1"]


@pytest.fixture
def register(kuwait, books, showroom1, stocked, retail, walk_in):
    return Register.objects.create(
        company=kuwait,
        branch=showroom1,
        code="SR1-T1",
        name="Showroom 1 till",
        warehouse=stocked,
        price_list=retail,
        cash_journal=Journal.objects.get(company=kuwait, code="CS"),
        card_journal=Journal.objects.get(company=kuwait, code="BK"),
        walk_in_customer=walk_in,
        cash_difference_account=Account.objects.get(company=kuwait, code="6900"),
        receipt_footer="Thank you",
    )


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


@pytest.fixture
def shift(admin_api, register):
    response = admin_api.post(
        f"{SHIFTS}open/", {"register": register.id, "opening_cash": "50"}, format="json"
    )
    assert response.status_code == 201, response.json()
    return response.json()


def cart(shift_id, lines, payments, **extra):
    return {"shift": shift_id, "lines": lines, "payments": payments, **extra}


def journal_lines(entry_id):
    entry = JournalEntry.objects.get(pk=entry_id)
    return {(ln.account.code, str(ln.debit), str(ln.credit)) for ln in entry.lines.all()}


def qty(warehouse, product, status="available"):
    row = StockBalance.objects.filter(warehouse=warehouse, product=product, status=status).first()
    return row.quantity if row else Decimal(0)


# --- Registers and catalogue ---------------------------------------------------------------------


def test_register_needs_cash_or_bank_journals(
    admin_api, kuwait, books, showroom1, stocked, walk_in
):
    payload = {
        "company": kuwait.id,
        "branch": showroom1.id,
        "code": "T2",
        "name": "Till 2",
        "warehouse": stocked.id,
        "cash_journal": Journal.objects.get(company=kuwait, code="JV").id,
        "card_journal": Journal.objects.get(company=kuwait, code="BK").id,
        "walk_in_customer": walk_in.id,
    }
    response = admin_api.post(REG, payload, format="json")
    assert response.status_code == 400 and "cash_journal" in response.json()["fields"]
    payload["cash_journal"] = Journal.objects.get(company=kuwait, code="CS").id
    response = admin_api.post(REG, payload, format="json")
    assert response.status_code == 201, response.json()
    assert response.json()["open_shift"] is None and response.json()["is_active"] is True


def test_catalog_shows_price_stock_and_barcode_match(
    admin_api, register, downlight, install_service
):
    body = admin_api.get(f"{REG}{register.id}/catalog/").json()
    assert body["price_list"]["name"] == "Retail" and body["currency"] == "KWD"
    assert [c["name"] for c in body["categories"]] == ["Downlights"]
    by_sku = {p["sku"]: p for p in body["products"]}
    assert by_sku["MS-DL-10W"]["price"] == "4.500" and by_sku["MS-DL-10W"]["stock"] == "20"
    assert by_sku["INSTALL"]["stock"] is None  # services carry no stock

    scanned = admin_api.get(f"{REG}{register.id}/catalog/", {"search": "6291234567890"}).json()
    assert [p["sku"] for p in scanned["products"]] == ["MS-DL-10W"]


# --- Shifts --------------------------------------------------------------------------------------


def test_one_open_shift_per_register(admin_api, register, shift):
    assert shift["number"] == "KW-SHF-2026-00001" and shift["status"] == "open"
    assert shift["totals"]["expected_cash"] == "50.000"
    again = admin_api.post(
        f"{SHIFTS}open/", {"register": register.id, "opening_cash": "0"}, format="json"
    )
    assert again.status_code == 400 and "already" in again.json()["fields"]["register"]
    assert admin_api.get(f"{REG}{register.id}/").json()["open_shift"]["id"] == shift["id"]


# --- Checkout ------------------------------------------------------------------------------------


def test_cash_sale_with_change(admin_api, kuwait, register, shift, downlight, walk_in):
    response = admin_api.post(
        f"{SALES}checkout/",
        cart(
            shift["id"],
            [{"product": downlight.id, "quantity": "2"}],
            [{"method": "cash", "amount": "10"}],
        ),
        format="json",
    )
    assert response.status_code == 201, response.json()
    sale = response.json()
    assert sale["number"] == "KW-INV-2026-00001" and sale["status"] == "completed"
    assert sale["total"] == "9.000" and sale["tendered"] == "10.000"
    assert sale["change_given"] == "1.000"
    assert sale["customer_name"] == "Walk-in customer" and sale["receipt_footer"] == "Thank you"
    assert [(p["method"], p["amount"]) for p in sale["payments"]] == [("cash", "9.000")]
    assert sale["lines"][0]["unit_price"] == "4.500"

    invoice = Invoice.objects.get(pk=sale["invoice"])
    assert invoice.status == "paid" and invoice.amount_paid == Decimal("9.000")
    assert qty(register.warehouse, downlight) == Decimal(18)
    # Money went into the cash drawer, and the goods out of inventory at cost.
    receipt_entry = SalePayment.objects.get(sale_id=sale["id"]).receipt.journal_entry_id
    assert journal_lines(receipt_entry) == {
        ("1110", "9.000", "0.000"),
        ("1130", "0.000", "9.000"),
    }
    delivery_entry = invoice.deliveries.get().journal_entry_id
    assert journal_lines(delivery_entry) == {
        ("5100", "4.000", "0.000"),
        ("1140", "0.000", "4.000"),
    }
    totals = admin_api.get(f"{SHIFTS}{shift['id']}/").json()["totals"]
    assert totals["sales_count"] == "1" and totals["expected_cash"] == "59.000"


def test_split_knet_and_cash_with_service_line(
    admin_api, register, shift, downlight, install_service
):
    sale = admin_api.post(
        f"{SALES}checkout/",
        cart(
            shift["id"],
            [
                {"product": downlight.id, "quantity": "1"},
                {"product": install_service.id, "quantity": "2"},
            ],
            [
                {"method": "knet", "amount": "20", "reference": "KNET-7781"},
                {"method": "cash", "amount": "5"},
            ],
        ),
        format="json",
    ).json()
    assert sale["total"] == "24.500" and sale["change_given"] == "0.500"
    assert {(p["method"], p["amount"]) for p in sale["payments"]} == {
        ("knet", "20.000"),
        ("cash", "4.500"),
    }
    knet = SalePayment.objects.get(sale_id=sale["id"], method="knet")
    assert knet.receipt.journal.code == "BK" and knet.receipt.reference == "KNET-7781"
    assert qty(register.warehouse, downlight) == Decimal(19)


def test_checkout_rejections(admin_api, kuwait, register, shift, downlight, uae):
    url = f"{SALES}checkout/"
    short = admin_api.post(
        url,
        cart(shift["id"], [{"product": downlight.id}], [{"method": "cash", "amount": "1"}]),
        format="json",
    )
    assert short.status_code == 400 and "short" in short.json()["fields"]["payments"]

    card_over = admin_api.post(
        url,
        cart(shift["id"], [{"product": downlight.id}], [{"method": "card", "amount": "9"}]),
        format="json",
    )
    assert card_over.status_code == 400 and "card" in card_over.json()["fields"]["payments"]

    too_many = admin_api.post(
        url,
        cart(
            shift["id"],
            [{"product": downlight.id, "quantity": "25"}],
            [{"method": "cash", "amount": "200"}],
        ),
        format="json",
    )
    assert too_many.status_code == 400 and "free" in too_many.json()["fields"]["lines"]
    assert Sale.objects.count() == 0 and qty(register.warehouse, downlight) == Decimal(20)

    unpriced = Product.objects.create(
        sku="NOPRICE", name="No price", category=downlight.category, uom=downlight.uom
    )
    none = admin_api.post(
        url,
        cart(shift["id"], [{"product": unpriced.id}], [{"method": "cash", "amount": "9"}]),
        format="json",
    )
    assert none.status_code == 400 and "no price" in none.json()["fields"]["lines"]

    SalesSettings.objects.update_or_create(
        company=kuwait, defaults={"max_line_discount_percent": Decimal("10")}
    )
    other = Customer.objects.create(company=uae, code="AE-1", name="Dubai customer")
    wrong = admin_api.post(
        url,
        cart(
            shift["id"],
            [{"product": downlight.id}],
            [{"method": "cash", "amount": "9"}],
            customer=other.id,
        ),
        format="json",
    )
    assert wrong.status_code == 400 and "customer" in wrong.json()["fields"]


def test_discount_ceiling_applies_at_the_till(
    api, make_role, make_user, kuwait, showroom1, register, downlight
):
    SalesSettings.objects.update_or_create(
        company=kuwait, defaults={"max_line_discount_percent": Decimal("10")}
    )
    cashier = make_role(
        "cashier", "pos.sale.view", "pos.sale.create", "pos.shift.view", "pos.shift.open"
    )
    api.force_authenticate(make_user((cashier, kuwait, showroom1)))
    shift = api.post(
        f"{SHIFTS}open/", {"register": register.id, "opening_cash": "0"}, format="json"
    ).json()
    too_much = api.post(
        f"{SALES}checkout/",
        cart(
            shift["id"],
            [{"product": downlight.id, "discount_percent": "25"}],
            [{"method": "cash", "amount": "9"}],
        ),
        format="json",
    )
    assert too_much.status_code == 400 and "manager" in too_much.json()["fields"]["lines"]
    fine = api.post(
        f"{SALES}checkout/",
        cart(
            shift["id"],
            [{"product": downlight.id, "discount_percent": "10"}],
            [{"method": "cash", "amount": "9"}],
        ),
        format="json",
    )
    assert fine.status_code == 201 and fine.json()["total"] == "4.050"


# --- Void --------------------------------------------------------------------------------------


def test_void_returns_stock_and_refunds_money(admin_api, kuwait, register, shift, downlight):
    sale = admin_api.post(
        f"{SALES}checkout/",
        cart(
            shift["id"],
            [{"product": downlight.id, "quantity": "2"}],
            [{"method": "knet", "amount": "9"}],
        ),
        format="json",
    ).json()
    missing = admin_api.post(f"{SALES}{sale['id']}/void/", {"reason": ""}, format="json")
    assert missing.status_code == 400
    voided = admin_api.post(f"{SALES}{sale['id']}/void/", {"reason": "Wrong item"}, format="json")
    assert voided.status_code == 200, voided.json()
    body = voided.json()
    assert body["status"] == "voided" and body["credit_note_number"] == "KW-CN-2026-00001"
    assert {(p["method"], p["amount"]) for p in body["payments"]} == {
        ("knet", "9.000"),
        ("knet", "-9.000"),
    }
    assert qty(register.warehouse, downlight) == Decimal(18)
    assert qty(register.warehouse, downlight, "returned") == Decimal(2)
    invoice = Invoice.objects.get(pk=sale["invoice"])
    assert invoice.amount_credited == Decimal("9.000") and invoice.amount_paid == Decimal(0)
    refund = SalePayment.objects.get(sale_id=sale["id"], amount__lt=0)
    assert journal_lines(refund.journal_entry_id) == {
        ("1130", "9.000", "0.000"),
        ("1120", "0.000", "9.000"),
    }
    totals = admin_api.get(f"{SHIFTS}{shift['id']}/").json()["totals"]
    assert totals["net_total"] == "0.000" and totals["voided_count"] == "1"
    assert totals["by_method"]["knet"] == "0.000"
    again = admin_api.post(f"{SALES}{sale['id']}/void/", {"reason": "x"}, format="json")
    assert again.status_code == 400


# --- Close -------------------------------------------------------------------------------------


def test_close_shift_books_cash_short(admin_api, kuwait, register, shift, downlight):
    admin_api.post(
        f"{SALES}checkout/",
        cart(shift["id"], [{"product": downlight.id}], [{"method": "cash", "amount": "4.5"}]),
        format="json",
    )
    closed = admin_api.post(
        f"{SHIFTS}{shift['id']}/close/",
        {"closing_cash": "54", "notes": "1 KD short"},
        format="json",
    )
    assert closed.status_code == 200, closed.json()
    body = closed.json()
    assert body["status"] == "closed" and body["expected_cash"] == "54.500"
    assert body["cash_difference"] == "-0.500" and body["sales_total"] == "4.500"
    assert journal_lines(body["difference_entry"]) == {
        ("6900", "0.500", "0.000"),
        ("1110", "0.000", "0.500"),
    }
    # Nothing more can be sold or voided on a closed shift.
    sale_id = Sale.objects.get().pk
    assert (
        admin_api.post(f"{SALES}{sale_id}/void/", {"reason": "late"}, format="json").status_code
        == 400
    )
    blocked = admin_api.post(
        f"{SALES}checkout/",
        cart(shift["id"], [{"product": downlight.id}], [{"method": "cash", "amount": "9"}]),
        format="json",
    )
    assert blocked.status_code == 400 and "shift" in blocked.json()["fields"]
    assert admin_api.get(f"{REG}{register.id}/").json()["open_shift"] is None


def test_close_with_exact_cash_books_nothing(admin_api, register, shift):
    body = admin_api.post(
        f"{SHIFTS}{shift['id']}/close/", {"closing_cash": "50"}, format="json"
    ).json()
    assert body["cash_difference"] == "0.000" and body["difference_entry"] is None
    assert Shift.objects.get(pk=shift["id"]).status == "closed"


# --- Access ------------------------------------------------------------------------------------


def test_branch_cashier_sees_own_branch_only(
    api, make_role, make_user, kuwait, showroom1, showroom2, register, warehouses, walk_in
):
    other = Register.objects.create(
        company=kuwait,
        branch=showroom2,
        code="SR2-T1",
        name="Showroom 2 till",
        warehouse=warehouses["kw_sr2"],
        cash_journal=register.cash_journal,
        card_journal=register.card_journal,
        walk_in_customer=walk_in,
    )
    cashier = make_role("cashier", "pos.register.view", "pos.shift.view", "pos.shift.open")
    api.force_authenticate(make_user((cashier, kuwait, showroom1)))
    assert [r["code"] for r in api.get(REG).json()["results"]] == ["SR1-T1"]
    denied = api.post(f"{SHIFTS}open/", {"register": other.id, "opening_cash": "0"}, format="json")
    assert denied.status_code == 403
    assert api.post(f"{SALES}checkout/", {}, format="json").status_code == 403
