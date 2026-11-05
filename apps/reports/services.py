"""Management dashboard, financial statements, ageing and country compliance outputs.
Everything is computed from posted documents and journal entries in the company currency."""

from datetime import date, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

from django.db.models import DecimalField, ExpressionWrapper, F, Sum
from django.utils import timezone

from apps.accounting.models import Account, AccountType, Journal, JournalEntry, JournalLine
from apps.core.fields import round_money
from apps.inventory.models import ProductCost
from apps.organization.models import Company
from apps.organization.services import currency_decimals
from apps.purchasing.models import PurchaseOrder, SupplierInvoice
from apps.sales.models import CreditNote, Invoice, InvoiceLine

ZERO = Decimal("0")
AMOUNT = DecimalField(max_digits=24, decimal_places=6)
BASE = ExpressionWrapper(F("subtotal") * F("exchange_rate"), output_field=AMOUNT)
TAX_BASE = ExpressionWrapper(F("tax_total") * F("exchange_rate"), output_field=AMOUNT)
DUE_BASE = ExpressionWrapper(
    (F("total") - F("amount_paid") - F("amount_credited")) * F("exchange_rate"), output_field=AMOUNT
)
BILL_DUE = ExpressionWrapper(
    (F("total") - F("amount_paid")) * F("exchange_rate"), output_field=AMOUNT
)
OPEN_SALES = (Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID)
BOOKED_SALES = (Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID)
OPEN_BILLS = (SupplierInvoice.Status.POSTED, SupplierInvoice.Status.PARTIALLY_PAID)
BOOKED_BILLS = (*OPEN_BILLS, SupplierInvoice.Status.PAID)


def _money(company: Company):
    decimals = currency_decimals(company.currency_id)
    return lambda value: str(round_money(value or ZERO, decimals))


def _posted_lines(company: Company):
    return JournalLine.objects.filter(
        entry__company=company, entry__status=JournalEntry.Status.POSTED
    )


# --- Dashboard ------------------------------------------------------------------------------------


def dashboard(company: Company, today: date | None = None, branch_ids=None) -> dict:
    today = today or timezone.localdate()
    money = _money(company)
    month_start = today.replace(day=1)
    prev_month_end = month_start - timedelta(days=1)
    prev_month_start = prev_month_end.replace(day=1)
    invoices = Invoice.objects.filter(company=company, status__in=BOOKED_SALES)
    if branch_ids:
        invoices = invoices.filter(branch_id__in=branch_ids)

    def booked(start, end):
        window = invoices.filter(date__gte=start, date__lte=end)
        return window.aggregate(amount=Sum(BASE, default=ZERO))["amount"] or ZERO, window.count()

    today_amount, today_count = booked(today, today)
    month_amount, month_count = booked(month_start, today)
    prev_amount, _ = booked(prev_month_start, prev_month_end)
    series_start = today - timedelta(days=29)
    daily = {
        row["date"].isoformat(): row["amount"]
        for row in invoices.filter(date__gte=series_start, date__lte=today)
        .values("date")
        .annotate(amount=Sum(BASE, default=ZERO))
    }
    sales_series = []
    for i in range(30):
        day = (series_start + timedelta(days=i)).isoformat()
        sales_series.append({"date": day, "amount": money(daily.get(day, ZERO))})
    by_branch = [
        {"branch": row["branch_id"], "name": row["branch__name"], "amount": money(row["amount"])}
        for row in invoices.filter(date__gte=month_start, date__lte=today)
        .values("branch_id", "branch__name")
        .annotate(amount=Sum(BASE, default=ZERO))
        .order_by("-amount")
    ]
    line_base = ExpressionWrapper(
        F("net_amount") * F("invoice__exchange_rate"), output_field=AMOUNT
    )
    top_products = [
        {
            "product": row["product_id"],
            "sku": row["product__sku"],
            "name": row["product__name"],
            "quantity": f"{row['quantity'].normalize():f}",
            "amount": money(row["amount"]),
        }
        for row in InvoiceLine.objects.filter(
            invoice__in=invoices.filter(date__gte=month_start, date__lte=today)
        )
        .values("product_id", "product__sku", "product__name")
        .annotate(quantity=Sum("quantity", default=ZERO), amount=Sum(line_base, default=ZERO))
        .order_by("-amount")[:6]
    ]

    open_invoices = Invoice.objects.filter(company=company, status__in=OPEN_SALES)
    receivable = open_invoices.aggregate(due=Sum(DUE_BASE, default=ZERO))["due"]
    overdue_qs = open_invoices.filter(due_date__lt=today)
    overdue = overdue_qs.aggregate(due=Sum(DUE_BASE, default=ZERO))["due"]
    open_bills = SupplierInvoice.objects.filter(company=company, status__in=OPEN_BILLS)
    payable = open_bills.aggregate(due=Sum(BILL_DUE, default=ZERO))["due"]
    bills_overdue = open_bills.filter(due_date__lt=today).aggregate(
        due=Sum(BILL_DUE, default=ZERO)
    )["due"]

    cash_accounts = list(
        Journal.objects.filter(
            company=company, journal_type__in=("cash", "bank"), default_account__isnull=False
        ).values_list("default_account_id", flat=True)
    )
    cash_rows = list(
        _posted_lines(company)
        .filter(account_id__in=cash_accounts, entry__date__lte=today)
        .values("account_id", "account__code", "account__name")
        .annotate(balance=Sum(F("debit_base") - F("credit_base"), default=ZERO))
        .order_by("account__code")
    )
    cash_total = sum((r["balance"] for r in cash_rows), ZERO)
    stock_value = ProductCost.objects.filter(company=company).aggregate(
        v=Sum("value", default=ZERO)
    )["v"]

    from apps.installation.models import WorkOrder
    from apps.pos.models import Shift
    from apps.projects.models import Project

    projects = Project.objects.filter(company=company, status__in=("won", "in_progress"))
    open_orders = PurchaseOrder.objects.filter(company=company, status__in=("sent", "partial"))
    po_value = ExpressionWrapper(F("total") * F("exchange_rate"), output_field=AMOUNT)
    change = (
        str(round((month_amount - prev_amount) / prev_amount * 100, 1)) if prev_amount else None
    )
    return {
        "currency": company.currency_id,
        "today": today.isoformat(),
        "sales": {
            "today": money(today_amount),
            "today_count": today_count,
            "month": money(month_amount),
            "month_count": month_count,
            "previous_month": money(prev_amount),
            "change_percent": change,
            "series": sales_series,
            "by_branch": by_branch,
            "top_products": top_products,
        },
        "receivables": {
            "total": money(receivable),
            "count": open_invoices.count(),
            "overdue": money(overdue),
            "overdue_count": overdue_qs.count(),
        },
        "payables": {
            "total": money(payable),
            "count": open_bills.count(),
            "overdue": money(bills_overdue),
        },
        "cash": {
            "total": money(cash_total),
            "accounts": [
                {
                    "account": r["account_id"],
                    "code": r["account__code"],
                    "name": r["account__name"],
                    "balance": money(r["balance"]),
                }
                for r in cash_rows
            ],
        },
        "stock_value": money(stock_value),
        "projects": {
            "active": projects.count(),
            "contract_value": money(projects.aggregate(v=Sum("contract_value", default=ZERO))["v"]),
        },
        "jobs_today": WorkOrder.objects.filter(company=company, scheduled_date=today)
        .exclude(status="cancelled")
        .count(),
        "open_shifts": Shift.objects.filter(company=company, status="open").count(),
        "purchase_orders": {
            "open": open_orders.count(),
            "value": money(open_orders.aggregate(v=Sum(po_value, default=ZERO))["v"]),
        },
    }


# --- Financial statements -------------------------------------------------------------------------


def _top_group(account: Account, by_id: dict) -> Account:
    node = account
    while node.parent_id and node.parent_id in by_id:
        node = by_id[node.parent_id]
    return node


def _balances(company: Company, date_from: date | None, date_to: date) -> dict[int, Decimal]:
    """Debit-positive balance per account over the window (all history when date_from is None)."""
    lines = _posted_lines(company).filter(entry__date__lte=date_to)
    if date_from:
        lines = lines.filter(entry__date__gte=date_from)
    return {
        row["account_id"]: row["dr"] - row["cr"]
        for row in lines.values("account_id").annotate(
            dr=Sum("debit_base", default=ZERO), cr=Sum("credit_base", default=ZERO)
        )
    }


def _grouped(accounts: dict, balances: dict[int, Decimal], types: tuple, sign: int) -> list[dict]:
    """Rows per account under their top-level group, as positive 'natural' amounts."""
    groups: dict[int, dict] = {}
    for account_id, balance in balances.items():
        account = accounts.get(account_id)
        if account is None or account.account_type not in types:
            continue
        amount = balance * sign
        if amount == 0:
            continue
        top = _top_group(account, accounts)
        group = groups.setdefault(
            top.pk,
            {"code": top.code, "name": top.name, "name_ar": top.name_ar, "rows": [], "total": ZERO},
        )
        group["rows"].append(
            {
                "account": account.pk,
                "code": account.code,
                "name": account.name,
                "name_ar": account.name_ar,
                "amount": amount,
            }
        )
        group["total"] += amount
    result = sorted(groups.values(), key=lambda g: g["code"])
    for group in result:
        group["rows"].sort(key=lambda r: r["code"])
    return result


def _stringify(groups: list[dict], money) -> list[dict]:
    for group in groups:
        group["total"] = money(group["total"])
        for row in group["rows"]:
            row["amount"] = money(row["amount"])
    return groups


def profit_and_loss(company: Company, date_from: date, date_to: date) -> dict:
    money = _money(company)
    accounts = {a.pk: a for a in Account.objects.filter(company=company)}
    balances = _balances(company, date_from, date_to)
    income = _grouped(accounts, balances, (AccountType.INCOME,), -1)
    expenses = _grouped(accounts, balances, (AccountType.EXPENSE,), 1)
    cost_groups = [g for g in expenses if g["code"].startswith("5")]
    operating_groups = [g for g in expenses if not g["code"].startswith("5")]
    total_income = sum((g["total"] for g in income), ZERO)
    cost_of_sales = sum((g["total"] for g in cost_groups), ZERO)
    operating = sum((g["total"] for g in operating_groups), ZERO)
    gross = total_income - cost_of_sales
    net = gross - operating
    return {
        "currency": company.currency_id,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "income": _stringify(income, money),
        "cost_of_sales": _stringify(cost_groups, money),
        "expenses": _stringify(operating_groups, money),
        "totals": {
            "income": money(total_income),
            "cost_of_sales": money(cost_of_sales),
            "gross_profit": money(gross),
            "expenses": money(operating),
            "net_profit": money(net),
            "gross_margin_percent": str(round(gross / total_income * 100, 1))
            if total_income
            else None,
        },
    }


def balance_sheet(company: Company, as_of: date) -> dict:
    money = _money(company)
    accounts = {a.pk: a for a in Account.objects.filter(company=company)}
    balances = _balances(company, None, as_of)
    assets = _grouped(accounts, balances, (AccountType.ASSET,), 1)
    liabilities = _grouped(accounts, balances, (AccountType.LIABILITY,), -1)
    equity = _grouped(accounts, balances, (AccountType.EQUITY,), -1)
    # Income and expense balances not yet closed to retained earnings: credit-positive.
    earnings = ZERO
    for account_id, balance in balances.items():
        account = accounts.get(account_id)
        if account and account.account_type in (AccountType.INCOME, AccountType.EXPENSE):
            earnings -= balance
    total_assets = sum((g["total"] for g in assets), ZERO)
    total_liabilities = sum((g["total"] for g in liabilities), ZERO)
    total_equity = sum((g["total"] for g in equity), ZERO) + earnings
    decimals = currency_decimals(company.currency_id)
    return {
        "currency": company.currency_id,
        "as_of": as_of.isoformat(),
        "assets": _stringify(assets, money),
        "liabilities": _stringify(liabilities, money),
        "equity": _stringify(equity, money),
        "current_earnings": money(earnings),
        "totals": {
            "assets": money(total_assets),
            "liabilities": money(total_liabilities),
            "equity": money(total_equity),
            "liabilities_and_equity": money(total_liabilities + total_equity),
            "balanced": round_money(total_assets, decimals)
            == round_money(total_liabilities + total_equity, decimals),
        },
    }


# --- Ageing -----------------------------------------------------------------------------------------

BUCKETS = ["current", "1_30", "31_60", "61_90", "over_90"]


def _bucket(days: int) -> str:
    if days <= 0:
        return "current"
    if days <= 30:
        return "1_30"
    if days <= 60:
        return "31_60"
    if days <= 90:
        return "61_90"
    return "over_90"


def ageing(company: Company, kind: str, as_of: date) -> dict:
    money = _money(company)
    if kind == "receivable":
        docs = Invoice.objects.filter(
            company=company, status__in=OPEN_SALES, date__lte=as_of
        ).select_related("customer")
        partners = [(doc, doc.customer) for doc in docs]
    else:
        docs = SupplierInvoice.objects.filter(
            company=company, status__in=OPEN_BILLS, date__lte=as_of
        ).select_related("supplier")
        partners = [(doc, doc.supplier) for doc in docs]
    rows: dict[int, dict] = {}
    totals = dict.fromkeys(BUCKETS, ZERO)
    for doc, partner in partners:
        bucket = _bucket((as_of - (doc.due_date or doc.date)).days)
        amount = doc.amount_due * doc.exchange_rate
        row = rows.setdefault(
            partner.pk,
            {
                "partner": partner.pk,
                "code": partner.code,
                "name": partner.name,
                "count": 0,
                "total": ZERO,
                **dict.fromkeys(BUCKETS, ZERO),
            },
        )
        row[bucket] += amount
        row["total"] += amount
        row["count"] += 1
        totals[bucket] += amount
    result = sorted(rows.values(), key=lambda r: -r["total"])
    for row in result:
        for key in [*BUCKETS, "total"]:
            row[key] = money(row[key])
    grand = sum(totals.values(), ZERO)
    return {
        "currency": company.currency_id,
        "kind": kind,
        "as_of": as_of.isoformat(),
        "rows": result,
        "totals": {**{k: money(v) for k, v in totals.items()}, "total": money(grand)},
    }


# --- Compliance ------------------------------------------------------------------------------------


def vat_return(company: Company, date_from: date, date_to: date) -> dict:
    """VAT summary for a period (UAE VAT 201 boxes / Egypt return): outputs from sales and credit
    notes, inputs from supplier bills, all in the company currency."""
    money = _money(company)
    sales = Invoice.objects.filter(
        company=company, status__in=BOOKED_SALES, date__gte=date_from, date__lte=date_to
    )
    standard = sales.filter(tax_total__gt=0).aggregate(
        net=Sum(BASE, default=ZERO), tax=Sum(TAX_BASE, default=ZERO)
    )
    zero_rated = sales.filter(tax_total=0).aggregate(net=Sum(BASE, default=ZERO))["net"]
    credits = CreditNote.objects.filter(
        company=company, status="posted", date__gte=date_from, date__lte=date_to
    ).aggregate(net=Sum(BASE, default=ZERO), tax=Sum(TAX_BASE, default=ZERO))
    bills = SupplierInvoice.objects.filter(
        company=company, status__in=BOOKED_BILLS, date__gte=date_from, date__lte=date_to
    )
    purchases = bills.filter(tax_total__gt=0).aggregate(
        net=Sum(BASE, default=ZERO), tax=Sum(TAX_BASE, default=ZERO)
    )
    untaxed = bills.filter(tax_total=0).aggregate(net=Sum(BASE, default=ZERO))["net"]
    output_tax = standard["tax"] - credits["tax"]
    input_tax = purchases["tax"]
    by_branch = [
        {
            "branch": r["branch_id"],
            "name": r["branch__name"],
            "net": money(r["net"]),
            "tax": money(r["tax"]),
        }
        for r in sales.values("branch_id", "branch__name")
        .annotate(net=Sum(BASE, default=ZERO), tax=Sum(TAX_BASE, default=ZERO))
        .order_by("branch__name")
    ]
    return {
        "currency": company.currency_id,
        "country": company.country_id,
        "tax_registration_number": company.tax_registration_number,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "sales": {
            "standard_rated_net": money(standard["net"] - credits["net"]),
            "standard_rated_tax": money(output_tax),
            "zero_rated_net": money(zero_rated),
            "credit_notes_net": money(credits["net"]),
            "credit_notes_tax": money(credits["tax"]),
            "by_branch": by_branch,
        },
        "purchases": {
            "taxable_net": money(purchases["net"]),
            "input_tax": money(input_tax),
            "untaxed_net": money(untaxed),
        },
        "totals": {
            "output_tax": money(output_tax),
            "input_tax": money(input_tax),
            "net_payable": money(output_tax - input_tax),
        },
    }


def xbrl_instance(company: Company, date_from: date, date_to: date) -> str:
    """A minimal IFRS-taxonomy XBRL instance with the headline figures (Kuwait MOCI/CMA filing)."""
    pnl = profit_and_loss(company, date_from, date_to)
    bs = balance_sheet(company, date_to)
    entity = escape(company.commercial_registration_number or company.code)
    scheme = "http://www.moci.gov.kw"
    duration = (
        f'<xbrli:context id="D"><xbrli:entity><xbrli:identifier scheme="{scheme}">{entity}</xbrli:identifier>'
        f"</xbrli:entity><xbrli:period><xbrli:startDate>{date_from.isoformat()}</xbrli:startDate>"
        f"<xbrli:endDate>{date_to.isoformat()}</xbrli:endDate></xbrli:period></xbrli:context>"
    )
    instant = (
        f'<xbrli:context id="I"><xbrli:entity><xbrli:identifier scheme="{scheme}">{entity}</xbrli:identifier>'
        f"</xbrli:entity><xbrli:period><xbrli:instant>{date_to.isoformat()}</xbrli:instant></xbrli:period></xbrli:context>"
    )
    currency = company.currency_id
    unit = f'<xbrli:unit id="{currency}"><xbrli:measure>iso4217:{currency}</xbrli:measure></xbrli:unit>'
    decimals = currency_decimals(currency)

    def fact(name, context, value):
        return f'<ifrs-full:{name} contextRef="{context}" unitRef="{currency}" decimals="{decimals}">{value}</ifrs-full:{name}>'

    facts = [
        fact("Revenue", "D", pnl["totals"]["income"]),
        fact("CostOfSales", "D", pnl["totals"]["cost_of_sales"]),
        fact("GrossProfit", "D", pnl["totals"]["gross_profit"]),
        fact("OperatingExpense", "D", pnl["totals"]["expenses"]),
        fact("ProfitLoss", "D", pnl["totals"]["net_profit"]),
        fact("Assets", "I", bs["totals"]["assets"]),
        fact("Liabilities", "I", bs["totals"]["liabilities"]),
        fact("Equity", "I", bs["totals"]["equity"]),
        fact("EquityAndLiabilities", "I", bs["totals"]["liabilities_and_equity"]),
    ]
    for group in bs["assets"]:
        if group["code"].startswith("11"):
            facts.append(fact("CurrentAssets", "I", group["total"]))
        elif group["code"].startswith("12"):
            facts.append(fact("NoncurrentAssets", "I", group["total"]))
    for group in bs["liabilities"]:
        if group["code"].startswith("21"):
            facts.append(fact("CurrentLiabilities", "I", group["total"]))
        elif group["code"].startswith("22"):
            facts.append(fact("NoncurrentLiabilities", "I", group["total"]))
    body = "\n  ".join([duration, instant, unit, *facts])
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:link="http://www.xbrl.org/2003/linkbase" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" xmlns:iso4217="http://www.xbrl.org/2003/iso4217" '
        'xmlns:ifrs-full="https://xbrl.ifrs.org/taxonomy/2024-03-27/ifrs-full">\n'
        '  <link:schemaRef xlink:type="simple" '
        'xlink:href="https://xbrl.ifrs.org/taxonomy/2024-03-27/full_ifrs_entry_point_2024-03-27.xsd"/>\n'
        f"  {body}\n</xbrli:xbrl>\n"
    )


def egypt_einvoice(invoice: Invoice) -> dict:
    """An unsigned ETA (Egyptian Tax Authority) e-invoice document, version 1.0, ready for signing
    and submission with the company's ETA certificate."""
    company = invoice.company
    customer = invoice.customer

    def amount(value) -> float:
        return float(round_money(value, 5))

    lines = []
    gross_total = ZERO
    for line in invoice.lines.select_related("product"):
        gross = line.quantity * line.unit_price
        gross_total += gross
        rate = float(line.tax_rate)
        lines.append(
            {
                "description": line.description or line.product.name,
                "itemType": "EGS",
                "itemCode": f"EG-{company.tax_registration_number or company.code}-{line.product.sku}",
                "unitType": "EA",
                "quantity": float(line.quantity),
                "internalCode": line.product.sku,
                "unitValue": {
                    "currencySold": invoice.currency_id,
                    "amountEGP": amount(line.unit_price * invoice.exchange_rate),
                    "amountSold": amount(line.unit_price),
                    "currencyExchangeRate": float(invoice.exchange_rate),
                },
                "salesTotal": amount(gross),
                "total": amount(line.total_amount),
                "valueDifference": 0,
                "totalTaxableFees": 0,
                "netTotal": amount(line.net_amount),
                "itemsDiscount": 0,
                "discount": {
                    "rate": float(line.discount_percent),
                    "amount": amount(gross - line.net_amount),
                },
                "taxableItems": [
                    {
                        "taxType": "T1",
                        "amount": amount(line.tax_amount),
                        "subType": "V009" if rate > 0 else "V003",
                        "rate": rate,
                    }
                ],
            }
        )
    issued = (
        (invoice.posted_at or timezone.now())
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )
    return {
        "issuer": {
            "type": "B",
            "id": company.tax_registration_number,
            "name": company.legal_name or company.name,
            "address": {
                "branchID": "0",
                "country": "EG",
                "governate": "",
                "regionCity": "",
                "street": company.address,
                "buildingNumber": "",
            },
        },
        "receiver": {
            "type": "P" if customer.customer_type == "individual" else "B",
            "id": customer.tax_number or customer.civil_id,
            "name": customer.name,
            "address": {
                "country": "EG",
                "governate": "",
                "regionCity": customer.area,
                "street": customer.address,
                "buildingNumber": "",
            },
        },
        "documentType": "I",
        "documentTypeVersion": "1.0",
        "dateTimeIssued": issued,
        "taxpayerActivityCode": "4759",
        "internalID": invoice.number,
        "purchaseOrderReference": invoice.reference,
        "invoiceLines": lines,
        "totalDiscountAmount": amount(gross_total - invoice.subtotal),
        "totalSalesAmount": amount(gross_total),
        "netAmount": amount(invoice.subtotal),
        "taxTotals": [{"taxType": "T1", "amount": amount(invoice.tax_total)}],
        "totalAmount": amount(invoice.total),
        "extraDiscountAmount": 0,
        "totalItemsDiscountAmount": 0,
        "signatures": [],
        "note": "Unsigned. Sign with the company's ETA certificate and submit through the ETA API.",
    }
