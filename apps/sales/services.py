"""Posting rules for showroom sales. Every write runs inside a transaction and either fully
succeeds (stock, books, statuses) or leaves nothing behind."""

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.accounting.models import AccountingSettings
from apps.accounting.services import LineSpec, PostingError, create_and_post, default_rate
from apps.catalog.models import Product
from apps.core.fields import round_money
from apps.inventory.models import StockMove, StockStatus
from apps.inventory.services import StockError, available_quantity, move, reserved_quantity
from apps.organization.services import currency_decimals, next_document_number

from .models import (
    CreditNote,
    DeliveryNote,
    Invoice,
    InvoiceLine,
    Quotation,
    Receipt,
    SalesSettings,
)

ZERO = Decimal("0")


class SalesError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _settings(company) -> SalesSettings:
    settings, _ = SalesSettings.objects.get_or_create(company=company)
    return settings


def _accounts(company) -> AccountingSettings:
    accounts = (
        AccountingSettings.objects.filter(company=company)
        .select_related(
            "receivable",
            "sales",
            "sales_returns",
            "vat_output",
            "inventory",
            "cost_of_sales",
            "customer_advances",
        )
        .first()
    )
    if accounts is None or accounts.receivable is None or accounts.sales is None:
        raise SalesError("Set up accounting for this company first (Accounts → Chart of accounts).")
    return accounts


def _post(
    company, journal_code, on_date, lines, description, reference, source, currency, rate, user
):
    try:
        return create_and_post(
            company,
            journal_code,
            on_date,
            lines,
            description=description,
            reference=reference,
            currency_code=currency,
            exchange_rate=rate,
            source_type=source._meta.label_lower,
            source_id=source.pk,
            user=user,
        )
    except PostingError as exc:
        raise SalesError(exc.message, "date" if exc.field == "date" else None) from exc


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def recompute(document, lines) -> None:
    """Recalculate every line and the header totals in the document currency."""
    decimals = currency_decimals(document.currency_id)
    for line in lines:
        line.compute(decimals)
    document.recompute_totals(lines)


# --- Quotations ---------------------------------------------------------------------------------


@transaction.atomic
def send_quotation(quotation: Quotation, user=None) -> Quotation:
    quotation = Quotation.objects.select_for_update().get(pk=quotation.pk)
    if quotation.status != Quotation.Status.DRAFT:
        raise SalesError("Only draft quotations can be sent.")
    if not quotation.lines.exists():
        raise SalesError("Add at least one line.", "lines")
    if not quotation.number:
        quotation.number = next_document_number(quotation.company, "QT", quotation.date)
    if quotation.valid_until is None:
        quotation.valid_until = quotation.date + timedelta(
            days=_settings(quotation.company).quotation_valid_days
        )
    quotation.status = Quotation.Status.SENT
    quotation.sent_at = timezone.now()
    quotation.updated_by = user
    quotation.save()
    return quotation


@transaction.atomic
def convert_quotation(quotation: Quotation, user=None) -> Invoice:
    """Make a draft invoice with the same lines. The quotation is marked Invoiced once posted."""
    quotation = (
        Quotation.objects.select_for_update()
        .select_related("company", "customer")
        .get(pk=quotation.pk)
    )
    if quotation.status not in (
        Quotation.Status.DRAFT,
        Quotation.Status.SENT,
        Quotation.Status.ACCEPTED,
    ):
        raise SalesError("This quotation has already been invoiced or closed.")
    if quotation.invoice_id:
        raise SalesError("An invoice already exists for this quotation.")
    lines = list(quotation.lines.all())
    if not lines:
        raise SalesError("Add at least one line before invoicing.", "lines")
    if not quotation.number:
        quotation.number = next_document_number(quotation.company, "QT", quotation.date)
    terms = (
        quotation.customer.payment_terms_days
        or _settings(quotation.company).default_payment_terms_days
    )
    today = timezone.localdate()
    invoice = Invoice.objects.create(
        company=quotation.company,
        date=today,
        due_date=today + timedelta(days=terms),
        branch=quotation.branch,
        customer=quotation.customer,
        salesperson=quotation.salesperson,
        currency=quotation.currency,
        exchange_rate=quotation.exchange_rate,
        reference=quotation.reference,
        notes=quotation.notes,
        subtotal=quotation.subtotal,
        tax_total=quotation.tax_total,
        total=quotation.total,
        created_by=user,
        updated_by=user,
    )
    InvoiceLine.objects.bulk_create(
        [
            InvoiceLine(
                invoice=invoice,
                product=line.product,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                discount_percent=line.discount_percent,
                tax_rate=line.tax_rate,
                net_amount=line.net_amount,
                tax_amount=line.tax_amount,
                total_amount=line.total_amount,
                sequence=line.sequence,
            )
            for line in lines
        ]
    )
    quotation.invoice = invoice
    quotation.status = Quotation.Status.ACCEPTED
    quotation.updated_by = user
    quotation.save()
    return invoice


# --- Invoices -----------------------------------------------------------------------------------


def _refresh_invoice_status(invoice: Invoice) -> None:
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
        return
    due = invoice.amount_due
    if due <= 0:
        invoice.status = Invoice.Status.PAID
    elif invoice.amount_paid > 0 or invoice.amount_credited > 0:
        invoice.status = Invoice.Status.PARTIALLY_PAID
    else:
        invoice.status = Invoice.Status.POSTED


@transaction.atomic
def post_invoice(invoice: Invoice, user=None) -> Invoice:
    invoice = (
        Invoice.objects.select_for_update()
        .select_related("company", "customer", "currency")
        .get(pk=invoice.pk)
    )
    if invoice.status != Invoice.Status.DRAFT:
        raise SalesError("Only draft invoices can be posted.")
    lines = list(invoice.lines.select_related("product", "product__income_account"))
    if not lines:
        raise SalesError("Add at least one line.", "lines")
    if not invoice.customer.is_active:
        raise SalesError("This customer is inactive.", "customer")
    if invoice.customer.credit_limit > 0:
        outstanding = Invoice.objects.filter(
            customer=invoice.customer,
            status__in=(Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID),
        ).aggregate(
            total=Sum("total", default=ZERO),
            paid=Sum("amount_paid", default=ZERO),
            credited=Sum("amount_credited", default=ZERO),
        )
        owed = outstanding["total"] - outstanding["paid"] - outstanding["credited"]
        if owed + invoice.total > invoice.customer.credit_limit:
            raise SalesError(
                f"Credit limit exceeded: {invoice.customer.name} owes {owed} and the limit is "
                f"{invoice.customer.credit_limit}. Take a payment first or raise the limit.",
                "customer",
            )
    recompute(invoice, lines)
    for line in lines:
        line.save(update_fields=["net_amount", "tax_amount", "total_amount"])
    if invoice.total <= 0:
        raise SalesError("The invoice total must be greater than zero.", "lines")

    accounts = _accounts(invoice.company)
    invoice.number = next_document_number(invoice.company, "INV", invoice.date)
    if invoice.due_date is None:
        terms = (
            invoice.customer.payment_terms_days
            or _settings(invoice.company).default_payment_terms_days
        )
        invoice.due_date = invoice.date + timedelta(days=terms)
    journal_lines = [
        LineSpec(
            accounts.receivable,
            debit=invoice.total,
            customer=invoice.customer,
            branch=invoice.branch,
            description=invoice.number,
        ),
    ]
    # Income per account: a product may carry its own (e.g. installation income).
    income: dict[int, tuple] = {}
    for line in lines:
        account = line.product.income_account
        if account is None or account.company_id != invoice.company_id:
            account = accounts.sales  # a product's own account only applies in its company
        total = income.get(account.pk, (account, ZERO))[1] + line.net_amount
        income[account.pk] = (account, total)
    for account, amount in income.values():
        journal_lines.append(
            LineSpec(account, credit=amount, branch=invoice.branch, description=invoice.number)
        )
    if invoice.tax_total > 0:
        if accounts.vat_output is None:
            raise SalesError("Choose the VAT on sales account under Accounts → Default accounts.")
        journal_lines.append(
            LineSpec(accounts.vat_output, credit=invoice.tax_total, description=invoice.number)
        )
    invoice.journal_entry = _post(
        invoice.company,
        "SI",
        invoice.date,
        journal_lines,
        f"Invoice {invoice.number} · {invoice.customer.name}",
        invoice.number,
        invoice,
        invoice.currency_id,
        invoice.exchange_rate,
        user,
    )
    invoice.status = Invoice.Status.POSTED
    invoice.posted_at = timezone.now()
    invoice.updated_by = user
    invoice.save()
    Quotation.objects.filter(invoice=invoice).update(status=Quotation.Status.INVOICED)
    return invoice


@transaction.atomic
def cancel_invoice(invoice: Invoice, on_date: date, user=None) -> Invoice:
    """Cancel an unpaid, undelivered posted invoice by reversing its journal entry."""
    from apps.accounting.services import reverse_entry

    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.Status.POSTED:
        raise SalesError(
            "Only posted, unpaid invoices can be cancelled. Use a credit note otherwise."
        )
    if invoice.deliveries.filter(status=DeliveryNote.Status.POSTED).exists():
        raise SalesError("Goods have been delivered on this invoice; use a credit note instead.")
    if invoice.allocations.filter(receipt__status=Receipt.Status.POSTED).exists():
        raise SalesError("Payments are allocated to this invoice; use a credit note instead.")
    try:
        reverse_entry(invoice.journal_entry, on_date, user, f"Cancel invoice {invoice.number}")
    except PostingError as exc:
        raise SalesError(exc.message, "date" if exc.field == "date" else None) from exc
    invoice.status = Invoice.Status.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.updated_by = user
    invoice.save()
    Quotation.objects.filter(invoice=invoice).update(status=Quotation.Status.ACCEPTED, invoice=None)
    return invoice


# --- Deliveries ---------------------------------------------------------------------------------


@transaction.atomic
def post_delivery(delivery: DeliveryNote, user=None) -> DeliveryNote:
    delivery = (
        DeliveryNote.objects.select_for_update()
        .select_related("company", "invoice", "warehouse")
        .get(pk=delivery.pk)
    )
    if delivery.status != DeliveryNote.Status.DRAFT:
        raise SalesError("This delivery note has already been posted.")
    invoice = Invoice.objects.select_for_update().get(pk=delivery.invoice_id)
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
        raise SalesError("Post the invoice before delivering.", "invoice")
    lines = list(
        delivery.lines.select_related(
            "invoice_line", "invoice_line__product", "invoice_line__product__uom"
        )
    )
    if not lines:
        raise SalesError("Add at least one line.", "lines")
    accounts = _accounts(delivery.company)
    delivery.number = next_document_number(delivery.company, "DN", delivery.date)
    cost_total = ZERO
    for line in lines:
        invoice_line = InvoiceLine.objects.select_for_update().get(pk=line.invoice_line_id)
        remaining = invoice_line.quantity - invoice_line.delivered_quantity
        if line.quantity <= 0 or line.quantity > remaining:
            raise SalesError(
                f"{invoice_line.product.sku}: deliver between 0 and {_qty(remaining)}.", "lines"
            )
        product = invoice_line.product
        if product.product_type == Product.ProductType.STOCK:
            free = available_quantity(delivery.warehouse, product) - reserved_quantity(
                delivery.warehouse, product
            )
            if line.quantity > free:
                raise SalesError(
                    f"Only {_qty(max(free, ZERO))} {product.sku} free in {delivery.warehouse.name}.",
                    "lines",
                )
            try:
                stock_move = move(
                    delivery.company,
                    delivery.warehouse,
                    product,
                    StockStatus.AVAILABLE,
                    -line.quantity,
                    on_date=delivery.date,
                    move_type=StockMove.MoveType.ISSUE,
                    affects_cost=True,
                    reference=delivery.number,
                    source=delivery,
                    user=user,
                )
            except StockError as exc:
                raise SalesError(exc.message, exc.field) from exc
            cost_total += -stock_move.value
        invoice_line.delivered_quantity += line.quantity
        invoice_line.save(update_fields=["delivered_quantity"])

    if cost_total > 0:
        if accounts.cost_of_sales is None or accounts.inventory is None:
            raise SalesError(
                "Choose the inventory and cost of goods sold accounts under Accounts → Default accounts."
            )
        delivery.journal_entry = _post(
            delivery.company,
            "JV",
            delivery.date,
            [
                LineSpec(
                    accounts.cost_of_sales,
                    debit=cost_total,
                    branch=invoice.branch,
                    description=delivery.number,
                ),
                LineSpec(accounts.inventory, credit=cost_total, description=delivery.number),
            ],
            f"Delivery {delivery.number} · invoice {invoice.number}",
            delivery.number,
            delivery,
            None,
            None,
            user,
        )
    delivery.status = DeliveryNote.Status.POSTED
    delivery.posted_at = timezone.now()
    delivery.updated_by = user
    delivery.save()
    return delivery


def delivery_status(invoice: Invoice) -> str:
    lines = list(invoice.lines.all())
    stock_lines = [ln for ln in lines if ln.product.product_type == Product.ProductType.STOCK]
    if not stock_lines:
        return "not_required"
    delivered = sum(ln.delivered_quantity for ln in stock_lines)
    ordered = sum(ln.quantity for ln in stock_lines)
    if delivered <= 0:
        return "pending"
    return "delivered" if delivered >= ordered else "partial"


# --- Receipts -----------------------------------------------------------------------------------


@transaction.atomic
def post_receipt(receipt: Receipt, user=None) -> Receipt:
    receipt = (
        Receipt.objects.select_for_update(of=("self",))
        .select_related("company", "customer", "journal", "journal__default_account")
        .get(pk=receipt.pk)
    )
    if receipt.status != Receipt.Status.DRAFT:
        raise SalesError("Only draft receipts can be posted.")
    if receipt.amount <= 0:
        raise SalesError("The amount must be greater than zero.", "amount")
    if receipt.journal.default_account is None:
        raise SalesError("The chosen journal has no cash or bank account.", "journal")
    accounts = _accounts(receipt.company)
    allocations = list(receipt.allocations.select_related("invoice"))
    allocated = sum((a.amount for a in allocations), ZERO)
    if allocated > receipt.amount:
        raise SalesError("Allocations exceed the amount received.", "allocations")
    unallocated = receipt.amount - allocated
    if unallocated > 0 and accounts.customer_advances is None:
        raise SalesError(
            "Choose the customer advances account to hold unallocated money.", "allocations"
        )

    receipt.number = next_document_number(receipt.company, "RCT", receipt.date)
    journal_lines = [
        LineSpec(
            receipt.journal.default_account,
            debit=receipt.amount,
            branch=receipt.branch,
            description=receipt.number,
        )
    ]
    for allocation in allocations:
        invoice = Invoice.objects.select_for_update().get(pk=allocation.invoice_id)
        if invoice.customer_id != receipt.customer_id:
            raise SalesError(
                f"Invoice {invoice.number} belongs to another customer.", "allocations"
            )
        if invoice.status not in (Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID):
            raise SalesError(f"Invoice {invoice.number} is not open for payment.", "allocations")
        if invoice.currency_id != receipt.currency_id:
            raise SalesError(
                f"Invoice {invoice.number} is in {invoice.currency_id}; receive in the same currency.",
                "allocations",
            )
        if allocation.amount <= 0 or allocation.amount > invoice.amount_due:
            raise SalesError(
                f"Invoice {invoice.number}: allocate between 0 and {invoice.amount_due}.",
                "allocations",
            )
        invoice.amount_paid += allocation.amount
        _refresh_invoice_status(invoice)
        invoice.updated_by = user
        invoice.save()
        journal_lines.append(
            LineSpec(
                accounts.receivable,
                credit=allocation.amount,
                customer=receipt.customer,
                branch=receipt.branch,
                description=f"{receipt.number} → {invoice.number}",
            )
        )
    if unallocated > 0:
        journal_lines.append(
            LineSpec(
                accounts.customer_advances,
                credit=unallocated,
                customer=receipt.customer,
                description=f"{receipt.number} advance",
            )
        )
    receipt.allocated_amount = allocated
    receipt.journal_entry = _post(
        receipt.company,
        receipt.journal.code,
        receipt.date,
        journal_lines,
        f"Receipt {receipt.number} · {receipt.customer.name}",
        receipt.reference or receipt.number,
        receipt,
        receipt.currency_id,
        receipt.exchange_rate,
        user,
    )
    receipt.status = Receipt.Status.POSTED
    receipt.posted_at = timezone.now()
    receipt.updated_by = user
    receipt.save()
    return receipt


# --- Credit notes -------------------------------------------------------------------------------


@transaction.atomic
def post_credit_note(note: CreditNote, user=None) -> CreditNote:
    note = (
        CreditNote.objects.select_for_update(of=("self",))
        .select_related("company", "customer", "warehouse", "currency")
        .get(pk=note.pk)
    )
    if note.status != CreditNote.Status.DRAFT:
        raise SalesError("This credit note has already been posted.")
    invoice = Invoice.objects.select_for_update().get(pk=note.invoice_id)
    if invoice.status in (Invoice.Status.DRAFT, Invoice.Status.CANCELLED):
        raise SalesError("Credit notes need a posted invoice.", "invoice")
    lines = list(note.lines.select_related("invoice_line", "product", "product__uom"))
    if not lines:
        raise SalesError("Add at least one line.", "lines")
    accounts = _accounts(note.company)
    recompute(note, lines)
    note.number = next_document_number(note.company, "CN", note.date)
    stock_value = ZERO
    for line in lines:
        invoice_line = InvoiceLine.objects.select_for_update().get(pk=line.invoice_line_id)
        if invoice_line.invoice_id != invoice.pk:
            raise SalesError("A line belongs to another invoice.", "lines")
        creditable = invoice_line.quantity - invoice_line.returned_quantity
        if line.quantity <= 0 or line.quantity > creditable:
            raise SalesError(
                f"{invoice_line.product.sku}: credit between 0 and {_qty(creditable)}.", "lines"
            )
        if line.restock and invoice_line.product.product_type == Product.ProductType.STOCK:
            if line.quantity > invoice_line.delivered_quantity - invoice_line.returned_quantity:
                raise SalesError(
                    f"{invoice_line.product.sku}: only delivered goods can be returned to stock.",
                    "lines",
                )
            if note.warehouse is None:
                raise SalesError("Choose the warehouse the goods come back to.", "warehouse")
            try:
                stock_move = move(
                    note.company,
                    note.warehouse,
                    invoice_line.product,
                    StockStatus.RETURNED,
                    line.quantity,
                    on_date=note.date,
                    move_type=StockMove.MoveType.RECEIPT,
                    affects_cost=True,
                    reference=note.number,
                    source=note,
                    user=user,
                )
            except StockError as exc:
                raise SalesError(exc.message, exc.field) from exc
            stock_value += stock_move.value
        invoice_line.returned_quantity += line.quantity
        invoice_line.save(update_fields=["returned_quantity"])
        line.save(update_fields=["net_amount", "tax_amount", "total_amount"])
    if note.total > invoice.amount_due + invoice.amount_paid:
        raise SalesError("The credit note is larger than the invoice.", "lines")

    returns_account = accounts.sales_returns or accounts.sales
    journal_lines = [
        LineSpec(returns_account, debit=note.subtotal, branch=note.branch, description=note.number),
        LineSpec(
            accounts.receivable,
            credit=note.total,
            customer=note.customer,
            branch=note.branch,
            description=note.number,
        ),
    ]
    if note.tax_total > 0:
        journal_lines.insert(
            1, LineSpec(accounts.vat_output, debit=note.tax_total, description=note.number)
        )
    if stock_value > 0:
        journal_lines += [
            LineSpec(
                accounts.inventory, debit=stock_value, description=f"{note.number} returned goods"
            ),
            LineSpec(
                accounts.cost_of_sales,
                credit=stock_value,
                branch=note.branch,
                description=f"{note.number} returned goods",
            ),
        ]
    note.journal_entry = _post(
        note.company,
        "SI",
        note.date,
        journal_lines,
        f"Credit note {note.number} · invoice {invoice.number}",
        note.number,
        note,
        note.currency_id,
        note.exchange_rate,
        user,
    )
    invoice.amount_credited += note.total
    _refresh_invoice_status(invoice)
    invoice.updated_by = user
    invoice.save()
    note.status = CreditNote.Status.POSTED
    note.posted_at = timezone.now()
    note.updated_by = user
    note.save()
    return note


# --- Helpers for the API -------------------------------------------------------------------------


def document_rate(company, currency_code: str, on_date: date) -> Decimal:
    try:
        return default_rate(company, currency_code, on_date)
    except PostingError as exc:
        raise SalesError(exc.message, "exchange_rate") from exc


def customer_balance(customer) -> dict:
    """Open invoices and unallocated advances for a customer, in the company currency's terms."""
    open_invoices = Invoice.objects.filter(
        customer=customer, status__in=(Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID)
    )
    due = sum((inv.amount_due for inv in open_invoices), ZERO)
    advances = Receipt.objects.filter(customer=customer, status=Receipt.Status.POSTED).aggregate(
        amount=Sum("amount", default=ZERO), allocated=Sum("allocated_amount", default=ZERO)
    )
    decimals = currency_decimals(customer.company.currency_id)
    return {
        "open_invoices": open_invoices.count(),
        "amount_due": str(round_money(due, decimals)),
        "advances": str(round_money(advances["amount"] - advances["allocated"], decimals)),
        "credit_limit": str(customer.credit_limit),
    }
