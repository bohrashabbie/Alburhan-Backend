from datetime import date
from decimal import Decimal

from django.db.models import Q

from .models import PriceList, PriceListItem, Product


def active_price_list_q(on_date: date) -> Q:
    return (
        Q(is_active=True)
        & (Q(valid_from__isnull=True) | Q(valid_from__lte=on_date))
        & (Q(valid_to__isnull=True) | Q(valid_to__gte=on_date))
    )


def get_unit_price(
    price_list: PriceList, product: Product, quantity: Decimal, on_date: date
) -> Decimal | None:
    """Price for `quantity` units: the row with the highest min_quantity not above `quantity`.
    None if the list isn't valid on that date or has no price for the product."""
    if not PriceList.objects.filter(pk=price_list.pk).filter(active_price_list_q(on_date)).exists():
        return None
    item = (
        PriceListItem.objects.filter(
            price_list=price_list, product=product, min_quantity__lte=quantity
        )
        .order_by("-min_quantity")
        .first()
    )
    return item.unit_price if item else None
