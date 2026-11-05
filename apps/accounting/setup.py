"""Starting chart of accounts, journals, default-account mapping and monthly periods for a company.
The chart suits a lighting trading, projects and installation business; edit it freely afterwards."""

import calendar
from datetime import date

from django.db import transaction

from apps.organization.models import Company

from .models import Account, AccountingPeriod, AccountingSettings, AccountType, Journal

A, L, E, INC, X = (
    AccountType.ASSET,
    AccountType.LIABILITY,
    AccountType.EQUITY,
    AccountType.INCOME,
    AccountType.EXPENSE,
)

# code, name, Arabic name, type, parent code, is_group, reconcilable
CHART = [
    ("1000", "Assets", "الأصول", A, None, True, False),
    ("1100", "Current assets", "الأصول المتداولة", A, "1000", True, False),
    ("1110", "Cash on hand", "النقدية بالصندوق", A, "1100", False, False),
    ("1120", "Bank - main account", "البنك - الحساب الرئيسي", A, "1100", False, False),
    ("1130", "Accounts receivable", "الذمم المدينة", A, "1100", False, True),
    ("1140", "Inventory", "المخزون", A, "1100", False, False),
    ("1150", "Advances to suppliers", "دفعات مقدمة للموردين", A, "1100", False, True),
    ("1160", "VAT receivable", "ضريبة القيمة المضافة المدينة", A, "1100", False, False),
    ("1170", "Prepaid expenses", "مصروفات مدفوعة مقدماً", A, "1100", False, False),
    ("1180", "Due from group companies", "مستحق من شركات المجموعة", A, "1100", False, False),
    ("1200", "Non-current assets", "الأصول غير المتداولة", A, "1000", True, False),
    ("1210", "Showroom fit-out and furniture", "تجهيزات المعارض والأثاث", A, "1200", False, False),
    ("1220", "Vehicles", "السيارات", A, "1200", False, False),
    ("1230", "Tools and equipment", "العدد والمعدات", A, "1200", False, False),
    ("1290", "Accumulated depreciation", "مجمع الإهلاك", A, "1200", False, False),
    ("2000", "Liabilities", "الخصوم", L, None, True, False),
    ("2100", "Current liabilities", "الخصوم المتداولة", L, "2000", True, False),
    ("2110", "Accounts payable", "الذمم الدائنة", L, "2100", False, True),
    ("2120", "Customer advances", "دفعات مقدمة من العملاء", L, "2100", False, True),
    ("2130", "VAT payable", "ضريبة القيمة المضافة المستحقة", L, "2100", False, False),
    ("2140", "Accrued expenses", "مصروفات مستحقة", L, "2100", False, False),
    ("2150", "Salaries payable", "رواتب مستحقة", L, "2100", False, False),
    ("2160", "Due to group companies", "مستحق لشركات المجموعة", L, "2100", False, False),
    ("2170", "Goods received not invoiced", "بضاعة مستلمة غير مفوترة", L, "2100", False, False),
    ("2200", "Non-current liabilities", "الخصوم غير المتداولة", L, "2000", True, False),
    ("2210", "End-of-service benefits", "مكافأة نهاية الخدمة", L, "2200", False, False),
    ("3000", "Equity", "حقوق الملكية", E, None, True, False),
    ("3100", "Share capital", "رأس المال", E, "3000", False, False),
    ("3200", "Retained earnings", "الأرباح المحتجزة", E, "3000", False, False),
    ("3300", "Owners' current account", "جاري الشركاء", E, "3000", False, False),
    ("4000", "Income", "الإيرادات", INC, None, True, False),
    ("4100", "Showroom sales", "مبيعات المعارض", INC, "4000", False, False),
    ("4200", "Project sales", "مبيعات المشاريع", INC, "4000", False, False),
    ("4300", "Installation income", "إيرادات التركيب", INC, "4000", False, False),
    ("4400", "Sales returns and discounts", "مردودات وخصومات المبيعات", INC, "4000", False, False),
    ("4500", "Other income", "إيرادات أخرى", INC, "4000", False, False),
    ("5000", "Cost of sales", "تكلفة المبيعات", X, None, True, False),
    ("5100", "Cost of goods sold", "تكلفة البضاعة المباعة", X, "5000", False, False),
    ("5200", "Freight and customs", "الشحن والجمارك", X, "5000", False, False),
    (
        "5300",
        "Installation materials and subcontractors",
        "مواد ومقاولو التركيب",
        X,
        "5000",
        False,
        False,
    ),
    ("5400", "Inventory adjustments", "تسويات المخزون", X, "5000", False, False),
    ("6000", "Operating expenses", "المصروفات التشغيلية", X, None, True, False),
    ("6100", "Salaries and wages", "الرواتب والأجور", X, "6000", False, False),
    ("6110", "Staff benefits", "مزايا الموظفين", X, "6000", False, False),
    ("6200", "Showroom rent", "إيجار المعارض", X, "6000", False, False),
    ("6210", "Utilities", "المرافق", X, "6000", False, False),
    ("6300", "Marketing", "التسويق", X, "6000", False, False),
    ("6400", "Transport and vehicles", "النقل والسيارات", X, "6000", False, False),
    ("6500", "Bank charges", "مصاريف بنكية", X, "6000", False, False),
    ("6600", "Depreciation", "الإهلاك", X, "6000", False, False),
    ("6700", "Professional fees", "أتعاب مهنية", X, "6000", False, False),
    ("6800", "Exchange gains and losses", "فروقات أسعار الصرف", X, "6000", False, False),
    ("6900", "Other expenses", "مصروفات أخرى", X, "6000", False, False),
]

SETTINGS_MAP = {
    "receivable": "1130",
    "payable": "2110",
    "customer_advances": "2120",
    "supplier_advances": "1150",
    "grni": "2170",
    "purchase_expenses": "5200",
    "sales": "4100",
    "sales_returns": "4400",
    "installation_income": "4300",
    "inventory": "1140",
    "cost_of_sales": "5100",
    "inventory_adjustment": "5400",
    "vat_output": "2130",
    "vat_input": "1160",
    "fx_difference": "6800",
    "retained_earnings": "3200",
}

JOURNALS = [
    # code, name, Arabic, type, default account
    ("JV", "General journal", "قيود عامة", Journal.JournalType.GENERAL, None),
    ("SI", "Sales journal", "دفتر المبيعات", Journal.JournalType.SALES, None),
    ("PI", "Purchases journal", "دفتر المشتريات", Journal.JournalType.PURCHASE, None),
    ("CS", "Cash journal", "دفتر الصندوق", Journal.JournalType.CASH, "1110"),
    ("BK", "Bank journal", "دفتر البنك", Journal.JournalType.BANK, "1120"),
]


def create_year_periods(company: Company, year: int, user=None) -> int:
    """Twelve monthly periods; existing ones are kept. Returns how many were created."""
    made = 0
    for month in range(1, 13):
        _, created = AccountingPeriod.objects.get_or_create(
            company=company,
            name=f"{year}-{month:02d}",
            defaults={
                "start_date": date(year, month, 1),
                "end_date": date(year, month, calendar.monthrange(year, month)[1]),
                "created_by": user,
                "updated_by": user,
            },
        )
        made += created
    return made


@transaction.atomic
def set_up_company(company: Company, year: int, user=None) -> dict:
    """Idempotent: adds only what's missing, never changes what exists."""
    audit = {"created_by": user, "updated_by": user}
    accounts = {a.code: a for a in Account.objects.filter(company=company)}
    created_accounts = 0
    for code, name, name_ar, kind, parent, is_group, reconcilable in CHART:
        if code in accounts:
            continue
        accounts[code] = Account.objects.create(
            company=company,
            code=code,
            name=name,
            name_ar=name_ar,
            account_type=kind,
            parent=accounts.get(parent) if parent else None,
            is_group=is_group,
            is_reconcilable=reconcilable,
            **audit,
        )
        created_accounts += 1

    created_journals = 0
    for code, name, name_ar, kind, default in JOURNALS:
        _, created = Journal.objects.get_or_create(
            company=company,
            code=code,
            defaults={
                "name": name,
                "name_ar": name_ar,
                "journal_type": kind,
                "default_account": accounts.get(default) if default else None,
                **audit,
            },
        )
        created_journals += created

    settings, _ = AccountingSettings.objects.get_or_create(company=company, defaults=audit)
    for field, code in SETTINGS_MAP.items():
        if getattr(settings, f"{field}_id") is None and code in accounts:
            setattr(settings, field, accounts[code])
    settings.updated_by = user
    settings.save()

    periods = create_year_periods(company, year, user)
    return {"accounts": created_accounts, "journals": created_journals, "periods": periods}
