import json
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command

from apps.accounting.setup import set_up_company
from apps.catalog.models import PriceList, PriceListItem, Product
from apps.inventory.models import StockBalance
from apps.organization.models import Warehouse
from apps.partners.models import Customer, CustomerGroup, Supplier

pytestmark = pytest.mark.django_db

CUSTOMER = (
    '<span class="badge" data-dt-customer-type="individual">Individual</span><br>\r\n'
    "Hany\r\n<br>6789543\r\n<br>Hawally, Kuwait"
)


@pytest.fixture
def folder(tmp_path, kuwait, make_user):
    make_user(is_superuser=True)
    set_up_company(kuwait, 2026)
    PriceList.objects.create(
        company=kuwait, code="retail", name="Retail", currency_id="KWD", is_default=True
    )
    files = {
        "warehouses": [
            ["0", "1", "Shuwaikh Store", "+965 1", "", "Shuwaikh"],
            ["1", "3", "Hawally 1st branch", "+965 2", "", "Tunis street"],
        ],
        "brands": [["0", " ", "KOSLO", ""]],
        "categories": [{"id": 1, "name": "Socket"}, {"id": 2, "name": ""}],
        "units": [],
        "groups": [["0", "General Customers", "General", "0", ""]],
        "products": [
            {
                "id": 10,
                "name": "COMP-E442",
                "code": "104060342122",
                "brand": "KOSLO",
                "category": "Socket",
                "unit": "Piece",
                "price": 0.9,
                "cost": 0.35,
            },
            {
                "id": 11,
                "name": "Strip 5m",
                "code": "STRIP-5",
                "brand": "",
                "category": "",
                "unit": "Meter",
                "price": 2,
                "cost": 1,
            },
            {
                "id": 12,
                "name": "Same code again",
                "code": "104060342122",
                "brand": "KOSLO",
                "category": "Socket",
                "unit": "Piece",
                "price": 1,
                "cost": 0.5,
            },
        ],
        "customers": [
            [
                "",
                '<span data-customer-id="4">4</span>',
                "General Customers",
                CUSTOMER,
                "0.00",
                "5.60",
            ],
            [
                "",
                '<span data-customer-id="5">5</span>',
                "General Customers",
                CUSTOMER.replace("Hany", "Sara"),
                "0.00",
                "0.00",
            ],
        ],
        "suppliers": [["0", " ", "Local Market\n<br>local market\n<br>a@b.c", "405.60", ""]],
        "stock": {
            "10": {"product_warehouse": [["Shuwaikh Store", "Hawally 1st branch"], [50, -3]]},
            "11": {"product_warehouse": [["Shuwaikh Store", "Old Showroom"], [12.5, 4]]},
            "12": {"product_warehouse": [["Shuwaikh Store"], [7.75]]},
        },
    }
    for name, rows in files.items():
        (tmp_path / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")
    return tmp_path


def test_import_brings_master_data_and_is_safe_to_repeat(folder, kuwait):
    call_command("import_legacy", str(folder), "--company", "KW", "--dry-run")
    assert not Product.objects.exists() and not Customer.objects.exists()

    call_command("import_legacy", str(folder), "--company", "KW")
    assert set(Warehouse.objects.values_list("name", flat=True)) >= {
        "Shuwaikh Store",
        "Hawally 1st branch",
        "Old Showroom",  # only known from the stock file
    }
    assert Warehouse.objects.get(name="Shuwaikh Store").branch is None  # a store, no branch
    assert Warehouse.objects.get(name="Hawally 1st branch").branch.branch_type == "showroom"
    first = Product.objects.get(sku="104060342122")
    assert (first.name, first.brand.name, first.category.name, first.uom.code) == (
        "COMP-E442",
        "KOSLO",
        "Socket",
        "PCS",
    )
    assert Product.objects.get(sku="STRIP-5").uom.code == "M"
    assert Product.objects.filter(sku="104060342122-12").exists()  # a code used twice there
    assert PriceListItem.objects.get(product=first).unit_price == Decimal("0.900")

    hany = Customer.objects.get(name="Hany")
    assert (hany.mobile, hany.address, hany.code) == ("6789543", "Hawally, Kuwait", "KW-CUS-00001")
    assert hany.group == CustomerGroup.objects.get(name="General Customers")
    assert "due 5.60" in hany.notes
    assert "Hany" in (folder / "customer-balances-to-enter.csv").read_text(encoding="utf-8")
    assert Supplier.objects.get(name="Local Market").currency_id == "KWD"

    call_command("import_legacy", str(folder), "--company", "KW")  # again: nothing doubles
    assert Product.objects.count() == 3 and Customer.objects.count() == 2
    assert Supplier.objects.count() == 1


def test_opening_stock_skips_minus_and_rounds_pieces_down(folder, kuwait):
    call_command("import_legacy", str(folder), "--company", "KW")
    call_command("import_legacy", str(folder), "--company", "KW", "--stock", "--date", "2026-03-10")

    def on_hand(warehouse, sku):
        row = StockBalance.objects.filter(warehouse__name=warehouse, product__sku=sku).first()
        return row.quantity if row else Decimal(0)

    assert on_hand("Shuwaikh Store", "104060342122") == 50
    assert on_hand("Hawally 1st branch", "104060342122") == 0  # minus there: left out
    assert on_hand("Shuwaikh Store", "STRIP-5") == Decimal("12.5")  # metres keep fractions
    assert on_hand("Old Showroom", "STRIP-5") == 4
    assert on_hand("Shuwaikh Store", "104060342122-12") == 7  # 7.75 pieces → 7

    with pytest.raises(CommandError):
        call_command("import_legacy", str(folder), "--company", "KW", "--stock")


def test_past_documents_go_to_the_archive(folder, kuwait, api, make_user):
    from apps.reports.models import LegacyDocument

    (folder / "sales.json").write_text(
        json.dumps(
            [
                {
                    "id": "9",
                    "date": "2026-11-23",
                    "reference_no": "inv-26905",
                    "biller": "Al Burhan",
                    "customer": "<b>MSHAN</b>",
                    "sale_status": "Completed",
                    "payment_status": "Due",
                    "grand_total": "1,072.000",
                    "paid_amount": "72.000",
                    "due": "1,000.000",
                    "sale": ["x", "COB STRIP", "5"],
                    "options": "<a>Edit</a>",
                }
            ]
        ),
        encoding="utf-8",
    )
    (folder / "lines.json").write_text(
        json.dumps({"sale:9": [["COB STRIP [123]", 5, "Mtrs", 0, 12.5]]}), encoding="utf-8"
    )
    (folder / "adjustments.json").write_text(
        json.dumps([["0", "22-02-2026 15:26:58", "adr-1", "Amghara Store", "Lamp x2", "broken"]]),
        encoding="utf-8",
    )
    call_command("import_legacy", str(folder), "--company", "KW", "--documents")
    call_command(
        "import_legacy", str(folder), "--company", "KW", "--documents"
    )  # replaces, no doubles
    sale = LegacyDocument.objects.get(kind="sale")
    assert (sale.number, sale.party, sale.status, sale.payment_status) == (
        "inv-26905",
        "MSHAN",
        "Completed",
        "Due",
    )
    assert (sale.total, sale.paid, sale.due) == (
        Decimal("1072.000"),
        Decimal("72.000"),
        Decimal("1000.000"),
    )
    assert sale.details["biller"] == "Al Burhan" and "COB STRIP" in sale.details["sale"]
    assert sale.details["lines"] == [["COB STRIP [123]", 5, "Mtrs", 0, 12.5]]
    adjustment = LegacyDocument.objects.get(kind="adjustment")
    assert adjustment.date.isoformat() == "2026-02-22" and adjustment.warehouse == "Amghara Store"

    api.force_authenticate(make_user(is_superuser=True))
    body = api.get(
        "/api/v1/reports/legacy-documents/", {"company": kuwait.id, "search": "mshan"}
    ).json()
    assert body["count"] == 1 and Decimal(body["due"]) == 1000
    assert {row["kind"] for row in body["kinds"]} == {"sale", "adjustment"}


def test_expense_categories_banks_and_users_come_over(folder, kuwait):
    from apps.access.models import Role, UserRole
    from apps.accounting.models import ExpenseCategory, Journal
    from apps.users.models import User

    Role.objects.create(code="salesperson", name="Showroom salesperson")
    (folder / "expense_categories.json").write_text(
        json.dumps([["0", "20020", "Utility Bills", ""]]), encoding="utf-8"
    )
    (folder / "accounts.json").write_text(
        json.dumps(
            [
                ["0", "123456", "Warba Bank", "0", "0", "", "", ""],
                ["0", "10010", "Sales Account", "0", "0", "", "", ""],
            ]
        ),
        encoding="utf-8",
    )
    (folder / "users.json").write_text(
        json.dumps(
            [
                ["0", "Yusuf Sales", "yusuf@example.com", "Salesman", "Active", ""],
                ["0", "boss", "boss@example.com", "Admin", "Active", ""],
            ]
        ),
        encoding="utf-8",
    )
    call_command("import_legacy", str(folder), "--company", "KW")
    call_command("import_legacy", str(folder), "--company", "KW")
    assert ExpenseCategory.objects.get(name="Utility Bills").account.code == "6900"
    bank = Journal.objects.get(name="Warba Bank")
    assert bank.journal_type == "bank" and bank.default_account.code == "1121"
    assert not Journal.objects.filter(name="Sales Account").exists()
    yusuf = User.objects.get(email="yusuf@example.com")
    assert (
        yusuf.username == "Yusuf_Sales" and not yusuf.is_active and not yusuf.has_usable_password()
    )
    assert UserRole.objects.get(user=yusuf).role.code == "salesperson"
    boss = User.objects.get(username="boss")
    assert (
        not boss.is_active
        and not boss.is_superuser
        and not UserRole.objects.filter(user=boss).exists()
    )


def test_opening_balances_and_tills(folder, kuwait):
    from apps.accounting.models import JournalEntry
    from apps.partners.models import Customer
    from apps.pos.models import Register
    from apps.sales.models import Invoice

    (folder / "accounts.json").write_text(
        json.dumps([["0", "123456", "Warba Bank", "100.000", "100.000", "", "", ""]]),
        encoding="utf-8",
    )
    call_command("import_legacy", str(folder), "--company", "KW")
    Customer.objects.create(company=kuwait, code="KW-WALK", name="Walk-in customer")
    call_command(
        "import_legacy", str(folder), "--company", "KW", "--opening", "--date", "2026-03-10"
    )
    call_command(
        "import_legacy", str(folder), "--company", "KW", "--opening", "--date", "2026-03-10"
    )

    invoice = Invoice.objects.get(reference="Opening balance")  # Hany owes 5.600; Sara nothing
    assert invoice.customer.name == "Hany" and invoice.total == Decimal("5.600")
    assert invoice.status == "posted"
    lines = {
        line.account.code: (line.debit, line.credit) for line in invoice.journal_entry.lines.all()
    }
    assert lines["3300"] == (0, Decimal("5.600"))  # not sales income
    bank = JournalEntry.objects.get(reference__startswith="Opening balance ")
    assert sum(line.debit for line in bank.lines.all()) == Decimal("100.000")
    assert Register.objects.count() == 2  # one per showroom, none for the store
    till = Register.objects.get(warehouse__name="Hawally 1st branch")
    assert (
        till.warehouse.name == "Hawally 1st branch"
        and till.walk_in_customer.name == "Walk-in customer"
    )
