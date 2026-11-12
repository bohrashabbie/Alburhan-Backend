"""HR: employees and their managers, leave (requested by the employee, decided by the manager,
from the app or straight from an email link), and the monthly payroll that turns approved leave
into pay: unpaid leave is deducted, then the run is posted to the books and paid."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField
from apps.core.models import AuditedModel, CompanyScopedModel, TimeStampedModel

ZERO = Decimal("0")


class HRSettings(AuditedModel):
    """Per-country HR rules."""

    company = models.OneToOneField(
        "organization.Company", on_delete=models.CASCADE, related_name="hr_settings"
    )
    hr_email = models.EmailField(
        blank=True, help_text=_("HR inbox: told about every leave request and decision.")
    )
    weekend_days = models.CharField(
        max_length=20,
        default="4,5",
        help_text=_("Days not counted as leave, 0 = Monday … 6 = Sunday. Kuwait: 4,5 (Fri, Sat)."),
    )
    pay_days_per_month = models.PositiveSmallIntegerField(
        default=26,
        help_text=_("A day's pay = monthly pay ÷ this. Kuwait labour law: 26."),
    )
    salary_expense_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    salary_payable_account = models.ForeignKey(
        "accounting.Account", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    deductions_account = models.ForeignKey(
        "accounting.Account",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Where other deductions go, e.g. staff advances being repaid."),
    )

    class Meta:
        verbose_name_plural = "HR settings"

    def __str__(self):
        return f"HR settings · {self.company.code}"

    @property
    def weekend(self) -> set[int]:
        return {int(day) for day in self.weekend_days.split(",") if day.strip().isdigit()}


class Department(CompanyScopedModel):
    name = models.CharField(max_length=120)
    name_ar = models.CharField(max_length=120, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Employee(CompanyScopedModel):
    number = models.CharField(max_length=40, blank=True, db_index=True)
    first_name = models.CharField(max_length=80)
    last_name = models.CharField(max_length=80, blank=True)
    name_ar = models.CharField(max_length=160, blank=True)
    email = models.EmailField(blank=True, help_text=_("Leave emails go here."))
    phone = models.CharField(max_length=32, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee",
        help_text=_("The person's login, so they can request leave themselves."),
    )
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, null=True, blank=True, related_name="employees"
    )
    job_title = models.CharField(max_length=120, blank=True)
    manager = models.ForeignKey(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reports",
        help_text=_("Approves this person's leave."),
    )
    hire_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    civil_id = models.CharField(max_length=40, blank=True)
    nationality = models.CharField(max_length=2, blank=True)
    iban = models.CharField(max_length=40, blank=True)
    basic_salary = MoneyField()
    housing_allowance = MoneyField()
    transport_allowance = MoneyField()
    other_allowance = MoneyField()
    annual_leave_days = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=Decimal("30"),
        help_text=_("Paid annual leave per year (working days)."),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["first_name", "last_name"]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def monthly_pay(self) -> Decimal:
        return (
            self.basic_salary
            + self.housing_allowance
            + self.transport_allowance
            + self.other_allowance
        )


class LeaveType(CompanyScopedModel):
    code = models.SlugField(max_length=20)
    name = models.CharField(max_length=80)
    name_ar = models.CharField(max_length=80, blank=True)
    is_paid = models.BooleanField(default=True, help_text=_("Unpaid days are deducted from pay."))
    uses_annual_balance = models.BooleanField(
        default=False, help_text=_("Taken from the employee's annual leave days.")
    )
    days_per_year = models.DecimalField(
        max_digits=5,
        decimal_places=1,
        default=ZERO,
        help_text=_("Yearly limit for this type; 0 = no limit."),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_leave_type_code")
        ]

    def __str__(self):
        return self.name


class LeaveRequest(CompanyScopedModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Waiting for approval")
        APPROVED = "approved", _("Approved")
        REJECTED = "rejected", _("Rejected")
        CANCELLED = "cancelled", _("Cancelled")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="+")
    start_date = models.DateField()
    end_date = models.DateField()
    days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    reason = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    approver = models.ForeignKey(
        Employee,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("The manager asked to decide."),
    )
    decided_by_name = models.CharField(max_length=160, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decided_via = models.CharField(max_length=10, blank=True, help_text=_("app or email"))
    decision_note = models.CharField(max_length=255, blank=True)
    payroll_run = models.ForeignKey(
        "PayrollRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leaves",
        help_text=_("The payroll that accounted for this leave."),
    )

    class Meta:
        ordering = ["-start_date", "-id"]
        indexes = [models.Index(fields=["company", "status", "start_date"])]

    def __str__(self):
        return f"{self.number} {self.employee}".strip()


class Notification(TimeStampedModel):
    """Every HR email: who it went to, why, and whether it was sent."""

    company = models.ForeignKey("organization.Company", on_delete=models.CASCADE, related_name="+")
    leave_request = models.ForeignKey(
        LeaveRequest, on_delete=models.CASCADE, null=True, blank=True, related_name="notifications"
    )
    to = models.EmailField()
    kind = models.CharField(max_length=30)
    subject = models.CharField(max_length=200)
    sent = models.BooleanField(default=False)
    error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]


class PayrollRun(CompanyScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")
        PAID = "paid", _("Paid")

    number = models.CharField(max_length=40, blank=True, db_index=True)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.DRAFT)
    total_gross = MoneyField()
    total_deductions = MoneyField()
    total_additions = MoneyField()
    total_net = MoneyField()
    journal_entry = models.ForeignKey(
        "accounting.JournalEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    payment_entry = models.ForeignKey(
        "accounting.JournalEntry", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    paid_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-year", "-month"]
        constraints = [
            models.UniqueConstraint(
                fields=["company", "year", "month"], name="uniq_payroll_per_month"
            )
        ]

    def __str__(self):
        return f"{self.number} {self.year}-{self.month:02d}"


class Payslip(TimeStampedModel):
    run = models.ForeignKey(PayrollRun, on_delete=models.CASCADE, related_name="payslips")
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="payslips")
    basic = MoneyField()
    allowances = MoneyField()
    gross = MoneyField(help_text=_("Basic + allowances for the days employed this month."))
    paid_leave_days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    unpaid_leave_days = models.DecimalField(max_digits=5, decimal_places=1, default=ZERO)
    unpaid_leave_deduction = MoneyField()
    additions = MoneyField(help_text=_("Overtime, bonus, commission."))
    deductions = MoneyField(help_text=_("Other deductions, e.g. an advance being repaid."))
    net = MoneyField()
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["employee__first_name", "employee__last_name"]
        constraints = [
            models.UniqueConstraint(fields=["run", "employee"], name="uniq_payslip_per_run")
        ]


class SalaryChange(CompanyScopedModel):
    """Each salary set for an employee: the amounts, from when, and why. The employee record holds
    the current amounts the payroll uses; this is the history."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="salary_changes")
    effective_from = models.DateField()
    basic_salary = MoneyField()
    housing_allowance = MoneyField()
    transport_allowance = MoneyField()
    other_allowance = MoneyField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-effective_from", "-id"]

    @property
    def monthly_pay(self) -> Decimal:
        return (
            self.basic_salary
            + self.housing_allowance
            + self.transport_allowance
            + self.other_allowance
        )
