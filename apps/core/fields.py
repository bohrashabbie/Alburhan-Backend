"""Standard numeric field definitions so every module stores money and quantities identically.

KWD has 3 decimal places, so money is stored with 3 decimals everywhere; amounts in 2-decimal
currencies (AED, CNY, EGP, USD) are rounded with `round_money` before saving.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db import models

MONEY_MAX_DIGITS = 18
MONEY_DECIMAL_PLACES = 3
QUANTITY_DECIMAL_PLACES = 4
RATE_DECIMAL_PLACES = 8


class MoneyField(models.DecimalField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", MONEY_MAX_DIGITS)
        kwargs.setdefault("decimal_places", MONEY_DECIMAL_PLACES)
        kwargs.setdefault("default", Decimal("0"))
        super().__init__(*args, **kwargs)


class QuantityField(models.DecimalField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 18)
        kwargs.setdefault("decimal_places", QUANTITY_DECIMAL_PLACES)
        kwargs.setdefault("default", Decimal("0"))
        super().__init__(*args, **kwargs)


class RateField(models.DecimalField):
    """Exchange rates and conversion factors."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("max_digits", 20)
        kwargs.setdefault("decimal_places", RATE_DECIMAL_PLACES)
        super().__init__(*args, **kwargs)


def round_money(amount: Decimal, decimal_places: int) -> Decimal:
    """Round half-up to the currency's minor unit (e.g. 3 for KWD, 2 for AED)."""
    quantum = Decimal(1).scaleb(-decimal_places)
    return Decimal(amount).quantize(quantum, rounding=ROUND_HALF_UP)
