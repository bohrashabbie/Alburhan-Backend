"""Point-of-sale rules. A sale is rung up in one transaction: invoice → delivery → receipt(s).
If any step fails (no price, not enough stock, closed period…) nothing is saved."""

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from apps.access.services import companies_with
from apps.accounting.services import LineSpec, PostingError, create_and_post
from apps.catalog.models import PriceList, PriceListItem, Product, ProductCategory
from apps.catalog.services import active_price_list_q, get_unit_price
from apps.core.fields import round_money
from apps.core.images import image_url
from apps.inventory.models import Reservation, StockBalance, StockStatus
from apps.organization.services import currency_decimals, next_document_number
from apps.sales.models import (
    CreditNote,
    CreditNoteLine,
    DeliveryLine,
    DeliveryNote,
    Invoice,
    InvoiceLine,
    Receipt,
    ReceiptAllocation,
)
from apps.sales.services import (
    SalesError,
    _accounts,
    _refresh_invoice_status,
    _settings,
    post_credit_note,
    post_delivery,
    post_invoice,
    post_receipt,
    recompute,
)

from .models import Register, Sale, SalePayment, Shift

ZERO = Decimal("0")


class PosError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _wrap(exc: SalesError | PostingError) -> PosError:
    return PosError(exc.message, getattr(exc, "field", None))


def _money(value: Decimal, decimals: int) -> str:
    return f"{round_money(value, decimals):.{decimals}f}"


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


# --- Price list and catalogue --------------------------------------------------------------------


def price_list_for(register: Register, on_date: date) -> PriceList | None:
    """The register's own list if valid today, otherwise the company default."""
    valid = PriceList.objects.filter(company=register.company_id).filter(
        active_price_list_q(on_date)
    )
    if register.price_list_id:
        own = valid.filter(pk=register.price_list_id).first()
        if own:
            return own
    return valid.filter(is_default=True).first()


def register_catalog(
    register: Register, search: str = "", category_id: int | None = None, limit: int = 60
) -> dict:
    """Products the register can sell (those with a price), with the price and free stock in
    the register's warehouse. Barcode matches exactly; SKU / name match partly."""
    today = timezone.localdate()
    price_list = price_list_for(register, today)
    decimals = currency_decimals(register.company.currency_id)
    if price_list is None:
        return {
            "price_list": None,
            "currency": register.company.currency_id,
            "categories": [],
            "products": [],
        }

    priced = PriceListItem.objects.filter(price_list=price_list, product__is_active=True)
    category_ids = set(priced.values_list("product__category_id", flat=True).distinct())
    categories = [
        {"id": c.pk, "name": c.name, "name_ar": c.name_ar, "image": image_url(c, "image")}
        for c in ProductCategory.objects.filter(pk__in=category_ids).order_by("name")
    ]

    items = priced.select_related("product", "product__uom", "product__category")
    search = search.strip()
    if search:
        items = items.filter(
            Q(product__barcode=search)
            | Q(product__sku__icontains=search)
            | Q(product__model_code__icontains=search)
            | Q(product__name__icontains=search)
            | Q(product__name_ar__icontains=search)
        )
    if category_id:
        items = items.filter(product__category_id=category_id)

    # The base price is the row with the lowest minimum quantity.
    chosen: dict[int, PriceListItem] = {}
    for item in items.order_by("product__name", "product_id", "min_quantity"):
        if item.product_id not in chosen:
            chosen[item.product_id] = item
            if len(chosen) >= limit:
                break

    product_ids = list(chosen)
    on_hand = dict(
        StockBalance.objects.filter(
            warehouse=register.warehouse_id,
            product_id__in=product_ids,
            status=StockStatus.AVAILABLE,
        ).values_list("product_id", "quantity")
    )
    reserved = dict(
        Reservation.objects.filter(
            warehouse=register.warehouse_id,
            product_id__in=product_ids,
            status=Reservation.Status.ACTIVE,
        )
        .values("product_id")
        .annotate(total=Sum("quantity"))
        .values_list("product_id", "total")
    )

    products = []
    for item in chosen.values():
        product = item.product
        is_stock = product.product_type == Product.ProductType.STOCK
        free = on_hand.get(product.pk, ZERO) - reserved.get(product.pk, ZERO)
        products.append(
            {
                "id": product.pk,
                "sku": product.sku,
                "barcode": product.barcode,
                "name": product.name,
                "name_ar": product.name_ar,
                "uom": product.uom.code,
                "allow_decimal": product.uom.allow_decimal,
                "product_type": product.product_type,
                "category": product.category_id,
                "image": image_url(product, "image"),
                "price": _money(item.unit_price, decimals),
                "stock": _qty(max(free, ZERO)) if is_stock else None,
            }
        )
    return {
        "price_list": {"id": price_list.pk, "name": price_list.name},
        "currency": register.company.currency_id,
        "categories": categories,
        "products": products,
    }


# --- Shifts --------------------------------------------------------------------------------------


@transaction.atomic
def open_shift(register: Register, user, opening_cash: Decimal) -> Shift:
    register = Register.objects.select_for_update().select_related("company").get(pk=register.pk)
    if not register.is_active:
        raise PosError("This register is switched off.", "register")
    if register.shifts.filter(status=Shift.Status.OPEN).exists():
        raise PosError("This register already has an open shift.", "register")
    if opening_cash < 0:
        raise PosError("The opening float can't be negative.", "opening_cash")
    now = timezone.now()
    return Shift.objects.create(
        company=register.company,
        register=register,
        number=next_document_number(register.company, "SHF", timezone.localdate()),
        opened_by=user,
        opened_at=now,
        opening_cash=opening_cash,
        created_by=user,
        updated_by=user,
    )


def shift_totals(shift: Shift) -> dict:
    """Sales and money for a shift so far. Expected cash = float + cash kept − cash refunded."""
    decimals = currency_decimals(shift.company.currency_id)
    money = lambda value: round_money(value or ZERO, decimals)  # noqa: E731
    sales = Sale.objects.filter(shift=shift)
    gross = money(sales.aggregate(n=Sum("total", default=ZERO))["n"])
    refunds = money(
        sales.filter(status=Sale.Status.VOIDED).aggregate(n=Sum("total", default=ZERO))["n"]
    )
    by_method = {method: money(ZERO) for method in SalePayment.Method.values}
    for row in (
        SalePayment.objects.filter(sale__shift=shift).values("method").annotate(total=Sum("amount"))
    ):
        by_method[row["method"]] = money(row["total"])
    return {
        "sales_count": sales.filter(status=Sale.Status.COMPLETED).count(),
        "voided_count": sales.filter(status=Sale.Status.VOIDED).count(),
        "sales_total": gross,
        "refunds_total": refunds,
        "net_total": gross - refunds,
        "by_method": by_method,
        "expected_cash": money(shift.opening_cash + by_method[SalePayment.Method.CASH]),
    }


@transaction.atomic
def close_shift(shift: Shift, user, closing_cash: Decimal, notes: str = "") -> Shift:
    shift = (
        Shift.objects.select_for_update(of=("self",))
        .select_related(
            "company",
            "register",
            "register__branch",
            "register__cash_journal",
            "register__cash_journal__default_account",
            "register__cash_difference_account",
        )
        .get(pk=shift.pk)
    )
    if shift.status != Shift.Status.OPEN:
        raise PosError("This shift is already closed.")
    if closing_cash < 0:
        raise PosError("The counted cash can't be negative.", "closing_cash")
    totals = shift_totals(shift)
    today = timezone.localdate()
    shift.closing_cash = closing_cash
    shift.expected_cash = totals["expected_cash"]
    shift.cash_difference = closing_cash - totals["expected_cash"]
    shift.sales_count = totals["sales_count"]
    shift.sales_total = totals["sales_total"]
    shift.refunds_total = totals["refunds_total"]
    register = shift.register
    difference_account = register.cash_difference_account
    drawer = register.cash_journal.default_account
    if shift.cash_difference != 0 and difference_account is not None and drawer is not None:
        amount = abs(shift.cash_difference)
        short = shift.cash_difference < 0
        description = f"Shift {shift.number} cash {'short' if short else 'over'}"
        try:
            shift.difference_entry = create_and_post(
                shift.company,
                register.cash_journal.code,
                today,
                [
                    LineSpec(
                        difference_account if short else drawer,
                        debit=amount,
                        branch=register.branch,
                        description=description,
                    ),
                    LineSpec(
                        drawer if short else difference_account,
                        credit=amount,
                        branch=register.branch,
                        description=description,
                    ),
                ],
                description=description,
                reference=shift.number,
                source_type=shift._meta.label_lower,
                source_id=shift.pk,
                user=user,
            )
        except PostingError as exc:
            raise _wrap(exc) from exc
    shift.status = Shift.Status.CLOSED
    shift.closed_by = user
    shift.closed_at = timezone.now()
    shift.notes = notes
    shift.updated_by = user
    shift.save()
    return shift


# --- Sales ---------------------------------------------------------------------------------------


def _max_discount(company, user) -> Decimal:
    if user.is_superuser or company.pk in companies_with(user, "sales.discount.approve"):
        return Decimal(100)
    return _settings(company).max_line_discount_percent


def _check_payments(payments: list[dict], total: Decimal) -> tuple[Decimal, Decimal]:
    """Returns (cash kept, change). Card/KNET can't be more than the bill; cash can (change)."""
    if not payments:
        raise PosError("Add a payment.", "payments")
    for payment in payments:
        if payment["amount"] <= 0:
            raise PosError("Payment amounts must be greater than zero.", "payments")
    paid = sum((p["amount"] for p in payments), ZERO)
    non_cash = sum((p["amount"] for p in payments if p["method"] != SalePayment.Method.CASH), ZERO)
    if non_cash > total:
        raise PosError("KNET / card payments can't be more than the total.", "payments")
    if paid < total:
        raise PosError(f"Payment is short by {total - paid}.", "payments")
    change = paid - total
    cash = paid - non_cash
    return cash - change, change


@transaction.atomic
def make_sale(
    shift: Shift,
    user,
    lines: list[dict],
    payments: list[dict],
    customer=None,
    reference: str = "",
) -> Sale:
    """Ring up a sale. `lines`: [{product, quantity, discount_percent}], priced from the
    register's price list. `payments`: [{method, amount, reference}], cash may exceed the bill."""
    shift = (
        Shift.objects.select_for_update()
        .select_related(
            "company",
            "register",
            "register__branch",
            "register__warehouse",
            "register__walk_in_customer",
            "register__cash_journal",
            "register__card_journal",
        )
        .get(pk=shift.pk)
    )
    if shift.status != Shift.Status.OPEN:
        raise PosError("Open a shift first.", "shift")
    register = shift.register
    company = shift.company
    if not register.is_active:
        raise PosError("This register is switched off.", "shift")
    if not lines:
        raise PosError("The cart is empty.", "lines")

    customer = customer or register.walk_in_customer
    if customer.company_id != company.pk:
        raise PosError("Choose a customer of this company.", "customer")
    if not customer.is_active:
        raise PosError("This customer is inactive.", "customer")

    today = timezone.localdate()
    price_list = price_list_for(register, today)
    if price_list is None:
        raise PosError("This register has no valid price list.", "lines")
    if price_list.currency_id != company.currency_id:
        raise PosError(f"The price list must be in {company.currency_id}.", "lines")

    tax_rate = _settings(company).default_tax_rate
    max_discount = _max_discount(company, user)
    invoice = Invoice.objects.create(
        company=company,
        date=today,
        due_date=today,
        branch=register.branch,
        customer=customer,
        salesperson=user,
        currency_id=company.currency_id,
        exchange_rate=Decimal(1),
        warehouse=register.warehouse,
        reference=reference,
        notes=f"POS {register.code} · {shift.number}",
        created_by=user,
        updated_by=user,
    )
    invoice_lines = []
    for index, line in enumerate(lines):
        product: Product = line["product"]
        quantity = line["quantity"]
        discount = line.get("discount_percent") or ZERO
        if not product.is_active:
            raise PosError(f"{product.sku} is inactive.", "lines")
        if quantity <= 0:
            raise PosError(f"{product.sku}: quantity must be positive.", "lines")
        if not product.uom.allow_decimal and quantity != quantity.to_integral_value():
            raise PosError(f"{product.sku} is sold in whole {product.uom.code}.", "lines")
        if discount < 0 or discount > 100:
            raise PosError(f"{product.sku}: discount must be between 0 and 100%.", "lines")
        if discount > max_discount:
            raise PosError(
                f"{product.sku}: discounts above {max_discount}% need a manager.", "lines"
            )
        price = get_unit_price(price_list, product, quantity, today)
        if price is None:
            raise PosError(f"{product.sku} has no price in {price_list.name}.", "lines")
        invoice_lines.append(
            InvoiceLine(
                invoice=invoice,
                product=product,
                quantity=quantity,
                unit_price=price,
                discount_percent=discount,
                tax_rate=tax_rate,
                sequence=index,
            )
        )
    recompute(invoice, invoice_lines)
    InvoiceLine.objects.bulk_create(invoice_lines)
    invoice.save(update_fields=["subtotal", "tax_total", "total"])
    cash_kept, change = _check_payments(payments, invoice.total)

    try:
        invoice = post_invoice(invoice, user)
        delivery = None
        stock_lines = [
            ln
            for ln in invoice.lines.select_related("product")
            if ln.product.product_type == Product.ProductType.STOCK
        ]
        if stock_lines:
            delivery = DeliveryNote.objects.create(
                company=company,
                date=today,
                invoice=invoice,
                warehouse=register.warehouse,
                delivered_to=customer.name,
                notes="Taken at the counter",
                created_by=user,
                updated_by=user,
            )
            DeliveryLine.objects.bulk_create(
                [
                    DeliveryLine(delivery=delivery, invoice_line=ln, quantity=ln.quantity)
                    for ln in stock_lines
                ]
            )
            delivery = post_delivery(delivery, user)

        sale = Sale.objects.create(
            company=company,
            shift=shift,
            branch=register.branch,
            number=invoice.number,
            date=today,
            customer=customer,
            invoice=invoice,
            delivery=delivery,
            subtotal=invoice.subtotal,
            tax_total=invoice.tax_total,
            total=invoice.total,
            tendered=sum((p["amount"] for p in payments), ZERO),
            change_given=change,
            created_by=user,
            updated_by=user,
        )

        # Cash is merged into one receipt and reduced by the change handed back.
        kept = []
        if cash_kept > 0:
            cash_refs = [p.get("reference", "") for p in payments if p["method"] == "cash"]
            kept.append((SalePayment.Method.CASH, cash_kept, ", ".join(filter(None, cash_refs))))
        kept += [
            (p["method"], p["amount"], p.get("reference", ""))
            for p in payments
            if p["method"] != SalePayment.Method.CASH
        ]
        for method, amount, payment_ref in kept:
            journal = (
                register.cash_journal
                if method == SalePayment.Method.CASH
                else register.card_journal
            )
            receipt = Receipt.objects.create(
                company=company,
                date=today,
                branch=register.branch,
                customer=customer,
                method=method,
                journal=journal,
                reference=payment_ref,
                currency_id=company.currency_id,
                exchange_rate=Decimal(1),
                amount=amount,
                notes=f"POS sale {invoice.number}",
                created_by=user,
                updated_by=user,
            )
            ReceiptAllocation.objects.create(receipt=receipt, invoice=invoice, amount=amount)
            receipt = post_receipt(receipt, user)
            SalePayment.objects.create(
                sale=sale, method=method, amount=amount, reference=payment_ref, receipt=receipt
            )
    except SalesError as exc:
        raise _wrap(exc) from exc
    return sale


@transaction.atomic
def void_sale(sale: Sale, user, reason: str) -> Sale:
    """Undo a sale from the open shift: a full credit note (goods back to stock) and the money
    paid back the way it came in."""
    sale = (
        Sale.objects.select_for_update()
        .select_related(
            "company",
            "shift",
            "shift__register",
            "shift__register__warehouse",
            "branch",
            "customer",
        )
        .get(pk=sale.pk)
    )
    if sale.status != Sale.Status.COMPLETED:
        raise PosError("This sale is already voided.")
    if sale.shift.status != Shift.Status.OPEN:
        raise PosError(
            "The shift is closed. Use a credit note under Sales to return goods.", "shift"
        )
    if not reason.strip():
        raise PosError("Give a reason.", "reason")
    company = sale.company
    register = sale.shift.register
    today = timezone.localdate()
    invoice = Invoice.objects.select_for_update().get(pk=sale.invoice_id)

    note = CreditNote.objects.create(
        company=company,
        date=today,
        branch=sale.branch,
        customer=sale.customer,
        salesperson=user,
        currency_id=invoice.currency_id,
        exchange_rate=invoice.exchange_rate,
        invoice=invoice,
        warehouse=register.warehouse,
        reason=f"POS void: {reason.strip()}"[:255],
        created_by=user,
        updated_by=user,
    )
    note_lines = []
    for index, ln in enumerate(invoice.lines.select_related("product")):
        quantity = ln.quantity - ln.returned_quantity
        if quantity <= 0:
            continue
        is_stock = ln.product.product_type == Product.ProductType.STOCK
        note_lines.append(
            CreditNoteLine(
                credit_note=note,
                invoice_line=ln,
                product=ln.product,
                description=ln.description,
                quantity=quantity,
                unit_price=ln.unit_price,
                discount_percent=ln.discount_percent,
                tax_rate=ln.tax_rate,
                restock=is_stock and ln.delivered_quantity >= quantity,
                sequence=index,
            )
        )
    recompute(note, note_lines)
    CreditNoteLine.objects.bulk_create(note_lines)
    note.save(update_fields=["subtotal", "tax_total", "total"])
    try:
        note = post_credit_note(note, user)
        accounts = _accounts(company)
    except SalesError as exc:
        raise _wrap(exc) from exc

    refunded = ZERO
    payments = list(sale.payments.filter(amount__gt=0).select_related("receipt__journal"))
    for payment in payments:
        journal = payment.receipt.journal
        if journal.default_account is None:
            raise PosError(f"Journal {journal.code} has no cash or bank account.")
        description = f"Refund {sale.number} ({payment.get_method_display()})"
        try:
            entry = create_and_post(
                company,
                journal.code,
                today,
                [
                    LineSpec(
                        accounts.receivable,
                        debit=payment.amount,
                        customer=sale.customer,
                        branch=sale.branch,
                        description=description,
                    ),
                    LineSpec(
                        journal.default_account,
                        credit=payment.amount,
                        branch=sale.branch,
                        description=description,
                    ),
                ],
                description=description,
                reference=sale.number,
                source_type=sale._meta.label_lower,
                source_id=sale.pk,
                user=user,
            )
        except PostingError as exc:
            raise _wrap(exc) from exc
        SalePayment.objects.create(
            sale=sale,
            method=payment.method,
            amount=-payment.amount,
            reference=payment.reference,
            journal_entry=entry,
        )
        refunded += payment.amount

    invoice.refresh_from_db()
    invoice.amount_paid -= refunded
    _refresh_invoice_status(invoice)
    invoice.updated_by = user
    invoice.save()

    sale.credit_note = note
    sale.status = Sale.Status.VOIDED
    sale.voided_at = timezone.now()
    sale.voided_by = user
    sale.void_reason = reason.strip()[:255]
    sale.updated_by = user
    sale.save()
    return sale
