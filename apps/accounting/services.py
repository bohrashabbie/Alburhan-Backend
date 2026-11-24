"""Posting engine and ledger reports. Other modules post through `create_and_post`."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.core.fields import round_money
from apps.organization.models import Company
from apps.organization.services import (
    ExchangeRateMissing,
    currency_decimals,
    get_exchange_rate,
    next_document_number,
)

from .models import Account, AccountingPeriod, Journal, JournalEntry, JournalLine

ZERO = Decimal("0")


class PostingError(Exception):
    """A business rule stopped a posting. `field` points the UI at the cause when known."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


# --- Periods and rates ---------------------------------------------------------------------


def period_for(company: Company, on_date: date) -> AccountingPeriod | None:
    return AccountingPeriod.objects.filter(
        company=company, start_date__lte=on_date, end_date__gte=on_date
    ).first()


def assert_period_open(company: Company, on_date: date) -> None:
    period = period_for(company, on_date)
    if period is None:
        raise PostingError(f"No accounting period covers {on_date:%d %b %Y}.", "date")
    if period.status == AccountingPeriod.Status.CLOSED:
        raise PostingError(f"Period {period.name} is closed.", "date")


def default_rate(company: Company, currency_code: str, on_date: date) -> Decimal:
    if currency_code == company.currency_id:
        return Decimal(1)
    try:
        return get_exchange_rate(currency_code, company.currency_id, on_date)
    except ExchangeRateMissing as exc:
        raise PostingError(str(exc), "exchange_rate") from exc


# --- Posting -------------------------------------------------------------------------------


def _check_lines(entry: JournalEntry, lines: list[JournalLine]) -> None:
    if len(lines) < 2:
        raise PostingError("An entry needs at least two lines.", "lines")
    decimals = currency_decimals(entry.currency_id)
    quantum = Decimal(1).scaleb(-decimals)
    for number, line in enumerate(lines, start=1):
        where = f"Line {number}"
        account = line.account
        if account.company_id != entry.company_id:
            raise PostingError(
                f"{where}: account {account.code} belongs to another company.", "lines"
            )
        if account.is_group:
            raise PostingError(
                f"{where}: {account.code} is a heading; choose a detail account.", "lines"
            )
        if not account.is_active:
            raise PostingError(f"{where}: account {account.code} is inactive.", "lines")
        if (line.debit > 0) == (line.credit > 0):
            raise PostingError(f"{where}: enter either a debit or a credit.", "lines")
        for amount in (line.debit, line.credit):
            if amount != amount.quantize(quantum):
                raise PostingError(
                    f"{where}: {entry.currency_id} allows {decimals} decimal places.", "lines"
                )
        for partner in (line.customer, line.supplier):
            if partner is not None and partner.company_id != entry.company_id:
                raise PostingError(
                    f"{where}: the customer/supplier belongs to another company.", "lines"
                )


def _fill_base_amounts(entry: JournalEntry, lines: list[JournalLine]) -> None:
    """Convert to company currency. A rounding difference of at most one minor unit per line is
    absorbed by the largest line on the smaller side, so the base amounts balance exactly."""
    decimals = currency_decimals(entry.company.currency_id)
    for line in lines:
        line.debit_base = round_money(line.debit * entry.exchange_rate, decimals)
        line.credit_base = round_money(line.credit * entry.exchange_rate, decimals)
    difference = sum(ln.debit_base for ln in lines) - sum(ln.credit_base for ln in lines)
    if difference == 0:
        return
    if abs(difference) > Decimal(1).scaleb(-decimals) * len(lines):
        raise PostingError("Amounts don't balance in the company currency.", "lines")
    if difference > 0:  # debits too high: raise the biggest credit
        biggest = max((ln for ln in lines if ln.credit > 0), key=lambda ln: ln.credit_base)
        biggest.credit_base += difference
    else:
        biggest = max((ln for ln in lines if ln.debit > 0), key=lambda ln: ln.debit_base)
        biggest.debit_base -= difference


@transaction.atomic
def post_entry(entry: JournalEntry, user=None) -> JournalEntry:
    entry = (
        JournalEntry.objects.select_for_update()
        .select_related("company", "journal")
        .get(pk=entry.pk)
    )
    if entry.status != JournalEntry.Status.DRAFT:
        raise PostingError("Only draft entries can be posted.")
    if not entry.journal.is_active:
        raise PostingError("This journal is inactive.", "journal")
    if entry.currency_id == entry.company.currency_id and entry.exchange_rate != 1:
        raise PostingError("Entries in the company currency use a rate of 1.", "exchange_rate")
    assert_period_open(entry.company, entry.date)

    lines = list(entry.lines.select_related("account", "customer", "supplier"))
    _check_lines(entry, lines)
    total_debit = sum((ln.debit for ln in lines), ZERO)
    total_credit = sum((ln.credit for ln in lines), ZERO)
    if total_debit != total_credit:
        raise PostingError(
            f"Debits ({total_debit}) and credits ({total_credit}) must be equal.", "lines"
        )
    _fill_base_amounts(entry, lines)
    JournalLine.objects.bulk_update(lines, ["debit_base", "credit_base"])

    entry.number = next_document_number(entry.company, entry.journal.code, entry.date)
    entry.status = JournalEntry.Status.POSTED
    entry.posted_at = timezone.now()
    entry.posted_by = user
    entry.save(update_fields=["number", "status", "posted_at", "posted_by", "updated_at"])
    return entry


@transaction.atomic
def reverse_entry(
    entry: JournalEntry, on_date: date, user=None, description: str = ""
) -> JournalEntry:
    """Posts a mirror entry (debits ↔ credits) on `on_date`, cancelling the original's effect."""
    entry = JournalEntry.objects.select_for_update().get(pk=entry.pk)
    if entry.status != JournalEntry.Status.POSTED:
        raise PostingError("Only posted entries can be reversed.")
    if JournalEntry.objects.filter(reversal_of=entry).exists():
        raise PostingError("This entry has already been reversed.")
    if entry.reversal_of_id:
        raise PostingError("A reversal can't be reversed; post a new entry instead.")
    reversal = JournalEntry.objects.create(
        company=entry.company,
        journal=entry.journal,
        date=on_date,
        reference=entry.number,
        description=description or f"Reversal of {entry.number}",
        currency=entry.currency,
        exchange_rate=entry.exchange_rate,
        reversal_of=entry,
        source_type=entry.source_type,
        source_id=entry.source_id,
        created_by=user,
        updated_by=user,
    )
    JournalLine.objects.bulk_create(
        [
            JournalLine(
                entry=reversal,
                account=line.account,
                description=line.description,
                debit=line.credit,
                credit=line.debit,
                customer=line.customer,
                supplier=line.supplier,
                branch=line.branch,
                sequence=line.sequence,
            )
            for line in entry.lines.all()
        ]
    )
    return post_entry(reversal, user)


@dataclass
class LineSpec:
    account: Account
    debit: Decimal = ZERO
    credit: Decimal = ZERO
    description: str = ""
    customer: object = None
    supplier: object = None
    branch: object = None


@transaction.atomic
def create_and_post(
    company: Company,
    journal_code: str,
    on_date: date,
    lines: list[LineSpec],
    *,
    description: str = "",
    reference: str = "",
    currency_code: str | None = None,
    exchange_rate: Decimal | None = None,
    source_type: str = "",
    source_id: int | None = None,
    user=None,
) -> JournalEntry:
    """For other modules: build an entry from `lines` and post it in one step."""
    journal = Journal.objects.get(company=company, code=journal_code)
    currency_code = currency_code or company.currency_id
    entry = JournalEntry.objects.create(
        company=company,
        journal=journal,
        date=on_date,
        description=description,
        reference=reference,
        currency_id=currency_code,
        exchange_rate=exchange_rate or default_rate(company, currency_code, on_date),
        source_type=source_type,
        source_id=source_id,
        created_by=user,
        updated_by=user,
    )
    JournalLine.objects.bulk_create(
        [
            JournalLine(
                entry=entry,
                account=spec.account,
                debit=spec.debit,
                credit=spec.credit,
                description=spec.description,
                customer=spec.customer,
                supplier=spec.supplier,
                branch=spec.branch,
                sequence=index,
            )
            for index, spec in enumerate(lines)
        ]
    )
    return post_entry(entry, user)


# --- Reports -------------------------------------------------------------------------------


def _formatter(company: Company):
    """Money as exact strings in the company currency's minor units (e.g. "750.000" for KWD)."""
    decimals = currency_decimals(company.currency_id)
    return lambda value: str(round_money(value, decimals))


def _posted_lines(company: Company):
    return JournalLine.objects.filter(
        entry__company=company, entry__status=JournalEntry.Status.POSTED
    )


def trial_balance(company: Company, date_from: date | None, date_to: date) -> dict:
    """Per account: opening balance, debits and credits in the range, closing balance — all in
    the company currency. Balances are debit-positive (credit balances are negative)."""
    lines = _posted_lines(company).filter(entry__date__lte=date_to)
    movement = Q(entry__date__gte=date_from) if date_from else Q()
    opening = ~movement if date_from else Q(pk__in=[])
    rows = lines.values("account_id").annotate(
        open_dr=Sum("debit_base", filter=opening, default=ZERO),
        open_cr=Sum("credit_base", filter=opening, default=ZERO),
        period_dr=Sum("debit_base", filter=movement, default=ZERO),
        period_cr=Sum("credit_base", filter=movement, default=ZERO),
    )
    accounts = Account.objects.in_bulk([row["account_id"] for row in rows])
    result = []
    for row in rows:
        account = accounts[row["account_id"]]
        opening_balance = row["open_dr"] - row["open_cr"]
        closing = opening_balance + row["period_dr"] - row["period_cr"]
        result.append(
            {
                "account": account.pk,
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar,
                "account_type": account.account_type,
                "opening": opening_balance,
                "debit": row["period_dr"],
                "credit": row["period_cr"],
                "closing": closing,
            }
        )
    result.sort(key=lambda r: r["code"])
    amounts = ("opening", "debit", "credit", "closing")
    totals = {key: sum((r[key] for r in result), ZERO) for key in amounts}
    money = _formatter(company)
    for row in result:
        row.update({key: money(row[key]) for key in amounts})
    return {
        "currency": company.currency_id,
        "rows": result,
        "totals": {key: money(value) for key, value in totals.items()},
    }


def general_ledger(account: Account, date_from: date, date_to: date) -> dict:
    lines = _posted_lines(account.company).filter(account=account)
    before = lines.filter(entry__date__lt=date_from).aggregate(
        dr=Sum("debit_base", default=ZERO), cr=Sum("credit_base", default=ZERO)
    )
    balance = before["dr"] - before["cr"]
    opening = balance
    rows = []
    for line in (
        lines.filter(entry__date__gte=date_from, entry__date__lte=date_to)
        .select_related("entry", "customer", "supplier")
        .order_by("entry__date", "entry__id", "sequence")
    ):
        balance += line.debit_base - line.credit_base
        rows.append(
            {
                "entry": line.entry_id,
                "number": line.entry.number,
                "date": line.entry.date,
                "description": line.description or line.entry.description,
                "partner": (line.customer or line.supplier).name
                if (line.customer or line.supplier)
                else "",
                "debit": line.debit_base,
                "credit": line.credit_base,
                "balance": balance,
            }
        )
    money = _formatter(account.company)
    for row in rows:
        row.update({key: money(row[key]) for key in ("debit", "credit", "balance")})
    return {
        "account": account.pk,
        "code": account.code,
        "name": account.name,
        "currency": account.company.currency_id,
        "opening": money(opening),
        "closing": money(balance),
        "rows": rows,
    }


# --- Expenses ------------------------------------------------------------------------------


@transaction.atomic
def record_expense(expense, user=None):
    """Numbers the expense and posts it: the category's expense account against the cash or
    bank account it was paid from."""
    from apps.organization.services import next_document_number

    company = expense.company
    paid_from = expense.paid_from
    if paid_from.default_account_id is None:
        raise PostingError(
            "This journal has no cash or bank account. Set one under Accounting → Journals.",
            "paid_from",
        )
    if expense.amount <= ZERO:
        raise PostingError("Enter an amount above zero.", "amount")
    expense.number = next_document_number(company, "EXP", expense.date)
    description = f"{expense.category.name}: {expense.note}".strip(": ")[:255]
    expense.journal_entry = create_and_post(
        company,
        paid_from.code,
        expense.date,
        [
            LineSpec(
                expense.category.account,
                debit=expense.amount,
                description=description,
                branch=expense.branch,
            ),
            LineSpec(paid_from.default_account, credit=expense.amount, description=description),
        ],
        description=f"Expense {expense.number}",
        reference=expense.reference or expense.number,
        source_type="accounting.expense",
        source_id=expense.pk,
        user=user,
    )
    expense.updated_by = user
    expense.save()
    return expense


@transaction.atomic
def cancel_expense(expense, on_date: date, user=None):
    """Reverses the expense's entry and marks it cancelled."""
    from .models import Expense

    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.status != Expense.Status.POSTED:
        raise PostingError("This expense is already cancelled.")
    reverse_entry(expense.journal_entry, on_date, user, f"Cancelled expense {expense.number}")
    expense.status = Expense.Status.CANCELLED
    expense.updated_by = user
    expense.save(update_fields=["status", "updated_by", "updated_at"])
    return expense


# --- Cash and bank accounts ------------------------------------------------------------------


def money_accounts(company: Company) -> list[dict]:
    """Every cash and bank journal with the balance of its account."""
    journals = list(
        Journal.objects.filter(company=company, journal_type__in=("cash", "bank"))
        .select_related("default_account")
        .order_by("journal_type", "code")
    )
    balances = {
        row["account_id"]: row["balance"]
        for row in _posted_lines(company)
        .filter(account_id__in=[j.default_account_id for j in journals if j.default_account_id])
        .values("account_id")
        .annotate(balance=Sum("debit_base", default=ZERO) - Sum("credit_base", default=ZERO))
    }
    money = _formatter(company)
    return [
        {
            "journal": journal.id,
            "code": journal.code,
            "name": journal.name,
            "name_ar": journal.name_ar,
            "journal_type": journal.journal_type,
            "is_active": journal.is_active,
            "account": journal.default_account_id,
            "account_code": journal.default_account.code if journal.default_account else None,
            "balance": money(balances.get(journal.default_account_id, ZERO)),
        }
        for journal in journals
    ]


@transaction.atomic
def record_money_transfer(transfer, user=None):
    from apps.organization.services import next_document_number

    source, target = transfer.from_journal, transfer.to_journal
    if source.pk == target.pk:
        raise PostingError("Choose two different accounts.", "to_journal")
    for journal, field in ((source, "from_journal"), (target, "to_journal")):
        if journal.journal_type not in ("cash", "bank") or journal.default_account_id is None:
            raise PostingError("Choose a cash or bank account.", field)
    if transfer.amount <= ZERO:
        raise PostingError("Enter an amount above zero.", "amount")
    transfer.number = next_document_number(transfer.company, "MT", transfer.date)
    description = f"Transfer {transfer.number}: {source.name} → {target.name}"
    transfer.journal_entry = create_and_post(
        transfer.company,
        target.code,
        transfer.date,
        [
            LineSpec(target.default_account, debit=transfer.amount, description=description),
            LineSpec(source.default_account, credit=transfer.amount, description=description),
        ],
        description=description,
        reference=transfer.number,
        source_type="accounting.moneytransfer",
        source_id=transfer.pk,
        user=user,
    )
    transfer.updated_by = user
    transfer.save()
    return transfer


@transaction.atomic
def cancel_money_transfer(transfer, on_date: date, user=None):
    from .models import MoneyTransfer

    transfer = MoneyTransfer.objects.select_for_update().get(pk=transfer.pk)
    if transfer.status != MoneyTransfer.Status.POSTED:
        raise PostingError("This transfer is already cancelled.")
    reverse_entry(transfer.journal_entry, on_date, user, f"Cancelled transfer {transfer.number}")
    transfer.status = MoneyTransfer.Status.CANCELLED
    transfer.updated_by = user
    transfer.save(update_fields=["status", "updated_by", "updated_at"])
    return transfer
