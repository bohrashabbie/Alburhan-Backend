"""One product at a glance, for the item card: code, unit, price, photo; stock, reserved and
free quantity per warehouse; what is coming (open purchase orders, transfers on the way); and
its latest movements."""

from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.catalog.models import PriceList, PriceListItem, Product
from apps.catalog.services import active_price_list_q
from apps.core.images import image_url
from apps.organization.models import Warehouse

from .models import (
    Reservation,
    StockBalance,
    StockMove,
    StockStatus,
    StockTransfer,
    StockTransferLine,
)

ZERO = Decimal("0")


def _qty(value) -> str:
    value = value or ZERO
    return f"{value.normalize():f}" if value else "0"


def item_overview(company, product: Product, warehouse_ids=None) -> dict:
    """`warehouse_ids` limits the per-warehouse rows to what the viewer may see (None = all)."""
    from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine

    today = timezone.localdate()
    price_list = (
        PriceList.objects.filter(company=company, is_default=True)
        .filter(active_price_list_q(today))
        .first()
    )
    price_row = (
        PriceListItem.objects.filter(price_list=price_list, product=product)
        .order_by("min_quantity")
        .first()
        if price_list
        else None
    )

    warehouses = Warehouse.objects.filter(company=company, is_active=True).select_related("branch")
    if warehouse_ids is not None:
        warehouses = warehouses.filter(pk__in=warehouse_ids)
    warehouses = list(warehouses.order_by("code"))
    ids = [w.pk for w in warehouses]

    balances = {
        (wid, status): qty
        for wid, status, qty in StockBalance.objects.filter(
            product=product, warehouse_id__in=ids
        ).values_list("warehouse_id", "status", "quantity")
    }
    reserved = dict(
        Reservation.objects.filter(
            product=product, warehouse_id__in=ids, status=Reservation.Status.ACTIVE
        )
        .values("warehouse_id")
        .annotate(total=Sum("quantity"))
        .values_list("warehouse_id", "total")
    )

    # Coming in: what open purchase orders still owe, and transfers on the way.
    open_po = (
        PurchaseOrderLine.objects.filter(
            product=product,
            order__company=company,
            order__status__in=[PurchaseOrder.Status.SENT, PurchaseOrder.Status.PARTIAL],
        )
        .select_related("order", "order__supplier", "order__warehouse")
        .order_by("order__expected_date", "order__id")
    )
    incoming = []
    on_order: dict[int, Decimal] = {}
    for line in open_po:
        left = line.quantity - line.received_quantity
        if left <= 0:
            continue
        wid = line.order.warehouse_id
        on_order[wid] = on_order.get(wid, ZERO) + left
        incoming.append(
            {
                "kind": "purchase",
                "document": line.order.number,
                "document_id": line.order_id,
                "from": line.order.supplier.name,
                "to": line.order.warehouse.name if line.order.warehouse_id else "",
                "ordered": _qty(line.quantity),
                "received": _qty(line.received_quantity),
                "remaining": _qty(left),
                "expected_date": line.order.expected_date,
                "status": line.order.get_status_display(),
            }
        )
    transit_lines = (
        StockTransferLine.objects.filter(
            product=product,
            transfer__company=company,
            transfer__status__in=[StockTransfer.Status.IN_TRANSIT, StockTransfer.Status.PARTIAL],
        )
        .select_related("transfer", "transfer__from_warehouse", "transfer__to_warehouse")
        .order_by("transfer__date", "transfer__id")
    )
    on_the_way: dict[int, Decimal] = {}
    for line in transit_lines:
        left = line.quantity - line.received_quantity
        if left <= 0:
            continue
        wid = line.transfer.to_warehouse_id
        on_the_way[wid] = on_the_way.get(wid, ZERO) + left
        incoming.append(
            {
                "kind": "transfer",
                "document": line.transfer.number,
                "document_id": line.transfer_id,
                "from": line.transfer.from_warehouse.name,
                "to": line.transfer.to_warehouse.name,
                "ordered": _qty(line.quantity),
                "received": _qty(line.received_quantity),
                "remaining": _qty(left),
                "expected_date": None,
                "status": line.transfer.get_status_display(),
            }
        )

    rows = []
    totals = {k: ZERO for k in ("on_hand", "reserved", "free", "on_order", "in_transit")}
    for w in warehouses:
        on_hand = balances.get((w.pk, StockStatus.AVAILABLE), ZERO)
        held = reserved.get(w.pk, ZERO) or ZERO
        row = {
            "warehouse": w.pk,
            "name": w.name,
            "code": w.code,
            "branch": w.branch.name if w.branch_id else None,
            "type": w.warehouse_type,
            "on_hand": on_hand,
            "reserved": held,
            "free": on_hand - held,
            "on_order": on_order.get(w.pk, ZERO),
            "in_transit": on_the_way.get(w.pk, ZERO),
        }
        if w.warehouse_type == Warehouse.WarehouseType.TRANSIT and not any(row[k] for k in totals):
            continue  # the in-transit warehouse is shown through "in transit" instead
        for k in totals:
            totals[k] += row[k]
        rows.append({**row, **{k: _qty(row[k]) for k in totals}})

    moves = (
        StockMove.objects.filter(product=product, company=company)
        .filter(Q(warehouse_id__in=ids))
        .select_related("warehouse")
        .order_by("-date", "-id")[:25]
    )
    return {
        "id": product.pk,
        "sku": product.sku,
        "name": product.name,
        "name_ar": product.name_ar,
        "uom": product.uom.code,
        "category": product.category.name if product.category_id else None,
        "image": image_url(product, "image"),
        "barcode": product.barcode,
        "price": str(price_row.unit_price) if price_row else None,
        "price_list": price_list.name if price_list else None,
        "currency": company.currency_id,
        "warehouses": rows,
        "totals": {k: _qty(v) for k, v in totals.items()},
        "incoming": incoming,
        "moves": [
            {
                "id": m.pk,
                "date": m.date,
                "warehouse": m.warehouse.name,
                "move_type": m.get_move_type_display(),
                "status": m.status,
                "quantity": _qty(m.quantity),
                "reference": m.reference,
            }
            for m in moves
        ],
    }
