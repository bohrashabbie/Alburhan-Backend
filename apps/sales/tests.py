from datetime import date
from decimal import Decimal

import pytest

from apps.accounting.models import Journal, JournalEntry
from apps.accounting.setup import set_up_company
from apps.catalog.models import Product, ProductCategory, UnitOfMeasure
from apps.inventory.models import StockBalance, StockMove, StockStatus
from apps.inventory.services import move
from apps.partners.models import Customer
from apps.sales.models import Invoice, Quotation, SalesSettings

pytestmark = pytest.mark.django_db

DAY = "2026-03-10"
QT = "/api/v1/sales/quotations/"
INV = "/api/v1/sales/invoices/"
DN = "/api/v1/sales/deliveries/"
RCT = "/api/v1/sales/receipts/"
CN = "/api/v1/sales/credit-notes/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


@pytest.fixture
def customer(kuwait):
    return Customer.objects.create(
        company=kuwait, code="KW-CUS-00001", name="Ahmad Al-Sabah", credit_limit=Decimal("0")
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
    """50 downlights in the main warehouse at 2.000 each."""
    move(
        kuwait,
        warehouses["kw_main"],
        downlight,
        StockStatus.AVAILABLE,
        Decimal(50),
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


def invoice_payload(kuwait, showroom1, customer, lines, **extra):
    return {
        "company": kuwait.id,
        "date": DAY,
        "branch": showroom1.id,
        "customer": customer.id,
        "currency": "KWD",
        "lines": lines,
        **extra,
    }


def line(product, qty="1", price="10.000", discount="0", tax="0"):
    return {
        "product": product.id,
        "quantity": qty,
        "unit_price": price,
        "discount_percent": discount,
        "tax_rate": tax,
    }


def journal_lines(entry_id):
    entry = JournalEntry.objects.get(pk=entry_id)
    return {(ln.account.code, str(ln.debit), str(ln.credit)) for ln in entry.lines.all()}


def qty(warehouse, product, status="available"):
    row = StockBalance.objects.filter(warehouse=warehouse, product=product, status=status).first()
    return row.quantity if row else Decimal(0)


# --- Quotation → invoice -----------------------------------------------------------------------


def test_quotation_totals_send_and_convert(
    admin_api, kuwait, books, showroom1, customer, downlight
):
    draft = admin_api.post(
        QT,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "10", "4.500", "10")]),
        format="json",
    )
    assert draft.status_code == 201, draft.json()
    body = draft.json()
    assert (body["subtotal"], body["tax_total"], body["total"]) == (
        "40.500",
        "0.000",
        "40.500",
    )  # 45 − 10%
    assert body["number"] == "" and body["status"] == "draft"

    sent = admin_api.post(f"{QT}{body['id']}/send/").json()
    assert sent["status"] == "sent" and sent["number"] == "KW-QT-2026-00001"
    assert sent["valid_until"] == "2026-04-09"  # 30 days by default

    converted = admin_api.post(f"{QT}{body['id']}/convert/")
    assert converted.status_code == 201, converted.json()
    invoice = converted.json()
    assert invoice["status"] == "draft" and invoice["total"] == "40.500"
    assert invoice["lines"][0]["discount_percent"] == "10.00"
    assert admin_api.get(f"{QT}{body['id']}/").json()["status"] == "accepted"

    again = admin_api.post(f"{QT}{body['id']}/convert/")
    assert again.status_code == 400


def test_vat_applies_per_line(admin_api, kuwait, books, showroom1, customer, downlight):
    body = admin_api.post(
        QT,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "2", "100", "0", "5")]),
        format="json",
    ).json()
    assert (body["subtotal"], body["tax_total"], body["total"]) == ("200.000", "10.000", "210.000")


def test_discount_ceiling_needs_approval(
    api, make_role, make_user, kuwait, books, showroom1, customer, downlight
):
    SalesSettings.objects.update_or_create(
        company=kuwait, defaults={"max_line_discount_percent": Decimal("15")}
    )
    seller = make_role("seller", "sales.quotation.view", "sales.quotation.create")
    api.force_authenticate(make_user((seller, kuwait, showroom1)))
    too_much = api.post(
        QT,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "100", "20")]),
        format="json",
    )
    assert too_much.status_code == 400 and "approval" in too_much.json()["fields"]["lines"]
    fine = api.post(
        QT,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "100", "15")]),
        format="json",
    )
    assert fine.status_code == 201

    manager = make_role(
        "manager", "sales.quotation.view", "sales.quotation.create", "sales.discount.approve"
    )
    api.force_authenticate(make_user((manager, kuwait, showroom1)))
    assert (
        api.post(
            QT,
            invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "100", "40")]),
            format="json",
        ).status_code
        == 201
    )


def test_line_rules(admin_api, kuwait, books, showroom1, customer, downlight):
    for bad in (
        line(downlight, "0"),
        line(downlight, "2.5"),
        line(downlight, "1", "-1"),
        line(downlight, "1", "1", "101"),
    ):
        response = admin_api.post(
            QT, invoice_payload(kuwait, showroom1, customer, [bad]), format="json"
        )
        assert response.status_code == 400, bad


# --- Invoice posting -------------------------------------------------------------------------------


def test_post_invoice_books_receivable_and_sales(
    admin_api, kuwait, books, showroom1, customer, downlight
):
    draft = admin_api.post(
        INV,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "10", "4.500", "0", "5")]),
        format="json",
    ).json()
    posted = admin_api.post(f"{INV}{draft['id']}/post/")
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["number"] == "KW-INV-2026-00001" and body["status"] == "posted"
    assert body["amount_due"] == "47.250" and body["delivery_status"] == "pending"
    assert journal_lines(body["journal_entry"]) == {
        ("1130", "47.250", "0.000"),  # receivable
        ("4100", "0.000", "45.000"),  # sales
        ("2130", "0.000", "2.250"),  # VAT payable
    }
    entry = JournalEntry.objects.get(pk=body["journal_entry"])
    assert entry.lines.get(account__code="1130").customer == customer


def test_posted_invoice_is_locked(admin_api, kuwait, books, showroom1, customer, downlight):
    draft = admin_api.post(
        INV, invoice_payload(kuwait, showroom1, customer, [line(downlight)]), format="json"
    ).json()
    admin_api.post(f"{INV}{draft['id']}/post/")
    assert (
        admin_api.patch(f"{INV}{draft['id']}/", {"reference": "x"}, format="json").status_code
        == 400
    )
    assert admin_api.delete(f"{INV}{draft['id']}/").status_code == 400


def test_credit_limit_blocks_posting(admin_api, kuwait, books, showroom1, customer, downlight):
    customer.credit_limit = Decimal("100")
    customer.save()
    first = admin_api.post(
        INV,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "80")]),
        format="json",
    ).json()
    assert admin_api.post(f"{INV}{first['id']}/post/").status_code == 200
    second = admin_api.post(
        INV,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "30")]),
        format="json",
    ).json()
    response = admin_api.post(f"{INV}{second['id']}/post/")
    assert response.status_code == 400 and "Credit limit" in response.json()["fields"]["customer"]


def test_cancel_unpaid_invoice_reverses_entry(
    admin_api, kuwait, books, showroom1, customer, downlight
):
    draft = admin_api.post(
        INV,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "50")]),
        format="json",
    ).json()
    admin_api.post(f"{INV}{draft['id']}/post/")
    cancelled = admin_api.post(f"{INV}{draft['id']}/cancel/", {"date": DAY})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "cancelled"
    assert JournalEntry.objects.filter(reversal_of__isnull=False).count() == 1


# --- Delivery --------------------------------------------------------------------------------------


def post_invoice(admin_api, kuwait, showroom1, customer, lines, **extra):
    draft = admin_api.post(
        INV, invoice_payload(kuwait, showroom1, customer, lines, **extra), format="json"
    ).json()
    posted = admin_api.post(f"{INV}{draft['id']}/post/")
    assert posted.status_code == 200, posted.json()
    return posted.json()


def test_partial_delivery_issues_stock_and_cogs(
    admin_api, kuwait, stocked, showroom1, customer, downlight, install_service
):
    invoice = post_invoice(
        admin_api,
        kuwait,
        showroom1,
        customer,
        [line(downlight, "10", "5"), line(install_service, "2", "15")],
        warehouse=stocked.id,
    )
    product_line = next(ln for ln in invoice["lines"] if ln["sku"] == "MS-DL-10W")

    first = admin_api.post(
        DN,
        {
            "company": kuwait.id,
            "date": DAY,
            "invoice": invoice["id"],
            "warehouse": stocked.id,
            "delivered_to": "Ahmad",
            "lines": [{"invoice_line": product_line["id"], "quantity": "6"}],
        },
        format="json",
    )
    assert first.status_code == 201, first.json()
    posted = admin_api.post(f"{DN}{first.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    assert posted.json()["number"] == "KW-DN-2026-00001"
    assert qty(stocked, downlight) == 44
    assert journal_lines(posted.json()["journal_entry"]) == {
        ("5100", "12.000", "0.000"),
        ("1140", "0.000", "12.000"),
    }  # 6 × 2.000

    refreshed = admin_api.get(f"{INV}{invoice['id']}/").json()
    assert refreshed["delivery_status"] == "partial"
    assert (
        next(ln for ln in refreshed["lines"] if ln["sku"] == "MS-DL-10W")["delivered_quantity"]
        == "6.0000"
    )

    too_many = admin_api.post(
        DN,
        {
            "company": kuwait.id,
            "date": DAY,
            "invoice": invoice["id"],
            "warehouse": stocked.id,
            "lines": [{"invoice_line": product_line["id"], "quantity": "5"}],
        },
        format="json",
    ).json()
    assert admin_api.post(f"{DN}{too_many['id']}/post/").status_code == 400

    rest = admin_api.post(
        DN,
        {
            "company": kuwait.id,
            "date": DAY,
            "invoice": invoice["id"],
            "warehouse": stocked.id,
            "lines": [{"invoice_line": product_line["id"], "quantity": "4"}],
        },
        format="json",
    ).json()
    admin_api.post(f"{DN}{rest['id']}/post/")
    assert admin_api.get(f"{INV}{invoice['id']}/").json()["delivery_status"] == "delivered"


def test_delivery_needs_posted_invoice_and_stock(
    admin_api, kuwait, books, warehouses, showroom1, customer, downlight
):
    draft = admin_api.post(
        INV,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "5")]),
        format="json",
    ).json()
    response = admin_api.post(
        DN,
        {
            "company": kuwait.id,
            "date": DAY,
            "invoice": draft["id"],
            "warehouse": warehouses["kw_main"].id,
            "lines": [{"invoice_line": draft["lines"][0]["id"], "quantity": "1"}],
        },
        format="json",
    )
    assert response.status_code == 400 and "invoice" in response.json()["fields"]

    invoice = post_invoice(admin_api, kuwait, showroom1, customer, [line(downlight, "1", "5")])
    delivery = admin_api.post(
        DN,
        {
            "company": kuwait.id,
            "date": DAY,
            "invoice": invoice["id"],
            "warehouse": warehouses["kw_main"].id,
            "lines": [{"invoice_line": invoice["lines"][0]["id"], "quantity": "1"}],
        },
        format="json",
    ).json()
    no_stock = admin_api.post(f"{DN}{delivery['id']}/post/")
    assert no_stock.status_code == 400 and "free" in no_stock.json()["fields"]["lines"]


# --- Receipts --------------------------------------------------------------------------------------


def test_receipt_settles_invoices_and_holds_advance(
    admin_api, kuwait, books, showroom1, customer, downlight
):
    inv1 = post_invoice(admin_api, kuwait, showroom1, customer, [line(downlight, "1", "100")])
    inv2 = post_invoice(admin_api, kuwait, showroom1, customer, [line(downlight, "1", "50")])
    cash = Journal.objects.get(company=kuwait, code="CS")

    receipt = admin_api.post(
        RCT,
        {
            "company": kuwait.id,
            "date": DAY,
            "branch": showroom1.id,
            "customer": customer.id,
            "method": "knet",
            "journal": cash.id,
            "currency": "KWD",
            "amount": "170.000",
            "allocations": [
                {"invoice": inv1["id"], "amount": "100.000"},
                {"invoice": inv2["id"], "amount": "30.000"},
            ],
        },
        format="json",
    )
    assert receipt.status_code == 201, receipt.json()
    posted = admin_api.post(f"{RCT}{receipt.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["number"] == "KW-RCT-2026-00001" and body["unallocated_amount"] == "40.000"
    assert journal_lines(body["journal_entry"]) == {
        ("1110", "170.000", "0.000"),
        ("1130", "0.000", "100.000"),
        ("1130", "0.000", "30.000"),
        ("2120", "0.000", "40.000"),  # customer advance
    }
    assert admin_api.get(f"{INV}{inv1['id']}/").json()["status"] == "paid"
    second = admin_api.get(f"{INV}{inv2['id']}/").json()
    assert second["status"] == "partially_paid" and second["amount_due"] == "20.000"

    balance = admin_api.get(f"/api/v1/sales/customers/{customer.id}/balance/").json()
    assert (balance["open_invoices"], balance["amount_due"], balance["advances"]) == (
        1,
        "20.000",
        "40.000",
    )


def test_receipt_cannot_overpay(admin_api, kuwait, books, showroom1, customer, downlight):
    inv = post_invoice(admin_api, kuwait, showroom1, customer, [line(downlight, "1", "100")])
    cash = Journal.objects.get(company=kuwait, code="CS")
    receipt = admin_api.post(
        RCT,
        {
            "company": kuwait.id,
            "date": DAY,
            "branch": showroom1.id,
            "customer": customer.id,
            "journal": cash.id,
            "currency": "KWD",
            "amount": "150",
            "allocations": [{"invoice": inv["id"], "amount": "150"}],
        },
        format="json",
    ).json()
    response = admin_api.post(f"{RCT}{receipt['id']}/post/")
    assert (
        response.status_code == 400
        and "allocate between" in response.json()["fields"]["allocations"]
    )


def test_receipt_journal_must_be_cash_or_bank(admin_api, kuwait, books, showroom1, customer):
    general = Journal.objects.get(company=kuwait, code="JV")
    response = admin_api.post(
        RCT,
        {
            "company": kuwait.id,
            "date": DAY,
            "branch": showroom1.id,
            "customer": customer.id,
            "journal": general.id,
            "currency": "KWD",
            "amount": "10",
        },
        format="json",
    )
    assert response.status_code == 400 and "journal" in response.json()["fields"]


# --- Credit notes ------------------------------------------------------------------------------------


def test_credit_note_returns_goods_and_reduces_due(
    admin_api, kuwait, stocked, showroom1, customer, downlight
):
    invoice = post_invoice(
        admin_api,
        kuwait,
        showroom1,
        customer,
        [line(downlight, "4", "10", "0", "5")],
        warehouse=stocked.id,
    )
    inv_line = invoice["lines"][0]
    delivery = admin_api.post(
        DN,
        {
            "company": kuwait.id,
            "date": DAY,
            "invoice": invoice["id"],
            "warehouse": stocked.id,
            "lines": [{"invoice_line": inv_line["id"], "quantity": "4"}],
        },
        format="json",
    ).json()
    admin_api.post(f"{DN}{delivery['id']}/post/")

    note = admin_api.post(
        CN,
        {
            "company": kuwait.id,
            "date": DAY,
            "branch": showroom1.id,
            "customer": customer.id,
            "currency": "KWD",
            "invoice": invoice["id"],
            "warehouse": stocked.id,
            "reason": "Wrong colour",
            "lines": [
                {
                    **line(downlight, "1", "10", "0", "5"),
                    "invoice_line": inv_line["id"],
                    "restock": True,
                }
            ],
        },
        format="json",
    )
    assert note.status_code == 201, note.json()
    posted = admin_api.post(f"{CN}{note.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    body = posted.json()
    assert body["number"] == "KW-CN-2026-00001" and body["total"] == "10.500"
    assert qty(stocked, downlight, "returned") == 1
    assert journal_lines(body["journal_entry"]) == {
        ("4400", "10.000", "0.000"),  # sales returns
        ("2130", "0.500", "0.000"),  # VAT back
        ("1130", "0.000", "10.500"),  # receivable down
        ("1140", "2.000", "0.000"),  # stock back at cost
        ("5100", "0.000", "2.000"),
    }
    refreshed = admin_api.get(f"{INV}{invoice['id']}/").json()
    assert refreshed["amount_credited"] == "10.500" and refreshed["amount_due"] == "31.500"

    too_many = admin_api.post(
        CN,
        {
            "company": kuwait.id,
            "date": DAY,
            "branch": showroom1.id,
            "customer": customer.id,
            "currency": "KWD",
            "invoice": invoice["id"],
            "warehouse": stocked.id,
            "lines": [{**line(downlight, "4", "10"), "invoice_line": inv_line["id"]}],
        },
        format="json",
    ).json()
    assert admin_api.post(f"{CN}{too_many['id']}/post/").status_code == 400


# --- Access ------------------------------------------------------------------------------------------


def test_showroom_seller_sees_only_own_branch(
    api, make_role, make_user, kuwait, books, showroom1, showroom2, customer, downlight
):
    seller = make_role("seller", "sales.invoice.view", "sales.invoice.create", "sales.invoice.post")
    api.force_authenticate(make_user((seller, kuwait, showroom1)))
    own = api.post(
        INV,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "5")]),
        format="json",
    )
    other = api.post(
        INV,
        invoice_payload(kuwait, showroom2, customer, [line(downlight, "1", "5")]),
        format="json",
    )
    assert own.status_code == 201 and other.status_code == 403
    assert api.get(INV).json()["count"] == 1
    assert api.post(f"{INV}{own.json()['id']}/post/").status_code == 200


def test_posting_needs_post_permission(
    api, make_role, make_user, kuwait, books, showroom1, customer, downlight
):
    clerk = make_role("clerk", "sales.invoice.view", "sales.invoice.create")
    api.force_authenticate(make_user((clerk, kuwait, showroom1)))
    draft = api.post(
        INV,
        invoice_payload(kuwait, showroom1, customer, [line(downlight, "1", "5")]),
        format="json",
    ).json()
    assert api.post(f"{INV}{draft['id']}/post/").status_code == 403


def test_settings_created_on_first_read(admin_api, kuwait):
    body = admin_api.get(f"/api/v1/sales/settings/{kuwait.id}/").json()
    assert body["default_tax_rate"] == "0.00" and body["quotation_valid_days"] == 30
    updated = admin_api.patch(
        f"/api/v1/sales/settings/{kuwait.id}/", {"default_tax_rate": "5.00"}, format="json"
    )
    assert updated.status_code == 200 and updated.json()["default_tax_rate"] == "5.00"


def test_quotation_model_str(kuwait, showroom1, customer):
    quotation = Quotation.objects.create(
        company=kuwait,
        date=date(2026, 3, 1),
        branch=showroom1,
        customer=customer,
        currency_id="KWD",
    )
    assert str(quotation).startswith("Draft quotation")
    assert Invoice.Status.PAID == "paid"
