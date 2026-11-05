"""Posting rules for purchasing. Each write runs in one transaction."""

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
from apps.inventory.services import StockError, move
from apps.organization.services import currency_decimals, next_document_number

from .models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseOrderLine,
    SupplierInvoice,
    SupplierPayment,
)

ZERO = Decimal("0")


class PurchaseError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _accounts(company) -> AccountingSettings:
    accounts = (
        AccountingSettings.objects.filter(company=company)
        .select_related(
            "payable",
            "supplier_advances",
            "grni",
            "purchase_expenses",
            "inventory",
            "vat_input",
            "cost_of_sales",
        )
        .first()
    )
    if accounts is None or accounts.payable is None or accounts.inventory is None:
        raise PurchaseError(
            "Set up accounting for this company first (Accounts → Chart of accounts)."
        )
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
        raise PurchaseError(exc.message, "date" if exc.field == "date" else None) from exc


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


def recompute(document, lines) -> None:
    decimals = currency_decimals(document.currency_id)
    for line in lines:
        line.compute(decimals)
    document.recompute_totals(lines)


def document_rate(company, currency_code: str, on_date: date) -> Decimal:
    try:
        return default_rate(company, currency_code, on_date)
    except PostingError as exc:
        raise PurchaseError(exc.message, "exchange_rate") from exc


# --- Purchase orders ------------------------------------------------------------------------------


def _refresh_order_status(order: PurchaseOrder) -> None:
    if order.status in (
        PurchaseOrder.Status.DRAFT,
        PurchaseOrder.Status.CLOSED,
        PurchaseOrder.Status.CANCELLED,
    ):
        return
    lines = list(order.lines.all())
    ordered = sum((ln.quantity for ln in lines), ZERO)
    received = sum((ln.received_quantity for ln in lines), ZERO)
    if received <= 0:
        order.status = PurchaseOrder.Status.SENT
    elif received >= ordered:
        order.status = PurchaseOrder.Status.RECEIVED
    else:
        order.status = PurchaseOrder.Status.PARTIAL


@transaction.atomic
def send_order(order: PurchaseOrder, user=None) -> PurchaseOrder:
    order = PurchaseOrder.objects.select_for_update().select_related("company").get(pk=order.pk)
    if order.status != PurchaseOrder.Status.DRAFT:
        raise PurchaseError("Only draft orders can be sent.")
    lines = list(order.lines.all())
    if not lines:
        raise PurchaseError("Add at least one line.", "lines")
    recompute(order, lines)
    for line in lines:
        line.save(update_fields=["net_amount", "tax_amount", "total_amount"])
    if not order.number:
        order.number = next_document_number(order.company, "PO", order.date)
    order.status = PurchaseOrder.Status.SENT
    order.sent_at = timezone.now()
    order.updated_by = user
    order.save()
    return order


@transaction.atomic
def cancel_order(order: PurchaseOrder, user=None) -> PurchaseOrder:
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in (PurchaseOrder.Status.DRAFT, PurchaseOrder.Status.SENT):
        raise PurchaseError("Goods have been received on this order; close it instead.")
    order.status = PurchaseOrder.Status.CANCELLED
    order.updated_by = user
    order.save()
    return order


@transaction.atomic
def close_order(order: PurchaseOrder, user=None) -> PurchaseOrder:
    """Stop expecting the rest (short shipment)."""
    order = PurchaseOrder.objects.select_for_update().get(pk=order.pk)
    if order.status not in (
        PurchaseOrder.Status.SENT,
        PurchaseOrder.Status.PARTIAL,
        PurchaseOrder.Status.RECEIVED,
    ):
        raise PurchaseError("Only sent orders can be closed.")
    order.status = PurchaseOrder.Status.CLOSED
    order.updated_by = user
    order.save()
    return order


# --- Goods receipts -------------------------------------------------------------------------------


@transaction.atomic
def post_goods_receipt(receipt: GoodsReceipt, user=None) -> GoodsReceipt:
    receipt = (
        GoodsReceipt.objects.select_for_update(of=("self",))
        .select_related("company", "order", "warehouse")
        .get(pk=receipt.pk)
    )
    if receipt.status != GoodsReceipt.Status.DRAFT:
        raise PurchaseError("This receipt has already been posted.")
    order = PurchaseOrder.objects.select_for_update().get(pk=receipt.order_id)
    if order.status not in (PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL):
        raise PurchaseError("Send the order before receiving against it.", "order")
    lines = list(
        receipt.lines.select_related(
            "order_line", "order_line__product", "order_line__product__uom"
        )
    )
    if not lines:
        raise PurchaseError("Add at least one line.", "lines")
    if receipt.freight_cost < 0 or receipt.customs_cost < 0:
        raise PurchaseError("Landed costs can't be negative.", "freight_cost")
    accounts = _accounts(receipt.company)
    decimals = currency_decimals(receipt.company.currency_id)
    rate = order.exchange_rate

    # Goods value per line in the company currency (order cost × rate), then landed costs spread
    # by value; the last line takes the rounding difference.
    for line in lines:
        order_line = PurchaseOrderLine.objects.select_for_update().get(pk=line.order_line_id)
        if order_line.order_id != order.pk:
            raise PurchaseError("A line belongs to another order.", "lines")
        remaining = order_line.quantity - order_line.received_quantity
        if line.quantity <= 0 or line.quantity > remaining:
            raise PurchaseError(
                f"{order_line.product.sku}: receive between 0 and {_qty(remaining)}.", "lines"
            )
        line.goods_value = round_money(order_line.unit_net_cost * rate * line.quantity, decimals)
    goods_total = sum((ln.goods_value for ln in lines), ZERO)
    landed_total = receipt.freight_cost + receipt.customs_cost
    allocated = ZERO
    for index, line in enumerate(lines):
        if landed_total > 0 and goods_total > 0:
            share = (
                landed_total - allocated
                if index == len(lines) - 1
                else round_money(landed_total * line.goods_value / goods_total, decimals)
            )
        else:
            share = ZERO
        allocated += share
        line.landed_share = share

    receipt.number = next_document_number(receipt.company, "GRN", receipt.date)
    stock_value = ZERO
    for line in lines:
        order_line = line.order_line
        product = order_line.product
        line_value = line.goods_value + line.landed_share
        line.unit_cost = (line_value / line.quantity).quantize(Decimal("0.000001"))
        if product.product_type == Product.ProductType.STOCK:
            try:
                stock_move = move(
                    receipt.company,
                    receipt.warehouse,
                    product,
                    StockStatus.AVAILABLE,
                    line.quantity,
                    on_date=receipt.date,
                    move_type=StockMove.MoveType.RECEIPT,
                    affects_cost=True,
                    unit_cost=line.unit_cost,
                    reference=receipt.number,
                    source=receipt,
                    user=user,
                )
            except StockError as exc:
                raise PurchaseError(exc.message, exc.field) from exc
            stock_value += stock_move.value
        line.save(update_fields=["unit_cost", "goods_value"])
        order_line.received_quantity += line.quantity
        order_line.save(update_fields=["received_quantity"])

    receipt.goods_value = goods_total
    if accounts.grni is None:
        raise PurchaseError(
            "Choose the 'Goods received not invoiced' account under Accounts → Default accounts."
        )
    journal_lines = [
        LineSpec(accounts.inventory, debit=goods_total + landed_total, description=receipt.number),
        LineSpec(
            accounts.grni,
            credit=goods_total,
            supplier=order.supplier,
            description=f"{receipt.number} · {order.number}",
        ),
    ]
    if landed_total > 0:
        if accounts.purchase_expenses is None:
            raise PurchaseError("Choose the freight and customs account under Default accounts.")
        journal_lines.append(
            LineSpec(
                accounts.purchase_expenses,
                credit=landed_total,
                description=f"{receipt.number} landed costs",
            )
        )
    if goods_total + landed_total > 0:
        receipt.journal_entry = _post(
            receipt.company,
            "PI",
            receipt.date,
            journal_lines,
            f"Goods receipt {receipt.number} · {order.supplier.name}",
            receipt.supplier_reference or receipt.number,
            receipt,
            None,
            None,
            user,
        )
    receipt.status = GoodsReceipt.Status.POSTED
    receipt.posted_at = timezone.now()
    receipt.updated_by = user
    receipt.save()
    _refresh_order_status(order)
    order.updated_by = user
    order.save()
    return receipt


# --- Supplier invoices ----------------------------------------------------------------------------


def _refresh_invoice_status(invoice: SupplierInvoice) -> None:
    if invoice.status in (SupplierInvoice.Status.DRAFT, SupplierInvoice.Status.CANCELLED):
        return
    if invoice.amount_due <= 0:
        invoice.status = SupplierInvoice.Status.PAID
    elif invoice.amount_paid > 0:
        invoice.status = SupplierInvoice.Status.PARTIALLY_PAID
    else:
        invoice.status = SupplierInvoice.Status.POSTED


@transaction.atomic
def post_supplier_invoice(invoice: SupplierInvoice, user=None) -> SupplierInvoice:
    invoice = (
        SupplierInvoice.objects.select_for_update(of=("self",))
        .select_related("company", "supplier", "currency", "order")
        .get(pk=invoice.pk)
    )
    if invoice.status != SupplierInvoice.Status.DRAFT:
        raise PurchaseError("Only draft bills can be posted.")
    lines = list(
        invoice.lines.select_related(
            "product",
            "product__expense_account",
            "account",
            "receipt_line",
            "receipt_line__order_line",
        )
    )
    if not lines:
        raise PurchaseError("Add at least one line.", "lines")
    if not invoice.supplier.is_active:
        raise PurchaseError("This supplier is inactive.", "supplier")
    accounts = _accounts(invoice.company)
    decimals = currency_decimals(invoice.company.currency_id)
    recompute(invoice, lines)
    if invoice.total <= 0:
        raise PurchaseError("The bill total must be greater than zero.", "lines")
    rate = invoice.exchange_rate
    invoice.number = next_document_number(invoice.company, "BILL", invoice.date)
    if invoice.due_date is None:
        invoice.due_date = invoice.date + timedelta(days=invoice.supplier.payment_terms_days)

    debits: dict[int, list] = {}  # account id -> [account, amount]

    def debit(account, amount):
        if amount == 0:
            return
        entry = debits.setdefault(account.pk, [account, ZERO])
        entry[1] += amount

    variance = ZERO
    for line in lines:
        line.save(update_fields=["net_amount", "tax_amount", "total_amount"])
        value = round_money(line.net_amount * rate, decimals)
        if line.receipt_line_id:
            receipt_line = GoodsReceiptLine.objects.select_for_update().get(pk=line.receipt_line_id)
            if receipt_line.receipt.status != GoodsReceipt.Status.POSTED:
                raise PurchaseError("Post the goods receipt before billing it.", "lines")
            if receipt_line.order_line.product_id != line.product_id:
                raise PurchaseError("The receipt line is for another product.", "lines")
            open_qty = receipt_line.quantity - receipt_line.invoiced_quantity
            if line.quantity <= 0 or line.quantity > open_qty:
                raise PurchaseError(
                    f"{line.product.sku}: bill between 0 and {_qty(open_qty)} received units.",
                    "lines",
                )
            if accounts.grni is None:
                raise PurchaseError("Choose the 'Goods received not invoiced' account.")
            grni_value = round_money(
                receipt_line.goods_value * line.quantity / receipt_line.quantity, decimals
            )
            debit(accounts.grni, grni_value)
            variance += value - grni_value
            receipt_line.invoiced_quantity += line.quantity
            receipt_line.save(update_fields=["invoiced_quantity"])
            order_line = PurchaseOrderLine.objects.select_for_update().get(
                pk=receipt_line.order_line_id
            )
            order_line.invoiced_quantity += line.quantity
            order_line.save(update_fields=["invoiced_quantity"])
        else:
            if line.product.product_type == Product.ProductType.STOCK:
                raise PurchaseError(
                    f"{line.product.sku} is a stock item: receive it first, then bill the receipt.",
                    "lines",
                )
            account = line.account or line.product.expense_account or accounts.purchase_expenses
            if account is not None and account.company_id != invoice.company_id:
                account = accounts.purchase_expenses
            if account is None:
                raise PurchaseError(
                    f"{line.product.sku}: choose an expense account for this line.", "lines"
                )
            debit(account, value)
    if variance != 0:
        # Price differences between the order and the bill go to freight & customs / purchase costs.
        target = accounts.purchase_expenses or accounts.cost_of_sales
        if target is None:
            raise PurchaseError("Choose the freight and customs account for price differences.")
        debit(target, variance)

    journal_lines = [
        LineSpec(account, debit=amount, supplier=invoice.supplier, description=invoice.number)
        if account.pk == (accounts.grni.pk if accounts.grni else None)
        else LineSpec(account, debit=amount, description=invoice.number)
        for account, amount in debits.values()
    ]
    tax_value = round_money(invoice.tax_total * rate, decimals)
    if tax_value > 0:
        if accounts.vat_input is None:
            raise PurchaseError("Choose the VAT on purchases account under Default accounts.")
        journal_lines.append(
            LineSpec(accounts.vat_input, debit=tax_value, description=invoice.number)
        )
    total_value = round_money(invoice.total * rate, decimals)
    journal_lines.append(
        LineSpec(
            accounts.payable,
            credit=total_value,
            supplier=invoice.supplier,
            description=f"{invoice.number} · {invoice.supplier_number or invoice.supplier.name}",
        )
    )
    # Rounding between the per-line debits and the total credit, if any, lands in purchase costs.
    debit_sum = sum((ln.debit for ln in journal_lines), ZERO)
    if debit_sum != total_value:
        target = accounts.purchase_expenses or accounts.cost_of_sales
        diff = total_value - debit_sum
        journal_lines.append(
            LineSpec(target, debit=diff, description=f"{invoice.number} rounding")
            if diff > 0
            else LineSpec(target, credit=-diff, description=f"{invoice.number} rounding")
        )

    invoice.journal_entry = _post(
        invoice.company,
        "PI",
        invoice.date,
        journal_lines,
        f"Bill {invoice.number} · {invoice.supplier.name}",
        invoice.supplier_number or invoice.number,
        invoice,
        # Every line above is already converted to the company currency.
        None,
        None,
        user,
    )
    invoice.status = SupplierInvoice.Status.POSTED
    invoice.posted_at = timezone.now()
    invoice.updated_by = user
    invoice.save()
    return invoice


@transaction.atomic
def cancel_supplier_invoice(invoice: SupplierInvoice, on_date: date, user=None) -> SupplierInvoice:
    from apps.accounting.services import reverse_entry

    invoice = SupplierInvoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != SupplierInvoice.Status.POSTED:
        raise PurchaseError("Only posted, unpaid bills can be cancelled.")
    if invoice.allocations.filter(payment__status=SupplierPayment.Status.POSTED).exists():
        raise PurchaseError("Payments are allocated to this bill.")
    try:
        reverse_entry(invoice.journal_entry, on_date, user, f"Cancel bill {invoice.number}")
    except PostingError as exc:
        raise PurchaseError(exc.message, "date" if exc.field == "date" else None) from exc
    for line in invoice.lines.filter(receipt_line__isnull=False).select_related("receipt_line"):
        receipt_line = GoodsReceiptLine.objects.select_for_update().get(pk=line.receipt_line_id)
        receipt_line.invoiced_quantity -= line.quantity
        receipt_line.save(update_fields=["invoiced_quantity"])
        PurchaseOrderLine.objects.filter(pk=receipt_line.order_line_id).update(
            invoiced_quantity=models_f("invoiced_quantity") - line.quantity
        )
    invoice.status = SupplierInvoice.Status.CANCELLED
    invoice.cancelled_at = timezone.now()
    invoice.updated_by = user
    invoice.save()
    return invoice


def models_f(name):
    from django.db.models import F

    return F(name)


# --- Supplier payments ----------------------------------------------------------------------------


@transaction.atomic
def post_supplier_payment(payment: SupplierPayment, user=None) -> SupplierPayment:
    payment = (
        SupplierPayment.objects.select_for_update(of=("self",))
        .select_related("company", "supplier", "journal", "journal__default_account")
        .get(pk=payment.pk)
    )
    if payment.status != SupplierPayment.Status.DRAFT:
        raise PurchaseError("Only draft payments can be posted.")
    if payment.amount <= 0:
        raise PurchaseError("The amount must be greater than zero.", "amount")
    if payment.journal.default_account is None:
        raise PurchaseError("The chosen journal has no cash or bank account.", "journal")
    accounts = _accounts(payment.company)
    allocations = list(payment.allocations.select_related("invoice"))
    allocated = sum((a.amount for a in allocations), ZERO)
    if allocated > payment.amount:
        raise PurchaseError("Allocations exceed the amount paid.", "allocations")
    unallocated = payment.amount - allocated
    if unallocated > 0 and accounts.supplier_advances is None:
        raise PurchaseError("Choose the advances to suppliers account.", "allocations")

    payment.number = next_document_number(payment.company, "SP", payment.date)
    journal_lines = []
    for allocation in allocations:
        invoice = SupplierInvoice.objects.select_for_update().get(pk=allocation.invoice_id)
        if invoice.supplier_id != payment.supplier_id:
            raise PurchaseError(
                f"Bill {invoice.number} belongs to another supplier.", "allocations"
            )
        if invoice.status not in (
            SupplierInvoice.Status.POSTED,
            SupplierInvoice.Status.PARTIALLY_PAID,
        ):
            raise PurchaseError(f"Bill {invoice.number} is not open.", "allocations")
        if invoice.currency_id != payment.currency_id:
            raise PurchaseError(
                f"Bill {invoice.number} is in {invoice.currency_id}; pay in the same currency.",
                "allocations",
            )
        if allocation.amount <= 0 or allocation.amount > invoice.amount_due:
            raise PurchaseError(
                f"Bill {invoice.number}: allocate between 0 and {invoice.amount_due}.",
                "allocations",
            )
        invoice.amount_paid += allocation.amount
        _refresh_invoice_status(invoice)
        invoice.updated_by = user
        invoice.save()
        journal_lines.append(
            LineSpec(
                accounts.payable,
                debit=allocation.amount,
                supplier=payment.supplier,
                description=f"{payment.number} → {invoice.number}",
            )
        )
    if unallocated > 0:
        journal_lines.append(
            LineSpec(
                accounts.supplier_advances,
                debit=unallocated,
                supplier=payment.supplier,
                description=f"{payment.number} advance",
            )
        )
    journal_lines.append(
        LineSpec(payment.journal.default_account, credit=payment.amount, description=payment.number)
    )
    payment.allocated_amount = allocated
    payment.journal_entry = _post(
        payment.company,
        payment.journal.code,
        payment.date,
        journal_lines,
        f"Payment {payment.number} · {payment.supplier.name}",
        payment.reference or payment.number,
        payment,
        payment.currency_id,
        payment.exchange_rate,
        user,
    )
    payment.status = SupplierPayment.Status.POSTED
    payment.posted_at = timezone.now()
    payment.updated_by = user
    payment.save()
    return payment


# --- Helpers --------------------------------------------------------------------------------------


def supplier_balance(supplier) -> dict:
    open_bills = SupplierInvoice.objects.filter(
        supplier=supplier,
        status__in=(SupplierInvoice.Status.POSTED, SupplierInvoice.Status.PARTIALLY_PAID),
    )
    due = sum((bill.amount_due for bill in open_bills), ZERO)
    advances = SupplierPayment.objects.filter(
        supplier=supplier, status=SupplierPayment.Status.POSTED
    ).aggregate(amount=Sum("amount", default=ZERO), allocated=Sum("allocated_amount", default=ZERO))
    decimals = currency_decimals(supplier.currency_id)
    return {
        "open_bills": open_bills.count(),
        "amount_due": str(round_money(due, decimals)),
        "advances": str(round_money(advances["amount"] - advances["allocated"], decimals)),
        "currency": supplier.currency_id,
    }


def receivable_lines(order: PurchaseOrder) -> list[dict]:
    """Order lines with something left to receive (for a new goods receipt)."""
    rows = []
    for line in order.lines.select_related("product", "product__uom"):
        remaining = line.quantity - line.received_quantity
        if remaining > 0:
            rows.append(
                {
                    "order_line": line.pk,
                    "product": line.product_id,
                    "sku": line.product.sku,
                    "product_name": line.product.name,
                    "uom": line.product.uom.code,
                    "ordered_quantity": str(line.quantity),
                    "received_before": str(line.received_quantity),
                    "quantity": str(remaining),
                }
            )
    return rows


def billable_lines(order: PurchaseOrder) -> list[dict]:
    """Posted receipt lines of an order not yet fully billed (for a new bill)."""
    rows = []
    receipt_lines = GoodsReceiptLine.objects.filter(
        receipt__order=order, receipt__status=GoodsReceipt.Status.POSTED
    ).select_related("receipt", "order_line", "order_line__product", "order_line__product__uom")
    for line in receipt_lines:
        open_qty = line.quantity - line.invoiced_quantity
        if open_qty > 0:
            rows.append(
                {
                    "receipt_line": line.pk,
                    "receipt_number": line.receipt.number,
                    "product": line.order_line.product_id,
                    "sku": line.order_line.product.sku,
                    "product_name": line.order_line.product.name,
                    "uom": line.order_line.product.uom.code,
                    "quantity": str(open_qty),
                    "unit_cost": str(line.order_line.unit_cost),
                    "discount_percent": str(line.order_line.discount_percent),
                    "tax_rate": str(line.order_line.tax_rate),
                }
            )
    return rows
