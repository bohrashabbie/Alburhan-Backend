"""The stock engine. Every quantity change goes through `move`, inside the caller's transaction."""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import Max, Q, Sum
from django.utils import timezone

from apps.accounting.models import AccountingSettings
from apps.accounting.services import LineSpec, PostingError, create_and_post
from apps.catalog.models import Product
from apps.core.fields import round_money
from apps.organization.services import currency_decimals, next_document_number

from .models import (
    DocumentStatus,
    ProductCost,
    Reservation,
    StockAdjustment,
    StockBalance,
    StockCount,
    StockMove,
    StockStatus,
    StockTransfer,
)

ZERO = Decimal("0")


class StockError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _qty(value: Decimal) -> str:
    return f"{value.normalize():f}"


# --- The single entry point for quantity changes --------------------------------------------


def move(
    company,
    warehouse,
    product: Product,
    status: str,
    quantity: Decimal,
    *,
    on_date: date,
    move_type: str,
    affects_cost: bool,
    unit_cost: Decimal | None = None,
    reference: str = "",
    source=None,
    note: str = "",
    user=None,
) -> StockMove:
    """Change one balance by `quantity` (signed).

    `affects_cost` is True when stock enters or leaves the company (adjustments, counts, receipts,
    issues): it updates the weighted-average cost and the stock value. Internal moves (transfers,
    status changes) carry the current average cost for reporting only.
    """
    if quantity == 0:
        raise StockError("Quantity can't be zero.")
    if warehouse.company_id != company.pk:
        raise StockError(f"{warehouse.name} belongs to another company.", "warehouse")
    if product.product_type != Product.ProductType.STOCK:
        raise StockError(f"{product.sku} is not a stock item.", "lines")
    if not product.uom.allow_decimal and quantity != quantity.to_integral_value():
        raise StockError(f"{product.sku} is counted in whole {product.uom.code}.", "lines")

    balance, _ = StockBalance.objects.get_or_create(
        warehouse=warehouse, product=product, status=status, defaults={"company": company}
    )
    balance = StockBalance.objects.select_for_update().get(pk=balance.pk)
    if balance.quantity + quantity < 0:
        label = StockStatus(status).label.lower()
        raise StockError(
            f"Not enough {product.sku} {label} in {warehouse.name}: {_qty(balance.quantity)} on hand.",
            "lines",
        )

    cost, _ = ProductCost.objects.get_or_create(company=company, product=product)
    cost = ProductCost.objects.select_for_update().get(pk=cost.pk)
    decimals = currency_decimals(company.currency_id)

    if affects_cost and quantity > 0:
        unit = cost.average_cost if unit_cost is None else Decimal(unit_cost)
        if unit < 0:
            raise StockError("Unit cost can't be negative.", "lines")
        value = round_money(quantity * unit, decimals)
        cost.quantity += quantity
        cost.value += value
    elif affects_cost:
        unit = cost.average_cost
        if cost.quantity + quantity == 0:
            value = -cost.value  # the last units take the remaining value, so nothing is left over
        else:
            value = round_money(quantity * unit, decimals)
        cost.quantity += quantity
        cost.value += value
    else:
        unit = cost.average_cost
        value = round_money(quantity * unit, decimals)

    if affects_cost:
        # When the last unit leaves, the average is kept so the next receipt without a cost
        # still has a sensible default.
        if cost.quantity > 0:
            cost.average_cost = (cost.value / cost.quantity).quantize(Decimal("0.000001"))
        cost.save(update_fields=["quantity", "value", "average_cost"])

    balance.quantity += quantity
    balance.save(update_fields=["quantity"])
    return StockMove.objects.create(
        company=company,
        date=on_date,
        warehouse=warehouse,
        product=product,
        status=status,
        quantity=quantity,
        unit_cost=unit,
        value=value,
        move_type=move_type,
        reference=reference,
        source_type=source._meta.label_lower if source is not None else "",
        source_id=source.pk if source is not None else None,
        note=note,
        created_by=user,
    )


def available_quantity(warehouse, product) -> Decimal:
    row = StockBalance.objects.filter(
        warehouse=warehouse, product=product, status=StockStatus.AVAILABLE
    ).first()
    return row.quantity if row else ZERO


def reserved_quantity(warehouse, product) -> Decimal:
    return Reservation.objects.filter(
        warehouse=warehouse, product=product, status=Reservation.Status.ACTIVE
    ).aggregate(total=Sum("quantity", default=ZERO))["total"]


# --- Accounting --------------------------------------------------------------------------------


def _post_value(
    company, on_date, value: Decimal, counter_account, description, reference, source, user
):
    """Inventory against the counter account for a net stock value change."""
    if value == 0:
        return None
    settings = (
        AccountingSettings.objects.filter(company=company)
        .select_related("inventory", "inventory_adjustment")
        .first()
    )
    if settings is None or settings.inventory is None:
        raise StockError("Set up accounting for this company first (Accounts → Chart of accounts).")
    counter = counter_account or settings.inventory_adjustment
    if counter is None:
        raise StockError(
            "Choose the inventory adjustment account under Accounts → Default accounts."
        )
    amount = abs(value)
    lines = (
        [
            LineSpec(settings.inventory, debit=amount, description=description),
            LineSpec(counter, credit=amount, description=description),
        ]
        if value > 0
        else [
            LineSpec(counter, debit=amount, description=description),
            LineSpec(settings.inventory, credit=amount, description=description),
        ]
    )
    try:
        return create_and_post(
            company,
            "JV",
            on_date,
            lines,
            description=description,
            reference=reference,
            source_type=source._meta.label_lower,
            source_id=source.pk,
            user=user,
        )
    except PostingError as exc:
        raise StockError(exc.message, "date" if exc.field == "date" else None) from exc


# --- Adjustments ---------------------------------------------------------------------------------


@transaction.atomic
def post_adjustment(adjustment: StockAdjustment, user=None) -> StockAdjustment:
    adjustment = (
        StockAdjustment.objects.select_for_update()
        .select_related("company", "warehouse")
        .get(pk=adjustment.pk)
    )
    if adjustment.status != DocumentStatus.DRAFT:
        raise StockError("Only draft adjustments can be posted.")
    lines = list(adjustment.lines.select_related("product", "product__uom"))
    if not lines:
        raise StockError("Add at least one line.", "lines")

    status_only = adjustment.reason == StockAdjustment.Reason.STATUS
    if status_only:
        net = defaultdict(Decimal)
        for line in lines:
            net[line.product_id] += line.quantity
        if any(net.values()):
            raise StockError(
                "A status change must add and remove the same quantity of each product.", "lines"
            )

    adjustment.number = next_document_number(adjustment.company, "ADJ", adjustment.date)
    # Removals first, so a status change can't create stock out of nothing.
    total = ZERO
    for line in sorted(lines, key=lambda ln: ln.quantity):
        stock_move = move(
            adjustment.company,
            adjustment.warehouse,
            line.product,
            line.status,
            line.quantity,
            on_date=adjustment.date,
            move_type=StockMove.MoveType.STATUS if status_only else StockMove.MoveType.ADJUSTMENT,
            affects_cost=not status_only,
            unit_cost=line.unit_cost,
            reference=adjustment.number,
            source=adjustment,
            user=user,
        )
        total += stock_move.value if not status_only else ZERO

    adjustment.journal_entry = _post_value(
        adjustment.company,
        adjustment.date,
        total,
        adjustment.counter_account,
        f"Stock adjustment {adjustment.number}",
        adjustment.number,
        adjustment,
        user,
    )
    adjustment.status = DocumentStatus.POSTED
    adjustment.posted_at = timezone.now()
    adjustment.updated_by = user
    adjustment.save()
    return adjustment


# --- Transfers -----------------------------------------------------------------------------------


@transaction.atomic
def dispatch_transfer(transfer: StockTransfer, user=None) -> StockTransfer:
    """Transfer Out: stock leaves the source and shows as in transit at the destination."""
    transfer = (
        StockTransfer.objects.select_for_update()
        .select_related("company", "from_warehouse", "to_warehouse")
        .get(pk=transfer.pk)
    )
    if transfer.status != StockTransfer.Status.DRAFT:
        raise StockError("This transfer has already been sent.")
    lines = list(transfer.lines.select_related("product", "product__uom"))
    if not lines:
        raise StockError("Add at least one line.", "lines")
    transfer.number = next_document_number(transfer.company, "TRF", transfer.date)
    for line in lines:
        free = available_quantity(transfer.from_warehouse, line.product) - reserved_quantity(
            transfer.from_warehouse, line.product
        )
        if line.quantity > free:
            raise StockError(
                f"Only {_qty(max(free, ZERO))} {line.product.sku} free in {transfer.from_warehouse.name} "
                "(the rest is reserved or missing).",
                "lines",
            )
        common = {
            "on_date": transfer.date,
            "move_type": StockMove.MoveType.TRANSFER_OUT,
            "affects_cost": False,
            "reference": transfer.number,
            "source": transfer,
            "user": user,
        }
        move(
            transfer.company,
            transfer.from_warehouse,
            line.product,
            StockStatus.AVAILABLE,
            -line.quantity,
            **common,
        )
        move(
            transfer.company,
            transfer.to_warehouse,
            line.product,
            StockStatus.IN_TRANSIT,
            line.quantity,
            **common,
        )
    transfer.status = StockTransfer.Status.IN_TRANSIT
    transfer.dispatched_at = timezone.now()
    transfer.updated_by = user
    transfer.save()
    return transfer


@transaction.atomic
def receive_transfer(
    transfer: StockTransfer, quantities: dict[int, Decimal], on_date: date, user=None
) -> StockTransfer:
    """Transfer In: received quantities move from in transit to available. Partial is allowed."""
    transfer = (
        StockTransfer.objects.select_for_update()
        .select_related("company", "to_warehouse")
        .get(pk=transfer.pk)
    )
    if transfer.status not in (StockTransfer.Status.IN_TRANSIT, StockTransfer.Status.PARTIAL):
        raise StockError("Only transfers in transit can be received.")
    lines = {line.pk: line for line in transfer.lines.select_related("product", "product__uom")}
    received_any = False
    for line_id, quantity in quantities.items():
        line = lines.get(int(line_id))
        if line is None:
            raise StockError("Unknown transfer line.", "lines")
        quantity = Decimal(quantity)
        if quantity == 0:
            continue
        remaining = line.quantity - line.received_quantity
        if quantity < 0 or quantity > remaining:
            raise StockError(
                f"{line.product.sku}: receive between 0 and {_qty(remaining)}.", "lines"
            )
        common = {
            "on_date": on_date,
            "move_type": StockMove.MoveType.TRANSFER_IN,
            "affects_cost": False,
            "reference": transfer.number,
            "source": transfer,
            "user": user,
        }
        move(
            transfer.company,
            transfer.to_warehouse,
            line.product,
            StockStatus.IN_TRANSIT,
            -quantity,
            **common,
        )
        move(
            transfer.company,
            transfer.to_warehouse,
            line.product,
            StockStatus.AVAILABLE,
            quantity,
            **common,
        )
        line.received_quantity += quantity
        line.save(update_fields=["received_quantity"])
        received_any = True
    if not received_any:
        raise StockError("Enter at least one received quantity.", "lines")
    done = all(line.received_quantity == line.quantity for line in lines.values())
    transfer.status = StockTransfer.Status.RECEIVED if done else StockTransfer.Status.PARTIAL
    transfer.received_at = timezone.now() if done else None
    transfer.updated_by = user
    transfer.save()
    return transfer


# --- Stock counts --------------------------------------------------------------------------------


@transaction.atomic
def fill_count(count: StockCount) -> int:
    """Add a line for every product with available stock in the warehouse (counted = expected)."""
    from .models import StockCountLine

    have = set(count.lines.values_list("product_id", flat=True))
    rows = StockBalance.objects.filter(
        warehouse=count.warehouse, status=StockStatus.AVAILABLE, quantity__gt=0
    ).exclude(product_id__in=have)
    StockCountLine.objects.bulk_create(
        [
            StockCountLine(
                count=count,
                product_id=row.product_id,
                expected_quantity=row.quantity,
                counted_quantity=row.quantity,
            )
            for row in rows
        ]
    )
    return len(rows)


@transaction.atomic
def post_count(count: StockCount, user=None) -> StockCount:
    count = (
        StockCount.objects.select_for_update()
        .select_related("company", "warehouse")
        .get(pk=count.pk)
    )
    if count.status != DocumentStatus.DRAFT:
        raise StockError("Only draft counts can be posted.")
    lines = list(count.lines.select_related("product", "product__uom"))
    if not lines:
        raise StockError("Add at least one counted product.", "lines")
    count.number = next_document_number(count.company, "CNT", count.date)
    total = ZERO
    for line in lines:
        line.expected_quantity = available_quantity(count.warehouse, line.product)
        line.save(update_fields=["expected_quantity"])
        difference = line.counted_quantity - line.expected_quantity
        if difference:
            stock_move = move(
                count.company,
                count.warehouse,
                line.product,
                StockStatus.AVAILABLE,
                difference,
                on_date=count.date,
                move_type=StockMove.MoveType.COUNT,
                affects_cost=True,
                reference=count.number,
                source=count,
                user=user,
            )
            total += stock_move.value
    count.journal_entry = _post_value(
        count.company,
        count.date,
        total,
        None,
        f"Stock count {count.number}",
        count.number,
        count,
        user,
    )
    count.status = DocumentStatus.POSTED
    count.posted_at = timezone.now()
    count.updated_by = user
    count.save()
    return count


# --- Reservations --------------------------------------------------------------------------------


@transaction.atomic
def reserve(
    company, warehouse, product, quantity: Decimal, reference: str = "", source=None, user=None
) -> Reservation:
    balance = (
        StockBalance.objects.select_for_update()
        .filter(warehouse=warehouse, product=product, status=StockStatus.AVAILABLE)
        .first()
    )
    free = (balance.quantity if balance else ZERO) - reserved_quantity(warehouse, product)
    if quantity <= 0:
        raise StockError("Reserve a positive quantity.", "quantity")
    if quantity > free:
        raise StockError(
            f"Only {_qty(max(free, ZERO))} {product.sku} free in {warehouse.name}.", "quantity"
        )
    return Reservation.objects.create(
        company=company,
        warehouse=warehouse,
        product=product,
        quantity=quantity,
        reference=reference,
        source_type=source._meta.label_lower if source is not None else "",
        source_id=source.pk if source is not None else None,
        created_by=user,
        updated_by=user,
    )


# --- Reports -------------------------------------------------------------------------------------


def _q4(value) -> str:
    """Quantities as exact strings with 4 decimals, matching QuantityField."""
    return str(Decimal(value).quantize(Decimal("0.0001")))


def stock_levels(company, warehouse_ids=None, product_filter: Q | None = None) -> list[dict]:
    """One row per warehouse and product with every status, reserved and free quantities, value."""
    balances = StockBalance.objects.filter(company=company).exclude(quantity=0)
    reservations = Reservation.objects.filter(company=company, status=Reservation.Status.ACTIVE)
    if warehouse_ids is not None:
        balances = balances.filter(warehouse_id__in=warehouse_ids)
        reservations = reservations.filter(warehouse_id__in=warehouse_ids)
    if product_filter is not None:
        balances = balances.filter(product_filter)
        reservations = reservations.filter(product_filter)

    rows: dict[tuple[int, int], dict] = {}
    for balance in balances.select_related("warehouse", "product", "product__uom"):
        key = (balance.warehouse_id, balance.product_id)
        row = rows.setdefault(
            key,
            {
                "warehouse": balance.warehouse_id,
                "warehouse_name": balance.warehouse.name,
                "product": balance.product_id,
                "sku": balance.product.sku,
                "product_name": balance.product.name,
                "uom": balance.product.uom.code,
                **{status.value: ZERO for status in StockStatus},
                "reserved": ZERO,
            },
        )
        row[balance.status] += balance.quantity
    for item in reservations.values("warehouse_id", "product_id").annotate(total=Sum("quantity")):
        key = (item["warehouse_id"], item["product_id"])
        if key in rows:
            rows[key]["reserved"] += item["total"]
    costs = dict(
        ProductCost.objects.filter(
            company=company, product_id__in={key[1] for key in rows}
        ).values_list("product_id", "average_cost")
    )
    decimals = currency_decimals(company.currency_id)
    result = []
    for row in rows.values():
        average = costs.get(row["product"], ZERO)
        on_hand = sum(row[status.value] for status in StockStatus)
        row["free"] = row["available"] - row["reserved"]
        row["value"] = str(round_money(on_hand * average, decimals))
        row["average_cost"] = str(Decimal(average).quantize(Decimal("0.000001")))
        for key in [status.value for status in StockStatus] + ["reserved", "free"]:
            row[key] = _q4(row[key])
        result.append(row)
    result.sort(key=lambda r: (r["sku"], r["warehouse_name"]))
    return result


def reorder_suggestions(company, warehouse_ids=None) -> list[dict]:
    from .models import ReorderRule

    rules = ReorderRule.objects.filter(company=company, is_active=True).select_related(
        "warehouse", "product"
    )
    if warehouse_ids is not None:
        rules = rules.filter(warehouse_id__in=warehouse_ids)
    result = []
    for rule in rules:
        free = available_quantity(rule.warehouse, rule.product) - reserved_quantity(
            rule.warehouse, rule.product
        )
        if free < rule.min_quantity:
            result.append(
                {
                    "rule": rule.pk,
                    "warehouse": rule.warehouse_id,
                    "warehouse_name": rule.warehouse.name,
                    "product": rule.product_id,
                    "sku": rule.product.sku,
                    "product_name": rule.product.name,
                    "free": _q4(free),
                    "min_quantity": _q4(rule.min_quantity),
                    "suggested": _q4(max(rule.reorder_quantity, rule.min_quantity - free)),
                }
            )
    return sorted(result, key=lambda r: (r["warehouse_name"], r["sku"]))


def slow_moving(company, days: int, today: date | None = None) -> list[dict]:
    """Products in stock with no outgoing movement for `days` days."""
    today = today or timezone.localdate()
    since = today - timedelta(days=days)
    in_stock = ProductCost.objects.filter(company=company, quantity__gt=0).select_related("product")
    last_out = dict(
        StockMove.objects.filter(company=company, quantity__lt=0)
        .exclude(move_type__in=[StockMove.MoveType.STATUS])
        .values("product_id")
        .annotate(last=Max("date"))
        .values_list("product_id", "last")
    )
    first_in = dict(
        StockMove.objects.filter(company=company, quantity__gt=0)
        .values("product_id")
        .annotate(first=Max("date"))
        .values_list("product_id", "first")
    )
    result = []
    for cost in in_stock:
        last = last_out.get(cost.product_id)
        if last is None or last < since:
            result.append(
                {
                    "product": cost.product_id,
                    "sku": cost.product.sku,
                    "product_name": cost.product.name,
                    "quantity": _q4(cost.quantity),
                    "value": str(cost.value),
                    "last_out": last,
                    "last_in": first_in.get(cost.product_id),
                    "days_idle": (today - (last or first_in.get(cost.product_id) or today)).days,
                }
            )
    return sorted(result, key=lambda r: -r["days_idle"])
