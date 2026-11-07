from rest_framework import serializers

from .models import (
    Department,
    Employee,
    HRSettings,
    LeaveRequest,
    LeaveType,
    Notification,
    PayrollRun,
    Payslip,
)

AUDIT_FIELDS = ["created_at", "updated_at", "created_by", "updated_by"]


def _same_company(value, company, field):
    if value is not None and company is not None and value.company_id != company.pk:
        raise serializers.ValidationError({field: "Choose one from the same company."})


class HRSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = HRSettings
        fields = [
            "company",
            "hr_email",
            "weekend_days",
            "pay_days_per_month",
            "salary_expense_account",
            "salary_payable_account",
            "deductions_account",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["company", *AUDIT_FIELDS]

    def validate_weekend_days(self, value):
        days = [d.strip() for d in value.split(",") if d.strip()]
        if any(not d.isdigit() or int(d) > 6 for d in days) or len(days) > 3:
            raise serializers.ValidationError("Use day numbers 0–6 separated by commas, e.g. 4,5.")
        return ",".join(sorted(set(days)))

    def validate(self, attrs):
        company = self.instance.company if self.instance else None
        for field in ("salary_expense_account", "salary_payable_account", "deductions_account"):
            _same_company(attrs.get(field), company, field)
        return attrs


class DepartmentSerializer(serializers.ModelSerializer):
    employee_count = serializers.SerializerMethodField()

    def get_employee_count(self, obj) -> int:
        return obj.employees.filter(is_active=True).count()

    class Meta:
        model = Department
        fields = ["id", "company", "name", "name_ar", "is_active", "employee_count", *AUDIT_FIELDS]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {"is_active": {"default": True}}


class LeaveTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = LeaveType
        fields = [
            "id",
            "company",
            "code",
            "name",
            "name_ar",
            "is_paid",
            "uses_annual_balance",
            "days_per_year",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = AUDIT_FIELDS
        extra_kwargs = {"is_active": {"default": True}}


class EmployeeSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(read_only=True)
    manager_name = serializers.CharField(source="manager.full_name", read_only=True, default=None)
    department_name = serializers.CharField(source="department.name", read_only=True, default=None)
    branch_name = serializers.CharField(source="branch.name", read_only=True, default=None)
    user_email = serializers.CharField(source="user.email", read_only=True, default=None)
    monthly_pay = serializers.DecimalField(max_digits=18, decimal_places=3, read_only=True)
    report_count = serializers.SerializerMethodField()

    def get_report_count(self, obj) -> int:
        return obj.reports.filter(is_active=True).count()

    class Meta:
        model = Employee
        fields = [
            "id",
            "company",
            "number",
            "first_name",
            "last_name",
            "full_name",
            "name_ar",
            "email",
            "phone",
            "user",
            "user_email",
            "branch",
            "branch_name",
            "department",
            "department_name",
            "job_title",
            "manager",
            "manager_name",
            "report_count",
            "hire_date",
            "end_date",
            "civil_id",
            "nationality",
            "iban",
            "basic_salary",
            "housing_allowance",
            "transport_allowance",
            "other_allowance",
            "monthly_pay",
            "annual_leave_days",
            "is_active",
            *AUDIT_FIELDS,
        ]
        read_only_fields = ["number", *AUDIT_FIELDS]
        extra_kwargs = {"is_active": {"default": True}}

    def validate(self, attrs):
        get = lambda name: attrs.get(name, getattr(self.instance, name, None))  # noqa: E731
        company = get("company")
        for field in ("department", "manager"):
            _same_company(get(field), company, field)
        branch = get("branch")
        if branch is not None and company is not None and branch.company_id != company.pk:
            raise serializers.ValidationError({"branch": "Choose a branch of the same company."})
        manager = get("manager")
        if manager is not None and self.instance is not None:
            seen, step = set(), manager
            while step is not None and step.pk not in seen:
                if step.pk == self.instance.pk:
                    raise serializers.ValidationError(
                        {"manager": "That would make this person their own manager."}
                    )
                seen.add(step.pk)
                step = step.manager
        user = get("user")
        if user is not None:
            taken = Employee.objects.filter(user=user)
            if self.instance:
                taken = taken.exclude(pk=self.instance.pk)
            if taken.exists():
                raise serializers.ValidationError(
                    {"user": "That login already belongs to another employee."}
                )
        end = get("end_date")
        if end and get("hire_date") and end < get("hire_date"):
            raise serializers.ValidationError(
                {"end_date": "The last day can't be before the hire date."}
            )
        for field in (
            "basic_salary",
            "housing_allowance",
            "transport_allowance",
            "other_allowance",
        ):
            value = attrs.get(field)
            if value is not None and value < 0:
                raise serializers.ValidationError({field: "Can't be negative."})
        return attrs


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "to", "kind", "subject", "sent", "error", "created_at"]


class LeaveRequestSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_email = serializers.CharField(source="employee.email", read_only=True)
    leave_type_name = serializers.CharField(source="leave_type.name", read_only=True)
    leave_type_name_ar = serializers.CharField(source="leave_type.name_ar", read_only=True)
    is_paid = serializers.BooleanField(source="leave_type.is_paid", read_only=True)
    approver_name = serializers.CharField(source="approver.full_name", read_only=True, default=None)
    approver_email = serializers.CharField(source="approver.email", read_only=True, default=None)
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    payroll_number = serializers.CharField(
        source="payroll_run.number", read_only=True, default=None
    )
    notifications = NotificationSerializer(many=True, read_only=True)

    class Meta:
        model = LeaveRequest
        fields = [
            "id",
            "company",
            "number",
            "employee",
            "employee_name",
            "employee_email",
            "leave_type",
            "leave_type_name",
            "leave_type_name_ar",
            "is_paid",
            "start_date",
            "end_date",
            "days",
            "reason",
            "status",
            "status_label",
            "approver",
            "approver_name",
            "approver_email",
            "decided_by_name",
            "decided_at",
            "decided_via",
            "decision_note",
            "payroll_run",
            "payroll_number",
            "notifications",
            *AUDIT_FIELDS,
        ]
        read_only_fields = [
            "number",
            "days",
            "status",
            "approver",
            "decided_by_name",
            "decided_at",
            "decided_via",
            "decision_note",
            "payroll_run",
            *AUDIT_FIELDS,
        ]


class LeaveSubmitSerializer(serializers.Serializer):
    leave_type = serializers.PrimaryKeyRelatedField(queryset=LeaveType.objects.all())
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    reason = serializers.CharField(required=False, allow_blank=True, max_length=2000)


class DecisionSerializer(serializers.Serializer):
    decision = serializers.ChoiceField(choices=["approve", "reject"])
    note = serializers.CharField(required=False, allow_blank=True, max_length=255)


class PayslipSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source="employee.full_name", read_only=True)
    employee_number = serializers.CharField(source="employee.number", read_only=True)
    job_title = serializers.CharField(source="employee.job_title", read_only=True)
    iban = serializers.CharField(source="employee.iban", read_only=True)

    class Meta:
        model = Payslip
        fields = [
            "id",
            "run",
            "employee",
            "employee_name",
            "employee_number",
            "job_title",
            "iban",
            "basic",
            "allowances",
            "gross",
            "paid_leave_days",
            "unpaid_leave_days",
            "unpaid_leave_deduction",
            "additions",
            "deductions",
            "net",
            "notes",
        ]
        read_only_fields = [
            "run",
            "employee",
            "basic",
            "allowances",
            "gross",
            "paid_leave_days",
            "unpaid_leave_days",
            "unpaid_leave_deduction",
            "net",
        ]


class PayrollRunSerializer(serializers.ModelSerializer):
    status_label = serializers.CharField(source="get_status_display", read_only=True)
    journal_entry_number = serializers.CharField(
        source="journal_entry.number", read_only=True, default=None
    )
    payment_entry_number = serializers.CharField(
        source="payment_entry.number", read_only=True, default=None
    )
    employee_count = serializers.SerializerMethodField()
    payslips = PayslipSerializer(many=True, read_only=True)

    def get_employee_count(self, obj) -> int:
        return obj.payslips.count()

    class Meta:
        model = PayrollRun
        fields = [
            "id",
            "company",
            "number",
            "year",
            "month",
            "status",
            "status_label",
            "total_gross",
            "total_deductions",
            "total_additions",
            "total_net",
            "employee_count",
            "journal_entry",
            "journal_entry_number",
            "payment_entry",
            "payment_entry_number",
            "posted_at",
            "paid_at",
            "notes",
            "payslips",
            *AUDIT_FIELDS,
        ]
        read_only_fields = [
            "number",
            "status",
            "total_gross",
            "total_deductions",
            "total_additions",
            "total_net",
            "journal_entry",
            "payment_entry",
            "posted_at",
            "paid_at",
            *AUDIT_FIELDS,
        ]


class PayslipUpdateSerializer(serializers.Serializer):
    additions = serializers.DecimalField(max_digits=18, decimal_places=3)
    deductions = serializers.DecimalField(max_digits=18, decimal_places=3)
    notes = serializers.CharField(required=False, allow_blank=True, max_length=255)


class PaySerializer(serializers.Serializer):
    journal = serializers.IntegerField()
    date = serializers.DateField()
