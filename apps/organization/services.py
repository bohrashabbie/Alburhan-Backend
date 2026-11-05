from datetime import date
from decimal import Decimal

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from apps.core.fields import round_money

from .models import Company, Currency, DocumentSequence, ExchangeRate


class ExchangeRateMissing(Exception):
    pass


RATES_VERSION_KEY = "fx:version"
CURRENCY_DECIMALS_KEY = "currency:decimals"


def _rates_version() -> int:
    version = cache.get(RATES_VERSION_KEY)
    if version is None:
        cache.add(RATES_VERSION_KEY, 1, timeout=None)
        version = cache.get(RATES_VERSION_KEY, 1)
    return version


def invalidate_rates() -> None:
    def bump():
        try:
            cache.incr(RATES_VERSION_KEY)
        except ValueError:
            cache.set(RATES_VERSION_KEY, 2, timeout=None)

    transaction.on_commit(bump)


def invalidate_currencies() -> None:
    transaction.on_commit(lambda: cache.delete(CURRENCY_DECIMALS_KEY))


def currency_decimals(code: str) -> int:
    """Minor units per currency, cached (they almost never change)."""
    table = cache.get(CURRENCY_DECIMALS_KEY)
    if table is None:
        table = dict(Currency.objects.values_list("code", "decimal_places"))
        cache.set(CURRENCY_DECIMALS_KEY, table, timeout=None)
    return table[code]


def get_exchange_rate(from_code: str, to_code: str, on_date: date) -> Decimal:
    """Latest rate effective on or before `on_date`. Falls back to the inverse of a stored rate.
    Results are cached per currency pair and day until any rate changes."""
    if from_code == to_code:
        return Decimal(1)
    key = f"fx:v{_rates_version()}:{from_code}:{to_code}:{on_date.isoformat()}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    rate = _lookup_exchange_rate(from_code, to_code, on_date)
    cache.set(key, rate, timeout=60 * 60 * 24)
    return rate


def _lookup_exchange_rate(from_code: str, to_code: str, on_date: date) -> Decimal:

    direct = (
        ExchangeRate.objects.filter(
            currency_id=from_code, to_currency_id=to_code, effective_date__lte=on_date
        )
        .order_by("-effective_date")
        .first()
    )
    inverse = (
        ExchangeRate.objects.filter(
            currency_id=to_code, to_currency_id=from_code, effective_date__lte=on_date
        )
        .order_by("-effective_date")
        .first()
    )
    # Prefer whichever was published more recently; the direct rate wins a tie.
    if direct and (not inverse or direct.effective_date >= inverse.effective_date):
        return direct.rate
    if inverse:
        return Decimal(1) / inverse.rate
    raise ExchangeRateMissing(f"No exchange rate {from_code}->{to_code} on or before {on_date}.")


def convert_amount(amount: Decimal, from_code: str, to_code: str, on_date: date) -> Decimal:
    """Convert and round to the target currency's minor unit."""
    rate = get_exchange_rate(from_code, to_code, on_date)
    return round_money(Decimal(amount) * rate, currency_decimals(to_code))


def next_document_number(
    company: Company, doc_type: str, on_date: date | None = None, *, yearly: bool = True
) -> str:
    """Issue the next number for a document type, creating the sequence on first use.

    `yearly` only applies when the sequence is first created: documents (INV, QT…) restart each
    year (KW-INV-2026-00001); master records (CUS, SUP) never do (KW-CUS-00001).
    Must run inside the caller's transaction so a rolled-back document does not consume a number.
    """
    on_date = on_date or timezone.localdate()
    # No savepoint of its own: two queries per number once the sequence exists.
    with transaction.atomic(savepoint=False):
        locked = DocumentSequence.objects.select_for_update()
        try:
            seq = locked.get(company=company, doc_type=doc_type)
        except DocumentSequence.DoesNotExist:
            DocumentSequence.objects.get_or_create(
                company=company,
                doc_type=doc_type,
                defaults={"prefix": f"{company.code}-{doc_type}-", "reset_yearly": yearly},
            )
            seq = locked.get(company=company, doc_type=doc_type)

        if seq.reset_yearly and seq.current_year != on_date.year:
            seq.current_year = on_date.year
            seq.next_number = 1

        number = seq.next_number
        seq.next_number = number + 1
        seq.save(update_fields=["current_year", "next_number", "updated_at"])

    year_part = f"{seq.current_year}-" if seq.reset_yearly else ""
    return f"{seq.prefix}{year_part}{number:0{seq.padding}d}"
