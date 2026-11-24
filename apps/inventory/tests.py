from decimal import Decimal

import pytest

from apps.accounting.models import JournalEntry
from apps.accounting.setup import set_up_company
from apps.catalog.models import Product, ProductCategory, UnitOfMeasure
from apps.inventory.models import ProductCost, Reservation, StockBalance, StockMove, StockStatus
from apps.inventory.services import StockError, available_quantity, reserve

pytestmark = pytest.mark.django_db

DAY = "2026-03-10"
ADJ = "/api/v1/inventory/adjustments/"
TRF = "/api/v1/inventory/transfers/"
CNT = "/api/v1/inventory/counts/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


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
def strip(db):
    family = ProductCategory.objects.create(code="strip", name="LED strip")
    return Product.objects.create(
        sku="MS-STRIP", name="LED strip", category=family, uom=UnitOfMeasure.objects.get(code="M")
    )


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


def opening(api, company, warehouse, product, qty, cost, status="available"):
    draft = api.post(
        ADJ,
        {
            "company": company.id,
            "date": DAY,
            "warehouse": warehouse.id,
            "reason": "opening",
            "lines": [
                {
                    "product": product.id,
                    "status": status,
                    "quantity": str(qty),
                    "unit_cost": str(cost),
                }
            ],
        },
        format="json",
    )
    assert draft.status_code == 201, draft.json()
    posted = api.post(f"{ADJ}{draft.json()['id']}/post/")
    assert posted.status_code == 200, posted.json()
    return posted.json()


def qty(warehouse, product, status="available"):
    row = StockBalance.objects.filter(warehouse=warehouse, product=product, status=status).first()
    return row.quantity if row else Decimal(0)


# --- Adjustments and costing -------------------------------------------------------------------


def test_opening_stock_posts_quantity_value_and_journal(
    admin_api, kuwait, books, warehouses, downlight
):
    result = opening(admin_api, kuwait, warehouses["kw_main"], downlight, 100, "2.500")

    assert result["number"] == "KW-ADJ-2026-00001"
    assert result["status"] == "posted"
    assert qty(warehouses["kw_main"], downlight) == 100
    cost = ProductCost.objects.get(company=kuwait, product=downlight)
    assert (cost.quantity, cost.value, cost.average_cost) == (
        100,
        Decimal("250.000"),
        Decimal("2.5"),
    )
    entry = JournalEntry.objects.get(pk=result["journal_entry"])
    lines = {line.account.code: (line.debit, line.credit) for line in entry.lines.all()}
    assert lines["1140"] == (Decimal("250.000"), 0)  # inventory up
    assert lines["5400"] == (0, Decimal("250.000"))  # against inventory adjustments


def test_weighted_average_cost(admin_api, kuwait, books, warehouses, downlight):
    opening(admin_api, kuwait, warehouses["kw_main"], downlight, 100, "2.000")
    opening(admin_api, kuwait, warehouses["kw_main"], downlight, 100, "3.000")
    cost = ProductCost.objects.get(company=kuwait, product=downlight)
    assert cost.average_cost == Decimal("2.5")

    # Removing 50 units leaves at average cost, and the journal reflects 125.000
    draft = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "reason": "damage",
            "lines": [{"product": downlight.id, "status": "available", "quantity": "-50"}],
        },
        format="json",
    ).json()
    posted = admin_api.post(f"{ADJ}{draft['id']}/post/").json()
    cost.refresh_from_db()
    assert (cost.quantity, cost.value) == (150, Decimal("375.000"))
    entry = JournalEntry.objects.get(pk=posted["journal_entry"])
    assert entry.lines.get(account__code="1140").credit == Decimal("125.000")


def test_cannot_remove_more_than_on_hand(admin_api, kuwait, books, warehouses, downlight):
    opening(admin_api, kuwait, warehouses["kw_main"], downlight, 10, "1")
    draft = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "lines": [{"product": downlight.id, "status": "available", "quantity": "-11"}],
        },
        format="json",
    ).json()
    response = admin_api.post(f"{ADJ}{draft['id']}/post/")
    assert response.status_code == 400
    assert "Not enough" in response.json()["fields"]["lines"]
    assert qty(warehouses["kw_main"], downlight) == 10  # nothing changed


def test_status_change_moves_to_damaged_without_accounting(
    admin_api, kuwait, books, warehouses, downlight
):
    opening(admin_api, kuwait, warehouses["kw_main"], downlight, 10, "4")
    draft = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "reason": "status",
            "lines": [
                {"product": downlight.id, "status": "available", "quantity": "-3"},
                {"product": downlight.id, "status": "damaged", "quantity": "3"},
            ],
        },
        format="json",
    ).json()
    posted = admin_api.post(f"{ADJ}{draft['id']}/post/").json()
    assert qty(warehouses["kw_main"], downlight) == 7
    assert qty(warehouses["kw_main"], downlight, "damaged") == 3
    assert posted["journal_entry"] is None
    assert ProductCost.objects.get(company=kuwait, product=downlight).value == Decimal("40.000")


def test_status_change_must_net_to_zero(admin_api, kuwait, books, warehouses, downlight):
    opening(admin_api, kuwait, warehouses["kw_main"], downlight, 10, "4")
    draft = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "reason": "status",
            "lines": [{"product": downlight.id, "status": "damaged", "quantity": "3"}],
        },
        format="json",
    ).json()
    assert admin_api.post(f"{ADJ}{draft['id']}/post/").status_code == 400


def test_whole_units_and_stock_items_only(admin_api, kuwait, books, warehouses, downlight, strip):
    bad = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "lines": [
                {
                    "product": downlight.id,
                    "status": "available",
                    "quantity": "2.5",
                    "unit_cost": "1",
                }
            ],
        },
        format="json",
    )
    assert bad.status_code == 400 and "whole" in bad.json()["fields"]["lines"]
    ok = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "lines": [
                {"product": strip.id, "status": "available", "quantity": "2.5", "unit_cost": "1"}
            ],
        },
        format="json",
    )
    assert ok.status_code == 201
    service = Product.objects.create(
        sku="INSTALL",
        name="Installation",
        category=downlight.category,
        uom=downlight.uom,
        product_type="service",
    )
    bad2 = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "lines": [{"product": service.id, "status": "available", "quantity": "1"}],
        },
        format="json",
    )
    assert bad2.status_code == 400


def test_posting_needs_accounting_setup(admin_api, kuwait, warehouses, downlight):
    draft = admin_api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "lines": [
                {"product": downlight.id, "status": "available", "quantity": "1", "unit_cost": "1"}
            ],
        },
        format="json",
    ).json()
    response = admin_api.post(f"{ADJ}{draft['id']}/post/")
    assert response.status_code == 400
    assert "Set up accounting" in response.json()["message"]
    assert qty(warehouses["kw_main"], downlight) == 0  # rolled back


# --- Transfers -----------------------------------------------------------------------------------


def transfer(api, company, source, target, product, quantity):
    draft = api.post(
        TRF,
        {
            "company": company.id,
            "date": DAY,
            "from_warehouse": source.id,
            "to_warehouse": target.id,
            "lines": [{"product": product.id, "quantity": str(quantity)}],
        },
        format="json",
    )
    assert draft.status_code == 201, draft.json()
    return draft.json()


def test_transfer_out_in_transit_in(admin_api, kuwait, books, warehouses, downlight):
    main, sr1 = warehouses["kw_main"], warehouses["kw_sr1"]
    opening(admin_api, kuwait, main, downlight, 20, "2")
    draft = transfer(admin_api, kuwait, main, sr1, downlight, 8)

    sent = admin_api.post(f"{TRF}{draft['id']}/dispatch/")
    assert sent.status_code == 200, sent.json()
    assert sent.json()["status"] == "in_transit" and sent.json()["number"] == "KW-TRF-2026-00001"
    assert qty(main, downlight) == 12
    assert qty(sr1, downlight, "in_transit") == 8
    assert qty(sr1, downlight) == 0

    line_id = sent.json()["lines"][0]["id"]
    partial = admin_api.post(
        f"{TRF}{draft['id']}/receive/",
        {"date": DAY, "quantities": {str(line_id): "5"}},
        format="json",
    )
    assert partial.status_code == 200, partial.json()
    assert partial.json()["status"] == "partial"
    assert qty(sr1, downlight) == 5 and qty(sr1, downlight, "in_transit") == 3

    rest = admin_api.post(
        f"{TRF}{draft['id']}/receive/",
        {"date": DAY, "quantities": {str(line_id): "3"}},
        format="json",
    )
    assert rest.json()["status"] == "received"
    assert qty(sr1, downlight) == 8 and qty(sr1, downlight, "in_transit") == 0
    # Internal moves never change the company's stock value.
    assert ProductCost.objects.get(company=kuwait, product=downlight).value == Decimal("40.000")
    assert StockMove.objects.filter(source_type="inventory.stocktransfer").count() == 6


def test_cannot_receive_more_than_sent(admin_api, kuwait, books, warehouses, downlight):
    main, sr1 = warehouses["kw_main"], warehouses["kw_sr1"]
    opening(admin_api, kuwait, main, downlight, 20, "2")
    draft = transfer(admin_api, kuwait, main, sr1, downlight, 8)
    sent = admin_api.post(f"{TRF}{draft['id']}/dispatch/").json()
    line_id = sent["lines"][0]["id"]
    response = admin_api.post(
        f"{TRF}{draft['id']}/receive/",
        {"date": DAY, "quantities": {str(line_id): "9"}},
        format="json",
    )
    assert response.status_code == 400


def test_transfer_respects_reservations(admin_api, kuwait, books, warehouses, downlight):
    main, sr1 = warehouses["kw_main"], warehouses["kw_sr1"]
    opening(admin_api, kuwait, main, downlight, 10, "2")
    reserve(kuwait, main, downlight, Decimal(7), reference="PRJ-1")
    draft = transfer(admin_api, kuwait, main, sr1, downlight, 5)
    response = admin_api.post(f"{TRF}{draft['id']}/dispatch/")
    assert response.status_code == 400
    assert "Only 3" in response.json()["fields"]["lines"]


def test_transfer_needs_two_different_warehouses_of_same_company(
    admin_api, kuwait, warehouses, downlight
):
    same = admin_api.post(
        TRF,
        {
            "company": kuwait.id,
            "date": DAY,
            "from_warehouse": warehouses["kw_main"].id,
            "to_warehouse": warehouses["kw_main"].id,
            "lines": [{"product": downlight.id, "quantity": "1"}],
        },
        format="json",
    )
    other = admin_api.post(
        TRF,
        {
            "company": kuwait.id,
            "date": DAY,
            "from_warehouse": warehouses["kw_main"].id,
            "to_warehouse": warehouses["ae_main"].id,
            "lines": [{"product": downlight.id, "quantity": "1"}],
        },
        format="json",
    )
    assert same.status_code == other.status_code == 400


# --- Counts --------------------------------------------------------------------------------------


def test_count_fill_and_post_adjusts_differences(
    admin_api, kuwait, books, warehouses, downlight, strip
):
    main = warehouses["kw_main"]
    opening(admin_api, kuwait, main, downlight, 50, "2")
    opening(admin_api, kuwait, main, strip, 30, "1")

    draft = admin_api.post(
        CNT, {"company": kuwait.id, "date": DAY, "warehouse": main.id, "lines": []}, format="json"
    ).json()
    filled = admin_api.post(f"{CNT}{draft['id']}/fill/").json()
    assert filled["added"] == 2
    assert {line["sku"]: line["expected_quantity"] for line in filled["lines"]} == {
        "MS-DL-10W": "50.0000",
        "MS-STRIP": "30.0000",
    }

    lines = [
        {"product": downlight.id, "counted_quantity": "48"},
        {"product": strip.id, "counted_quantity": "31.5"},
    ]
    admin_api.patch(f"{CNT}{draft['id']}/", {"lines": lines}, format="json")
    posted = admin_api.post(f"{CNT}{draft['id']}/post/")
    assert posted.status_code == 200, posted.json()
    assert posted.json()["number"] == "KW-CNT-2026-00001"
    assert qty(main, downlight) == 48 and qty(main, strip) == Decimal("31.5")
    entry = JournalEntry.objects.get(pk=posted.json()["journal_entry"])
    # −2 × 2.000 + 1.5 × 1.000 = −2.500 → inventory credited 2.500
    assert entry.lines.get(account__code="1140").credit == Decimal("2.500")


def test_count_with_no_differences_posts_no_journal(
    admin_api, kuwait, books, warehouses, downlight
):
    main = warehouses["kw_main"]
    opening(admin_api, kuwait, main, downlight, 5, "2")
    draft = admin_api.post(
        CNT,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": main.id,
            "lines": [{"product": downlight.id, "counted_quantity": "5"}],
        },
        format="json",
    ).json()
    posted = admin_api.post(f"{CNT}{draft['id']}/post/").json()
    assert posted["status"] == "posted" and posted["journal_entry"] is None


# --- Reservations, levels, reports ---------------------------------------------------------------


def test_reservation_limits_and_release(admin_api, kuwait, books, warehouses, downlight):
    main = warehouses["kw_main"]
    opening(admin_api, kuwait, main, downlight, 10, "2")
    url = "/api/v1/inventory/reservations/"
    first = admin_api.post(
        url,
        {
            "company": kuwait.id,
            "warehouse": main.id,
            "product": downlight.id,
            "quantity": "6",
            "reference": "PRJ-1",
        },
    )
    second = admin_api.post(
        url,
        {
            "company": kuwait.id,
            "warehouse": main.id,
            "product": downlight.id,
            "quantity": "5",
            "reference": "PRJ-2",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 400 and "Only 4" in second.json()["fields"]["quantity"]

    levels = admin_api.get("/api/v1/inventory/stock/", {"company": kuwait.id}).json()["rows"]
    row = next(r for r in levels if r["sku"] == "MS-DL-10W")
    assert (row["available"], row["reserved"], row["free"], row["value"]) == (
        "10.0000",
        "6.0000",
        "4.0000",
        "20.000",
    )

    released = admin_api.post(f"{url}{first.json()['id']}/release/")
    assert released.json()["status"] == "released"
    assert (
        admin_api.post(
            url,
            {"company": kuwait.id, "warehouse": main.id, "product": downlight.id, "quantity": "5"},
        ).status_code
        == 201
    )


def test_reorder_and_slow_moving_reports(admin_api, kuwait, books, warehouses, downlight, strip):
    main = warehouses["kw_main"]
    opening(admin_api, kuwait, main, downlight, 3, "2")
    opening(admin_api, kuwait, main, strip, 100, "1")
    rule = admin_api.post(
        "/api/v1/inventory/reorder-rules/",
        {
            "company": kuwait.id,
            "warehouse": main.id,
            "product": downlight.id,
            "min_quantity": "10",
            "reorder_quantity": "50",
        },
    )
    assert rule.status_code == 201, rule.json()
    admin_api.post(
        "/api/v1/inventory/reorder-rules/",
        {
            "company": kuwait.id,
            "warehouse": main.id,
            "product": strip.id,
            "min_quantity": "10",
            "reorder_quantity": "50",
        },
    )

    from apps.inventory.models import ReorderRule

    rules = list(
        ReorderRule.objects.values("warehouse_id", "product_id", "min_quantity", "is_active")
    )
    report = admin_api.get("/api/v1/inventory/reports/reorder/", {"company": kuwait.id}).json()
    levels = admin_api.get("/api/v1/inventory/stock/", {"company": kuwait.id}).json()
    assert all(rule["is_active"] for rule in rules)
    assert levels["rows"]
    assert [(r["sku"], r["free"], r["suggested"]) for r in report["rows"]] == [
        ("MS-DL-10W", "3.0000", "50.0000")
    ]

    slow = admin_api.get(
        "/api/v1/inventory/reports/slow-moving/", {"company": kuwait.id, "days": 30}
    ).json()["rows"]
    assert {r["sku"] for r in slow} == {"MS-DL-10W", "MS-STRIP"}  # nothing has gone out yet


# --- Access --------------------------------------------------------------------------------------


def test_branch_storekeeper_sees_only_their_warehouses(
    api, make_role, make_user, kuwait, books, warehouses, downlight, showroom1
):
    role = make_role(
        "keeper",
        "inventory.stock.view",
        "inventory.adjustment.view",
        "inventory.adjustment.create",
        "inventory.adjustment.post",
    )
    api.force_authenticate(make_user((role, kuwait, showroom1)))
    sr1, main = warehouses["kw_sr1"], warehouses["kw_main"]

    own = api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": sr1.id,
            "lines": [
                {"product": downlight.id, "status": "available", "quantity": "2", "unit_cost": "1"}
            ],
        },
        format="json",
    )
    other = api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": main.id,
            "lines": [
                {"product": downlight.id, "status": "available", "quantity": "2", "unit_cost": "1"}
            ],
        },
        format="json",
    )
    assert own.status_code == 201
    assert other.status_code == 403
    assert api.post(f"{ADJ}{own.json()['id']}/post/").status_code == 200

    levels = api.get("/api/v1/inventory/stock/", {"company": kuwait.id}).json()["rows"]
    assert {r["warehouse"] for r in levels} == {sr1.id}


def test_posting_needs_post_permission(
    api, make_role, make_user, kuwait, books, warehouses, downlight
):
    role = make_role("clerk", "inventory.adjustment.view", "inventory.adjustment.create")
    api.force_authenticate(make_user((role, kuwait, None)))
    draft = api.post(
        ADJ,
        {
            "company": kuwait.id,
            "date": DAY,
            "warehouse": warehouses["kw_main"].id,
            "lines": [
                {"product": downlight.id, "status": "available", "quantity": "2", "unit_cost": "1"}
            ],
        },
        format="json",
    )
    assert draft.status_code == 201
    assert api.post(f"{ADJ}{draft.json()['id']}/post/").status_code == 403


def test_service_helpers(kuwait, warehouses, downlight):
    assert available_quantity(warehouses["kw_main"], downlight) == 0
    with pytest.raises(StockError):
        reserve(kuwait, warehouses["kw_main"], downlight, Decimal(1))
    assert Reservation.objects.count() == 0
    assert StockStatus.AVAILABLE == "available"


def test_item_card_shows_stock_reserved_and_incoming(
    admin_api, kuwait, books, warehouses, downlight
):
    main, sr1 = warehouses["kw_main"], warehouses["kw_sr1"]
    opening(admin_api, kuwait, main, downlight, 20, "2")
    reserve(kuwait, main, downlight, Decimal(4), reference="PRJ-1")
    draft = transfer(admin_api, kuwait, main, sr1, downlight, 5)
    sent = admin_api.post(f"{TRF}{draft['id']}/dispatch/").json()
    admin_api.post(
        f"{TRF}{draft['id']}/receive/",
        {"date": DAY, "quantities": {str(sent["lines"][0]["id"]): "3"}},
        format="json",
    )

    card = admin_api.get(f"/api/v1/inventory/items/{downlight.id}/?company={kuwait.id}").json()
    rows = {row["code"]: row for row in card["warehouses"]}
    assert card["sku"] == downlight.sku and card["uom"]
    assert rows[main.code]["on_hand"] == "15" and rows[main.code]["reserved"] == "4"
    assert rows[main.code]["free"] == "11"
    assert rows[sr1.code]["on_hand"] == "3" and rows[sr1.code]["in_transit"] == "2"
    assert [(i["kind"], i["remaining"]) for i in card["incoming"]] == [("transfer", "2")]
    assert card["moves"] and card["moves"][0]["reference"]


# --- Reset for a fresh demo run ------------------------------------------------------------------


def test_reset_transactions_clears_documents_and_keeps_master_data(
    admin_api, kuwait, books, warehouses, downlight
):
    from django.core.management import CommandError, call_command

    from apps.accounting.models import Account
    from apps.inventory.models import StockTransfer
    from apps.organization.models import DocumentSequence

    main, sr1 = warehouses["kw_main"], warehouses["kw_sr1"]
    opening(admin_api, kuwait, main, downlight, 20, "2")
    transfer(admin_api, kuwait, main, sr1, downlight, 5)

    with pytest.raises(CommandError):
        call_command("reset_transactions")  # needs --yes
    call_command("reset_transactions", "--dry-run")
    assert StockTransfer.objects.count() == 1

    call_command("reset_transactions", "--yes")
    assert not StockTransfer.objects.exists()
    assert not StockBalance.objects.exists() and not StockMove.objects.exists()
    assert not JournalEntry.objects.exists()
    assert Product.objects.filter(pk=downlight.pk).exists()
    assert Account.objects.filter(company=kuwait, code="1140").exists()
    assert DocumentSequence.objects.get(company=kuwait, doc_type="ADJ").next_number == 1
    # Numbering starts again from 00001.
    assert opening(admin_api, kuwait, main, downlight, 1, "2")["number"] == "KW-ADJ-2026-00001"


def test_count_fill_by_category_and_transfer_from_csv(
    admin_api, kuwait, books, warehouses, downlight, strip
):
    from django.core.files.uploadedfile import SimpleUploadedFile

    main, sr1 = warehouses["kw_main"], warehouses["kw_sr1"]
    opening(admin_api, kuwait, main, downlight, 20, "2")
    opening(admin_api, kuwait, main, strip, 30, "1")
    count = admin_api.post(
        CNT, {"company": kuwait.id, "date": DAY, "warehouse": main.id, "lines": []}, format="json"
    ).json()
    filled = admin_api.post(
        f"{CNT}{count['id']}/fill/", {"category": strip.category_id}, format="json"
    ).json()
    assert filled["added"] == 1 and filled["lines"][0]["product"] == strip.id

    upload = SimpleUploadedFile(
        "transfer.csv", b"sku,quantity\nMS-DL-10W,5\nMS-STRIP,2.5\n", content_type="text/csv"
    )
    made = admin_api.post(
        f"{TRF}import/",
        {
            "file": upload,
            "company": kuwait.id,
            "date": DAY,
            "from_warehouse": main.id,
            "to_warehouse": sr1.id,
        },
        format="multipart",
    )
    assert made.status_code == 201, made.json()
    assert made.json()["status"] == "draft" and len(made.json()["lines"]) == 2
    wrong = SimpleUploadedFile("transfer.csv", b"sku,quantity\nNOPE,1\n", content_type="text/csv")
    refused = admin_api.post(
        f"{TRF}import/",
        {
            "file": wrong,
            "company": kuwait.id,
            "date": DAY,
            "from_warehouse": main.id,
            "to_warehouse": sr1.id,
        },
        format="multipart",
    )
    assert refused.status_code == 400 and "NOPE" in refused.json()["fields"]["file"]
