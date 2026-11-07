"""Leave and payroll rules.

Leave: the employee asks → their manager (or HR when there is none) gets an email with a link to
approve or reject → HR and the employee hear the outcome. Approved leave is accounted for in the
month's payroll: unpaid days are deducted at (monthly pay ÷ pay days per month) a day.
"""

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.accounting.models import Account, Journal
from apps.accounting.services import LineSpec, PostingError, create_and_post
from apps.core.fields import round_money
from apps.organization.services import currency_decimals, next_document_number

from . import notify
from .models import Employee, HRSettings, LeaveRequest, LeaveType, PayrollRun, Payslip

ZERO = Decimal("0")
TOKEN_SALT = "hr.leave.decision"
TOKEN_DAYS = 30

# Weekend and the labour-law divisor for a day's pay, per country.
COUNTRY_RULES = {
    "KW": ("4,5", 26),
    "SA": ("4,5", 30),
    "AE": ("5,6", 30),
    "EG": ("4,5", 30),
    "CN": ("5,6", 22),
}

DEFAULT_TYPES = [
    # code, name, Arabic, paid, uses annual balance, yearly limit
    ("annual", "Annual leave", "إجازة سنوية", True, True, ZERO),
    ("sick", "Sick leave", "إجازة مرضية", True, False, Decimal("15")),
    ("emergency", "Emergency leave", "إجازة طارئة", True, False, Decimal("5")),
    ("unpaid", "Unpaid leave", "إجازة بدون راتب", False, False, ZERO),
]


class HRError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


# --- Settings ----------------------------------------------------------------------------------


def hr_settings(company) -> HRSettings:
    found = HRSettings.objects.filter(company=company).first()
    if found:
        return found
    weekend, pay_days = COUNTRY_RULES.get(company.country_id, ("5,6", 30))
    accounts = {
        a.code: a for a in Account.objects.filter(company=company, code__in=["6100", "2150"])
    }
    settings, _ = HRSettings.objects.get_or_create(
        company=company,
        defaults={
            "weekend_days": weekend,
            "pay_days_per_month": pay_days,
            "salary_expense_account": accounts.get("6100"),
            "salary_payable_account": accounts.get("2150"),
        },
    )
    ensure_leave_types(company)
    return settings


def ensure_leave_types(company) -> None:
    if LeaveType.objects.filter(company=company).exists():
        return
    LeaveType.objects.bulk_create(
        [
            LeaveType(
                company=company,
                code=code,
                name=name,
                name_ar=name_ar,
                is_paid=paid,
                uses_annual_balance=annual,
                days_per_year=limit,
            )
            for code, name, name_ar, paid, annual, limit in DEFAULT_TYPES
        ]
    )


# --- Days and balances -------------------------------------------------------------------------


def working_days(start: date, end: date, weekend: set[int]) -> Decimal:
    if end < start:
        return ZERO
    count = sum(
        1
        for offset in range((end - start).days + 1)
        if (start + timedelta(days=offset)).weekday() not in weekend
    )
    return Decimal(count)


def _taken(employee: Employee, leave_type: LeaveType, year: int, statuses) -> Decimal:
    query = LeaveRequest.objects.filter(
        employee=employee, start_date__year=year, status__in=statuses
    )
    if leave_type.uses_annual_balance:
        query = query.filter(leave_type__uses_annual_balance=True)
    else:
        query = query.filter(leave_type=leave_type)
    return sum((item.days for item in query), ZERO)


def balances(employee: Employee, year: int | None = None) -> list[dict]:
    """Per leave type: entitlement, taken (approved), pending, and what is left."""
    year = year or timezone.localdate().year
    ensure_leave_types(employee.company)
    rows = []
    for leave_type in LeaveType.objects.filter(company=employee.company, is_active=True):
        if leave_type.uses_annual_balance:
            entitled = employee.annual_leave_days
        elif leave_type.days_per_year > 0:
            entitled = leave_type.days_per_year
        else:
            entitled = None
        taken = _taken(employee, leave_type, year, [LeaveRequest.Status.APPROVED])
        pending = _taken(employee, leave_type, year, [LeaveRequest.Status.PENDING])
        rows.append(
            {
                "leave_type": leave_type.pk,
                "code": leave_type.code,
                "name": leave_type.name,
                "name_ar": leave_type.name_ar,
                "is_paid": leave_type.is_paid,
                "entitled": None if entitled is None else str(entitled),
                "taken": str(taken),
                "pending": str(pending),
                "remaining": None if entitled is None else str(entitled - taken - pending),
            }
        )
    return rows


# --- Leave requests ----------------------------------------------------------------------------


def decision_token(request: LeaveRequest) -> str:
    return signing.dumps({"r": request.pk, "a": request.approver_id or 0}, salt=TOKEN_SALT)


def request_from_token(token: str) -> LeaveRequest:
    try:
        data = signing.loads(token, salt=TOKEN_SALT, max_age=timedelta(days=TOKEN_DAYS))
    except signing.SignatureExpired:
        raise HRError("This link has expired. Open the request in the ERP instead.") from None
    except signing.BadSignature:
        raise HRError("This link is not valid.") from None
    request = (
        LeaveRequest.objects.select_related("employee", "leave_type", "approver", "company")
        .filter(pk=data.get("r"))
        .first()
    )
    if request is None or (request.approver_id or 0) != data.get("a"):
        raise HRError("This link is no longer valid: the request was reassigned.")
    return request


@transaction.atomic
def submit_leave(
    employee: Employee, leave_type: LeaveType, start: date, end: date, reason: str = "", user=None
) -> LeaveRequest:
    if not employee.is_active:
        raise HRError("This employee is not active.")
    if leave_type.company_id != employee.company_id or not leave_type.is_active:
        raise HRError("Choose a leave type of this company.", "leave_type")
    if end < start:
        raise HRError("The last day can't be before the first day.", "end_date")
    settings = hr_settings(employee.company)
    days = working_days(start, end, settings.weekend)
    if days <= 0:
        raise HRError("Those dates are all weekend days.", "end_date")
    clash = LeaveRequest.objects.filter(
        employee=employee,
        status__in=[LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED],
        start_date__lte=end,
        end_date__gte=start,
    ).first()
    if clash:
        raise HRError(f"These dates overlap {clash.number}.", "start_date")
    for row in balances(employee, start.year):
        if row["leave_type"] == leave_type.pk and row["remaining"] is not None:
            if Decimal(row["remaining"]) < days:
                raise HRError(
                    f"Only {Decimal(row['remaining']).normalize():f} days of {leave_type.name.lower()} left this year.",
                    "end_date",
                )
    request = LeaveRequest.objects.create(
        company=employee.company,
        employee=employee,
        leave_type=leave_type,
        start_date=start,
        end_date=end,
        days=days,
        reason=reason.strip(),
        approver=employee.manager if employee.manager_id and employee.manager.is_active else None,
        created_by=user,
        updated_by=user,
    )
    request.number = next_document_number(employee.company, "LV", start)
    request.save(update_fields=["number"])
    transaction.on_commit(lambda: notify.leave_submitted(request, decision_token(request)))
    return request


def can_decide(user, request: LeaveRequest) -> bool:
    """The approving manager decides in the app; HR with the approve permission can too."""
    approver = request.approver
    if approver and approver.user_id and approver.user_id == user.pk:
        return True
    return user.has_erp_permission("hr.leave.approve", company_id=request.company_id)


@transaction.atomic
def decide_leave(
    request: LeaveRequest, approve: bool, by_name: str, via: str, note: str = "", user=None
):
    request = LeaveRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != LeaveRequest.Status.PENDING:
        raise HRError(f"This request was already {request.get_status_display().lower()}.")
    if not approve and not note.strip():
        raise HRError("Give a reason for rejecting.", "note")
    request.status = LeaveRequest.Status.APPROVED if approve else LeaveRequest.Status.REJECTED
    request.decided_by_name = by_name[:160]
    request.decided_at = timezone.now()
    request.decided_via = via
    request.decision_note = note.strip()[:255]
    request.updated_by = user
    request.save()
    transaction.on_commit(lambda: notify.leave_decided(request))
    return request


@transaction.atomic
def cancel_leave(request: LeaveRequest, user=None) -> LeaveRequest:
    request = LeaveRequest.objects.select_for_update().get(pk=request.pk)
    if request.status not in (LeaveRequest.Status.PENDING, LeaveRequest.Status.APPROVED):
        raise HRError("Only waiting or approved leave can be cancelled.")
    if request.payroll_run_id and request.payroll_run.status != PayrollRun.Status.DRAFT:
        raise HRError("This leave is already in a posted payroll.")
    was_approved = request.status == LeaveRequest.Status.APPROVED
    request.status = LeaveRequest.Status.CANCELLED
    request.payroll_run = None
    request.updated_by = user
    request.save()
    transaction.on_commit(lambda: notify.leave_cancelled(request, was_approved))
    return request


# --- Payroll -----------------------------------------------------------------------------------


def _period(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


@transaction.atomic
def create_run(company, year: int, month: int, user=None) -> PayrollRun:
    if not 1 <= month <= 12:
        raise HRError("Choose a month.", "month")
    if PayrollRun.objects.filter(company=company, year=year, month=month).exists():
        raise HRError("There is already a payroll for that month.", "month")
    start, end = _period(year, month)
    run = PayrollRun.objects.create(
        company=company, year=year, month=month, created_by=user, updated_by=user
    )
    run.number = next_document_number(company, "PAY", end)
    run.save(update_fields=["number"])
    return compute_run(run, user)


@transaction.atomic
def compute_run(run: PayrollRun, user=None) -> PayrollRun:
    """(Re)build every payslip from the employees and the approved leave. Additions, deductions
    and notes typed on a payslip are kept."""
    run = PayrollRun.objects.select_for_update().select_related("company").get(pk=run.pk)
    if run.status != PayrollRun.Status.DRAFT:
        raise HRError("A posted payroll can't be recalculated.")
    company = run.company
    settings = hr_settings(company)
    decimals = currency_decimals(company.currency_id)
    start, end = _period(run.year, run.month)
    month_days = (end - start).days + 1
    kept = {slip.employee_id: slip for slip in run.payslips.all()}
    run.payslips.all().delete()
    LeaveRequest.objects.filter(payroll_run=run).update(payroll_run=None)

    employees = (
        Employee.objects.filter(company=company, hire_date__lte=end)
        .exclude(end_date__lt=start)
        .exclude(is_active=False, end_date__isnull=True)
    )
    totals = {"gross": ZERO, "deductions": ZERO, "additions": ZERO, "net": ZERO}
    slips = []
    for employee in employees:
        employed_from = max(employee.hire_date, start)
        employed_to = min(employee.end_date or end, end)
        factor = Decimal((employed_to - employed_from).days + 1) / Decimal(month_days)
        basic = round_money(employee.basic_salary * factor, decimals)
        allowances = round_money((employee.monthly_pay - employee.basic_salary) * factor, decimals)
        gross = basic + allowances

        # Approved leave in this month, plus unpaid leave from earlier months not yet accounted.
        leaves = LeaveRequest.objects.filter(
            employee=employee, status=LeaveRequest.Status.APPROVED, start_date__lte=end
        ).filter(end_date__gte=start) | LeaveRequest.objects.filter(
            employee=employee,
            status=LeaveRequest.Status.APPROVED,
            end_date__lt=start,
            payroll_run__isnull=True,
            leave_type__is_paid=False,
        )
        paid_days = unpaid_days = ZERO
        for leave in leaves.select_related("leave_type").distinct():
            if leave.end_date < start:
                days = leave.days
            else:
                days = working_days(
                    max(leave.start_date, start), min(leave.end_date, end), settings.weekend
                )
            if leave.leave_type.is_paid:
                paid_days += days
            else:
                unpaid_days += days
            leave.payroll_run = run
            leave.save(update_fields=["payroll_run"])
        daily = employee.monthly_pay / Decimal(settings.pay_days_per_month or 30)
        deduction = min(round_money(daily * unpaid_days, decimals), gross)
        previous = kept.get(employee.pk)
        additions = previous.additions if previous else ZERO
        deductions = previous.deductions if previous else ZERO
        net = gross - deduction + additions - deductions
        slips.append(
            Payslip(
                run=run,
                employee=employee,
                basic=basic,
                allowances=allowances,
                gross=gross,
                paid_leave_days=paid_days,
                unpaid_leave_days=unpaid_days,
                unpaid_leave_deduction=deduction,
                additions=additions,
                deductions=deductions,
                net=net,
                notes=previous.notes if previous else "",
            )
        )
        totals["gross"] += gross
        totals["deductions"] += deduction + deductions
        totals["additions"] += additions
        totals["net"] += net
    Payslip.objects.bulk_create(slips)
    run.total_gross = totals["gross"]
    run.total_deductions = totals["deductions"]
    run.total_additions = totals["additions"]
    run.total_net = totals["net"]
    run.updated_by = user
    run.save()
    return run


@transaction.atomic
def update_payslip(slip: Payslip, additions: Decimal, deductions: Decimal, notes: str, user=None):
    slip = Payslip.objects.select_for_update().select_related("run").get(pk=slip.pk)
    if slip.run.status != PayrollRun.Status.DRAFT:
        raise HRError("This payroll is posted; payslips can't change.")
    if additions < 0 or deductions < 0:
        raise HRError("Amounts can't be negative.")
    slip.additions = additions
    slip.deductions = deductions
    slip.notes = notes[:255]
    slip.net = slip.gross - slip.unpaid_leave_deduction + additions - deductions
    slip.save()
    run = slip.run
    items = list(run.payslips.all())
    run.total_additions = sum((s.additions for s in items), ZERO)
    run.total_deductions = sum((s.unpaid_leave_deduction + s.deductions for s in items), ZERO)
    run.total_net = sum((s.net for s in items), ZERO)
    run.updated_by = user
    run.save()
    return slip


@transaction.atomic
def post_run(run: PayrollRun, user=None) -> PayrollRun:
    """Book the month: debit salaries for what was earned, credit what is owed to staff (and
    other deductions to their account)."""
    run = PayrollRun.objects.select_for_update().select_related("company").get(pk=run.pk)
    if run.status != PayrollRun.Status.DRAFT:
        raise HRError("This payroll is already posted.")
    slips = list(run.payslips.all())
    if not slips:
        raise HRError("There are no payslips to post.")
    settings = hr_settings(run.company)
    if not settings.salary_expense_account or not settings.salary_payable_account:
        raise HRError("Choose the salary accounts in HR settings first.")
    other = sum((s.deductions for s in slips), ZERO)
    if other and not settings.deductions_account:
        raise HRError("Choose the account for other deductions in HR settings first.")
    net = sum((s.net for s in slips), ZERO)
    earned = net + other
    label = f"Payroll {run.year}-{run.month:02d}"
    lines = [LineSpec(settings.salary_expense_account, debit=earned, description=label)]
    if net:
        lines.append(LineSpec(settings.salary_payable_account, credit=net, description=label))
    if other:
        lines.append(
            LineSpec(
                settings.deductions_account, credit=other, description="Other payroll deductions"
            )
        )
    _, end = _period(run.year, run.month)
    try:
        entry = create_and_post(
            run.company,
            "JV",
            end,
            lines,
            description=label,
            reference=run.number,
            source_type="hr.payrollrun",
            source_id=run.pk,
            user=user,
        )
    except PostingError as exc:
        raise HRError(exc.message) from exc
    run.journal_entry = entry
    run.status = PayrollRun.Status.POSTED
    run.posted_at = timezone.now()
    run.updated_by = user
    run.save()
    return run


@transaction.atomic
def pay_run(run: PayrollRun, journal: Journal, on_date: date, user=None) -> PayrollRun:
    """Salaries paid from the bank (or cash): clear what is owed to staff."""
    run = PayrollRun.objects.select_for_update().select_related("company").get(pk=run.pk)
    if run.status != PayrollRun.Status.POSTED:
        raise HRError("Post the payroll before paying it.")
    if journal.company_id != run.company_id or journal.journal_type not in ("bank", "cash"):
        raise HRError("Choose a bank or cash journal.", "journal")
    if not journal.default_account_id:
        raise HRError("That journal has no account.", "journal")
    settings = hr_settings(run.company)
    label = f"Salaries paid {run.year}-{run.month:02d}"
    try:
        entry = create_and_post(
            run.company,
            journal.code,
            on_date,
            [
                LineSpec(settings.salary_payable_account, debit=run.total_net, description=label),
                LineSpec(journal.default_account, credit=run.total_net, description=label),
            ],
            description=label,
            reference=run.number,
            source_type="hr.payrollrun",
            source_id=run.pk,
            user=user,
        )
    except PostingError as exc:
        raise HRError(exc.message, "date" if exc.field == "date" else None) from exc
    run.payment_entry = entry
    run.status = PayrollRun.Status.PAID
    run.paid_at = timezone.now()
    run.updated_by = user
    run.save()
    return run
