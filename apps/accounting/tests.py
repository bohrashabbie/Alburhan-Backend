from datetime import date
from decimal import Decimal

import pytest

from apps.accounting.models import Account, AccountingPeriod, JournalEntry
from apps.accounting.services import LineSpec, PostingError, create_and_post, post_entry
from apps.accounting.setup import set_up_company
from apps.organization.models import ExchangeRate

pytestmark = pytest.mark.django_db

ENTRIES = "/api/v1/accounting/entries/"
DAY = date(2026, 3, 15)


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)
    return {a.code: a for a in Account.objects.filter(company=kuwait)}


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


def entry_payload(company, books, lines, **extra):
    from apps.accounting.models import Journal

    return {
        "company": company.id,
        "journal": Journal.objects.get(company=company, code="JV").id,
        "date": DAY.isoformat(),
        "currency": "KWD",
        "description": "Test",
        "lines": [
            {"account": books[code].id, "debit": str(dr), "credit": str(cr)}
            for code, dr, cr in lines
        ],
        **extra,
    }


# --- Setup -------------------------------------------------------------------------------


def test_setup_creates_chart_journals_settings_and_periods(kuwait, books):
    from apps.accounting.models import AccountingSettings, Journal

    assert books["1000"].is_group and not books["1110"].is_group
    assert books["1110"].parent == books["1100"]
    assert set(Journal.objects.filter(company=kuwait).values_list("code", flat=True)) == {
        "JV",
        "SI",
        "PI",
        "CS",
        "BK",
    }
    settings = AccountingSettings.objects.get(company=kuwait)
    assert settings.receivable.code == "1130" and settings.sales.code == "4100"
    assert AccountingPeriod.objects.filter(company=kuwait).count() == 12


def test_setup_is_idempotent(kuwait, books):
    assert set_up_company(kuwait, 2026) == {"accounts": 0, "journals": 0, "periods": 0}


# --- Posting rules -----------------------------------------------------------------------


def test_balanced_entry_posts_with_number(admin_api, kuwait, books):
    draft = admin_api.post(
        ENTRIES,
        entry_payload(kuwait, books, [("6200", "300.000", "0"), ("1120", "0", "300.000")]),
        format="json",
    )
    assert draft.status_code == 201, draft.json()
    assert draft.json()["status"] == "draft" and draft.json()["number"] == ""

    posted = admin_api.post(f"{ENTRIES}{draft.json()['id']}/post/")
    body = posted.json()
    assert posted.status_code == 200, body
    assert body["status"] == "posted"
    assert body["number"] == "KW-JV-2026-00001"
    assert [ln["debit_base"] for ln in body["lines"]] == ["300.000", "0.000"]


def test_unbalanced_entry_is_refused(admin_api, kuwait, books):
    draft = admin_api.post(
        ENTRIES,
        entry_payload(kuwait, books, [("6200", "300", "0"), ("1120", "0", "299")]),
        format="json",
    ).json()
    response = admin_api.post(f"{ENTRIES}{draft['id']}/post/")
    assert response.status_code == 400
    assert "must be equal" in response.json()["fields"]["lines"]


def test_heading_account_and_single_line_refused(kuwait, books):
    entry = create_draft(kuwait, books, [("1100", "10", "0"), ("1120", "0", "10")])
    with pytest.raises(PostingError, match="heading"):
        post_entry(entry)
    single = create_draft(kuwait, books, [("1110", "10", "0")])
    with pytest.raises(PostingError, match="at least two lines"):
        post_entry(single)


def test_closed_or_missing_period_blocks_posting(admin_api, kuwait, books):
    period = AccountingPeriod.objects.get(company=kuwait, name="2026-03")
    admin_api.post(f"/api/v1/accounting/periods/{period.id}/close/")
    draft = create_draft(kuwait, books, [("6200", "5", "0"), ("1110", "0", "5")])
    with pytest.raises(PostingError, match="closed"):
        post_entry(draft)

    draft.date = date(2030, 1, 1)
    draft.save()
    with pytest.raises(PostingError, match="No accounting period"):
        post_entry(draft)


def test_cannot_close_period_with_drafts(admin_api, kuwait, books):
    create_draft(kuwait, books, [("6200", "5", "0"), ("1110", "0", "5")])
    period = AccountingPeriod.objects.get(company=kuwait, name="2026-03")
    response = admin_api.post(f"/api/v1/accounting/periods/{period.id}/close/")
    assert response.status_code == 400
    assert "draft" in response.json()["message"]


def test_posted_entry_is_locked_and_reversal_cancels_it(admin_api, kuwait, books):
    draft = admin_api.post(
        ENTRIES,
        entry_payload(kuwait, books, [("6200", "100", "0"), ("1110", "0", "100")]),
        format="json",
    ).json()
    admin_api.post(f"{ENTRIES}{draft['id']}/post/")

    edit = admin_api.patch(f"{ENTRIES}{draft['id']}/", {"description": "changed"}, format="json")
    delete = admin_api.delete(f"{ENTRIES}{draft['id']}/")
    assert edit.status_code == 400 and delete.status_code == 400

    reversal = admin_api.post(f"{ENTRIES}{draft['id']}/reverse/", {"date": "2026-03-20"})
    assert reversal.status_code == 201, reversal.json()
    assert reversal.json()["reversal_of"] == draft["id"]
    again = admin_api.post(f"{ENTRIES}{draft['id']}/reverse/", {"date": "2026-03-21"})
    assert again.status_code == 400

    report = admin_api.get(
        "/api/v1/accounting/reports/trial-balance/", {"company": kuwait.id, "date_to": "2026-12-31"}
    ).json()
    assert {r["code"]: r["closing"] for r in report["rows"]} == {"1110": "0.000", "6200": "0.000"}


def test_foreign_currency_entry_converts_to_company_currency(kuwait, books):
    ExchangeRate.objects.create(
        currency_id="USD",
        to_currency_id="KWD",
        rate=Decimal("0.30712"),
        effective_date=date(2026, 1, 1),
    )
    entry = create_and_post(
        kuwait,
        "JV",
        DAY,
        [
            LineSpec(books["5200"], debit=Decimal("100.01")),
            LineSpec(books["5200"], debit=Decimal("100.01")),
            LineSpec(books["5200"], debit=Decimal("100.01")),
            LineSpec(books["1120"], credit=Decimal("300.03")),
        ],
        currency_code="USD",
    )
    lines = list(entry.lines.all())
    assert entry.exchange_rate == Decimal("0.30712")
    # 100.01 × 0.30712 = 30.715 (×3 = 92.145) vs 300.03 × 0.30712 = 92.145 → balances exactly.
    assert sum(ln.debit_base for ln in lines) == sum(ln.credit_base for ln in lines)


def test_rounding_difference_is_absorbed(kuwait, books):
    ExchangeRate.objects.create(
        currency_id="USD",
        to_currency_id="KWD",
        rate=Decimal("0.3333"),
        effective_date=date(2026, 1, 1),
    )
    entry = create_and_post(
        kuwait,
        "JV",
        DAY,
        [
            LineSpec(books["5200"], debit=Decimal("0.01")),
            LineSpec(books["5200"], debit=Decimal("0.01")),
            LineSpec(books["5200"], debit=Decimal("0.01")),
            LineSpec(books["1120"], credit=Decimal("0.03")),
        ],
        currency_code="USD",
    )
    lines = list(entry.lines.all())
    assert sum(ln.debit_base for ln in lines) == sum(ln.credit_base for ln in lines)


def test_missing_exchange_rate_is_reported(admin_api, kuwait, books):
    response = admin_api.post(
        ENTRIES,
        entry_payload(kuwait, books, [("6200", "1", "0"), ("1110", "0", "1")], currency="EUR"),
        format="json",
    )
    assert response.status_code == 400
    assert "exchange_rate" in response.json()["fields"]


def test_line_rules_on_save(admin_api, kuwait, books):
    both = admin_api.post(
        ENTRIES,
        entry_payload(kuwait, books, [("6200", "5", "5"), ("1110", "0", "5")]),
        format="json",
    )
    decimals = admin_api.post(
        ENTRIES,
        entry_payload(kuwait, books, [("6200", "1.2345", "0"), ("1110", "0", "1.2345")]),
        format="json",
    )
    assert both.status_code == decimals.status_code == 400


def test_accounts_of_another_company_are_rejected(admin_api, kuwait, uae, books):
    set_up_company(uae, 2026)
    uae_cash = Account.objects.get(company=uae, code="1110")
    payload = entry_payload(kuwait, books, [("6200", "5", "0"), ("1110", "0", "5")])
    payload["lines"][1]["account"] = uae_cash.id
    response = admin_api.post(ENTRIES, payload, format="json")
    assert response.status_code == 400


# --- Permissions -------------------------------------------------------------------------


def test_clerk_can_draft_but_not_post(api, make_role, make_user, kuwait, books):
    clerk = make_role("clerk", "accounting.entry.view", "accounting.entry.create")
    api.force_authenticate(make_user((clerk, kuwait, None)))
    draft = api.post(
        ENTRIES,
        entry_payload(kuwait, books, [("6200", "5", "0"), ("1110", "0", "5")]),
        format="json",
    )
    assert draft.status_code == 201
    assert api.post(f"{ENTRIES}{draft.json()['id']}/post/").status_code == 403


def test_reports_need_report_permission(api, make_role, make_user, kuwait, books):
    viewer = make_role("viewer", "accounting.entry.view")
    api.force_authenticate(make_user((viewer, kuwait, None)))
    response = api.get("/api/v1/accounting/reports/trial-balance/", {"company": kuwait.id})
    assert response.status_code == 403


# --- Reports -----------------------------------------------------------------------------


def test_trial_balance_and_ledger(admin_api, kuwait, books):
    create_and_post(
        kuwait,
        "JV",
        date(2026, 1, 10),
        [
            LineSpec(books["1120"], debit=Decimal("1000")),
            LineSpec(books["3100"], credit=Decimal("1000")),
        ],
    )
    create_and_post(
        kuwait,
        "JV",
        date(2026, 2, 5),
        [
            LineSpec(books["6200"], debit=Decimal("250")),
            LineSpec(books["1120"], credit=Decimal("250")),
        ],
    )
    report = admin_api.get(
        "/api/v1/accounting/reports/trial-balance/",
        {"company": kuwait.id, "date_from": "2026-02-01", "date_to": "2026-02-28"},
    ).json()
    bank = next(r for r in report["rows"] if r["code"] == "1120")
    assert (bank["opening"], bank["debit"], bank["credit"], bank["closing"]) == (
        "1000.000",
        "0.000",
        "250.000",
        "750.000",
    )
    assert report["totals"]["closing"] == "0.000"  # the books balance

    ledger = admin_api.get(
        "/api/v1/accounting/reports/general-ledger/",
        {"account": books["1120"].id, "date_from": "2026-01-01", "date_to": "2026-12-31"},
    ).json()
    assert [r["balance"] for r in ledger["rows"]] == ["1000.000", "750.000"]


def create_draft(company, books, lines):
    from apps.accounting.models import Journal, JournalLine

    entry = JournalEntry.objects.create(
        company=company,
        journal=Journal.objects.get(company=company, code="JV"),
        date=DAY,
        currency_id="KWD",
    )
    for index, (code, dr, cr) in enumerate(lines):
        JournalLine.objects.create(
            entry=entry, account=books[code], debit=Decimal(dr), credit=Decimal(cr), sequence=index
        )
    return entry


def test_money_takers_can_list_journals_of_their_company_only(
    api, make_role, make_user, kuwait, uae, showroom1
):
    from apps.accounting.setup import set_up_company

    set_up_company(kuwait, 2026)
    set_up_company(uae, 2026)
    seller = make_role("seller", "sales.receipt.create")
    api.force_authenticate(make_user((seller, kuwait, showroom1)))
    rows = api.get("/api/v1/accounting/journals/").json()["results"]
    assert rows and {row["company"] for row in rows} == {kuwait.id}
    nobody = make_role("nobody", "sales.quotation.view")
    api.force_authenticate(make_user((nobody, kuwait, showroom1)))
    assert api.get("/api/v1/accounting/journals/").json()["results"] == []
    # Changing journals still needs accounting rights.
    journal = rows[0]["id"]
    api.force_authenticate(make_user((seller, kuwait, showroom1)))
    assert (
        api.patch(
            f"/api/v1/accounting/journals/{journal}/", {"name": "x"}, format="json"
        ).status_code
        == 403
    )


# --- Expenses ----------------------------------------------------------------------------------

EXPENSES = "/api/v1/accounting/expenses/"


def expense_payload(company, **extra):
    from apps.accounting.models import ExpenseCategory, Journal

    return {
        "company": company.id,
        "date": DAY.isoformat(),
        "category": ExpenseCategory.objects.get(company=company, code="RENT").id,
        "amount": "250.000",
        "paid_from": Journal.objects.get(company=company, code="BK").id,
        "note": "March rent",
        **extra,
    }


def test_expense_posts_to_its_account_and_cancels(admin_api, kuwait, books):
    from apps.accounting.models import ExpenseCategory

    assert ExpenseCategory.objects.filter(company=kuwait).count() == 7  # defaults from setup
    made = admin_api.post(EXPENSES, expense_payload(kuwait), format="json")
    assert made.status_code == 201, made.json()
    body = made.json()
    assert body["number"] == "KW-EXP-2026-00001" and body["status"] == "posted"
    entry = JournalEntry.objects.get(pk=body["journal_entry"])
    lines = {line.account.code: (line.debit, line.credit) for line in entry.lines.all()}
    assert lines["6200"] == (Decimal("250.000"), 0)  # rent expense
    assert lines["1120"] == (0, Decimal("250.000"))  # out of the bank

    # Posted, so it can't be edited or deleted: only cancelled, which reverses the entry.
    assert admin_api.patch(f"{EXPENSES}{body['id']}/", {"amount": "1"}).status_code == 405
    assert admin_api.delete(f"{EXPENSES}{body['id']}/").status_code == 405
    cancelled = admin_api.post(
        f"{EXPENSES}{body['id']}/cancel/", {"date": DAY.isoformat()}, format="json"
    )
    assert cancelled.status_code == 200, cancelled.json()
    assert cancelled.json()["status"] == "cancelled"
    assert JournalEntry.objects.filter(reversal_of=entry).count() == 1
    again = admin_api.post(
        f"{EXPENSES}{body['id']}/cancel/", {"date": DAY.isoformat()}, format="json"
    )
    assert again.status_code == 400


def test_expense_rules(admin_api, kuwait, books):
    from apps.accounting.models import Journal

    general = Journal.objects.get(company=kuwait, code="JV").id
    bad = admin_api.post(EXPENSES, expense_payload(kuwait, paid_from=general), format="json")
    assert bad.status_code == 400 and "paid_from" in bad.json()["fields"]
    zero = admin_api.post(EXPENSES, expense_payload(kuwait, amount="0"), format="json")
    assert zero.status_code == 400 and "amount" in zero.json()["fields"]
    assert not JournalEntry.objects.exists()  # nothing half-posted

    # A category must point at a real expense account, not income or a heading.
    wrong = admin_api.post(
        "/api/v1/accounting/expense-categories/",
        {"company": kuwait.id, "code": "X", "name": "X", "account": books["4100"].id},
        format="json",
    )
    assert wrong.status_code == 400 and "account" in wrong.json()["fields"]


def test_expense_needs_permission(api, make_user, make_role, kuwait, books):
    viewer = make_user((make_role("expense_viewer", "accounting.expense.view"), kuwait, None))
    api.force_authenticate(viewer)
    assert api.get(EXPENSES, {"company": kuwait.id}).status_code == 200
    assert api.post(EXPENSES, expense_payload(kuwait), format="json").status_code == 403


# --- Cash and bank accounts, money transfers -----------------------------------------------------


def test_money_transfer_moves_cash_to_bank(admin_api, kuwait, books):
    from apps.accounting.models import Journal

    cash = Journal.objects.get(company=kuwait, code="CS")
    bank = Journal.objects.get(company=kuwait, code="BK")
    made = admin_api.post(
        "/api/v1/accounting/money-transfers/",
        {
            "company": kuwait.id,
            "date": DAY.isoformat(),
            "from_journal": cash.id,
            "to_journal": bank.id,
            "amount": "400.000",
            "note": "Cash banked",
        },
        format="json",
    )
    assert made.status_code == 201, made.json()
    assert made.json()["number"] == "KW-MT-2026-00001"
    accounts = admin_api.get("/api/v1/accounting/money-accounts/", {"company": kuwait.id}).json()
    balance = {row["code"]: row["balance"] for row in accounts}
    assert balance == {"BK": "400.000", "CS": "-400.000"}

    same = admin_api.post(
        "/api/v1/accounting/money-transfers/",
        {
            "company": kuwait.id,
            "date": DAY.isoformat(),
            "from_journal": cash.id,
            "to_journal": cash.id,
            "amount": "1",
        },
        format="json",
    )
    assert same.status_code == 400 and "to_journal" in same.json()["fields"]

    cancelled = admin_api.post(
        f"/api/v1/accounting/money-transfers/{made.json()['id']}/cancel/",
        {"date": DAY.isoformat()},
        format="json",
    )
    assert cancelled.json()["status"] == "cancelled"
    accounts = admin_api.get("/api/v1/accounting/money-accounts/", {"company": kuwait.id}).json()
    assert {row["balance"] for row in accounts} == {"0.000"}


def test_expense_receipt_can_be_attached(admin_api, kuwait, books):
    from django.core.files.uploadedfile import SimpleUploadedFile

    expense = admin_api.post(EXPENSES, expense_payload(kuwait), format="json").json()
    assert expense["attachment_url"] is None
    receipt = SimpleUploadedFile("receipt.pdf", b"%PDF-1.4 test", content_type="application/pdf")
    done = admin_api.post(
        f"{EXPENSES}{expense['id']}/attach/", {"file": receipt}, format="multipart"
    )
    assert done.status_code == 200, done.json()
    assert done.json()["attachment_url"].endswith(".pdf")
    script = SimpleUploadedFile("run.exe", b"MZ", content_type="application/octet-stream")
    assert (
        admin_api.post(
            f"{EXPENSES}{expense['id']}/attach/", {"file": script}, format="multipart"
        ).status_code
        == 400
    )
