from datetime import date

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from apps.accounting.models import Journal
from apps.core.viewsets import (
    AuditedViewSetMixin,
    CompanyScopedModelViewSet,
    CompanyScopedViewSetMixin,
)
from apps.organization.models import Company

from . import serializers
from .models import Department, Employee, HRSettings, LeaveRequest, LeaveType, PayrollRun, Payslip
from .services import (
    HRError,
    balances,
    can_decide,
    cancel_leave,
    compute_run,
    create_run,
    decide_leave,
    ensure_leave_types,
    hr_settings,
    pay_run,
    post_run,
    request_from_token,
    submit_leave,
    update_payslip,
)


def _raise(exc: HRError):
    raise ValidationError({exc.field: exc.message} if exc.field else {"detail": exc.message})


def _display(user) -> str:
    return (f"{user.first_name} {user.last_name}".strip() or user.email) if user else ""


class HRSettingsViewSet(
    CompanyScopedViewSetMixin,
    AuditedViewSetMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """GET/PATCH /hr/settings/<company id>/ (created with country defaults on first read)."""

    queryset = HRSettings.objects.select_related("company")
    serializer_class = serializers.HRSettingsSerializer
    permission_resource = "hr.settings"
    lookup_field = "company"

    def get_object(self):
        company = Company.objects.filter(pk=self.kwargs["company"]).first()
        if company is None:
            raise NotFound("Company not found.")
        hr_settings(company)
        return super().get_object()


class DepartmentViewSet(CompanyScopedModelViewSet):
    queryset = Department.objects.all()
    serializer_class = serializers.DepartmentSerializer
    permission_resource = "hr.department"
    filterset_fields = ["company", "is_active"]
    search_fields = ["name", "name_ar"]

    def perform_destroy(self, instance):
        if instance.employees.exists():
            raise ValidationError({"detail": "Move its employees to another department first."})
        instance.delete()


class LeaveTypeViewSet(CompanyScopedModelViewSet):
    queryset = LeaveType.objects.all()
    serializer_class = serializers.LeaveTypeSerializer
    permission_resource = "hr.leave_type"
    filterset_fields = ["company", "is_active", "is_paid"]
    search_fields = ["code", "name", "name_ar"]

    def list(self, request, *args, **kwargs):
        company = request.query_params.get("company")
        if company and company.isdigit():
            found = Company.objects.filter(pk=company).first()
            if found:
                ensure_leave_types(found)
        return super().list(request, *args, **kwargs)

    def perform_destroy(self, instance):
        if LeaveRequest.objects.filter(leave_type=instance).exists():
            raise ValidationError(
                {"detail": "This type is used by leave requests; switch it off instead."}
            )
        instance.delete()


class EmployeeViewSet(CompanyScopedModelViewSet):
    queryset = Employee.objects.select_related("manager", "department", "branch", "user")
    serializer_class = serializers.EmployeeSerializer
    permission_resource = "hr.employee"
    action_permissions = {"balance": "hr.employee.view"}
    branch_lookup = "branch"
    branch_field = "branch"
    filterset_fields = ["company", "branch", "department", "manager", "is_active"]
    search_fields = [
        "number",
        "first_name",
        "last_name",
        "name_ar",
        "email",
        "job_title",
        "civil_id",
    ]
    ordering_fields = ["first_name", "number", "hire_date"]

    def perform_create(self, serializer):
        from apps.organization.services import next_document_number

        self._assert_allowed(serializer)
        employee = serializer.save(created_by=self.request.user, updated_by=self.request.user)
        employee.number = next_document_number(employee.company, "EMP", yearly=False)
        employee.save(update_fields=["number"])

    def perform_destroy(self, instance):
        if instance.leave_requests.exists() or instance.payslips.exists():
            raise ValidationError(
                {"detail": "This employee has leave or payslips; set a last day instead."}
            )
        if instance.reports.exists():
            raise ValidationError(
                {"detail": "Others report to this person; give them a new manager first."}
            )
        instance.delete()

    @action(detail=True, methods=["get"])
    def balance(self, request, pk=None):
        year = request.query_params.get("year")
        employee = self.get_object()
        return Response(
            {
                "weekend": sorted(hr_settings(employee.company).weekend),
                "balances": balances(employee, int(year) if year and year.isdigit() else None),
            }
        )


class LeaveRequestViewSet(
    CompanyScopedViewSetMixin,
    AuditedViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """HR's view of every request. Requests are made with `submit` (HR on someone's behalf) or
    /hr/me/leave/ (the employee)."""

    queryset = LeaveRequest.objects.select_related(
        "employee", "leave_type", "approver", "payroll_run"
    ).prefetch_related("notifications")
    serializer_class = serializers.LeaveRequestSerializer
    permission_resource = "hr.leave"
    action_permissions = {
        "submit": "hr.leave.create",
        "decide": "hr.leave.approve",
        "cancel": "hr.leave.update",
    }
    branch_lookup = "employee__branch"
    filterset_fields = ["company", "employee", "leave_type", "status", "approver"]
    search_fields = ["number", "employee__first_name", "employee__last_name", "reason"]
    ordering_fields = ["start_date", "created_at", "number"]

    @extend_schema(request=serializers.LeaveSubmitSerializer)
    @action(detail=False, methods=["post"])
    def submit(self, request):
        employee = get_object_or_404(
            Employee.objects.filter(company__in=request.user.permission_scope("hr.leave.create")[0])
            if not request.user.is_superuser
            else Employee.objects.all(),
            pk=request.data.get("employee"),
        )
        data = serializers.LeaveSubmitSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            leave = submit_leave(
                employee,
                user=request.user,
                **{
                    "leave_type": data.validated_data["leave_type"],
                    "start": data.validated_data["start_date"],
                    "end": data.validated_data["end_date"],
                    "reason": data.validated_data.get("reason", ""),
                },
            )
        except HRError as exc:
            _raise(exc)
        return Response(self.get_serializer(leave).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=serializers.DecisionSerializer)
    @action(detail=True, methods=["post"])
    def decide(self, request, pk=None):
        data = serializers.DecisionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            leave = decide_leave(
                self.get_object(),
                data.validated_data["decision"] == "approve",
                f"{_display(request.user)} (HR)",
                "app",
                data.validated_data.get("note", ""),
                request.user,
            )
        except HRError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=leave.pk)).data)

    @action(detail=True, methods=["post"])
    def cancel(self, request, pk=None):
        try:
            leave = cancel_leave(self.get_object(), request.user)
        except HRError as exc:
            _raise(exc)
        return Response(self.get_serializer(self.get_queryset().get(pk=leave.pk)).data)


class PayrollRunViewSet(
    CompanyScopedViewSetMixin,
    AuditedViewSetMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    queryset = PayrollRun.objects.select_related("journal_entry", "payment_entry").prefetch_related(
        "payslips__employee"
    )
    serializer_class = serializers.PayrollRunSerializer
    permission_resource = "hr.payroll"
    action_permissions = {
        "create": "hr.payroll.create",
        "recalculate": "hr.payroll.update",
        "payslip": "hr.payroll.update",
        "post_run": "hr.payroll.post",
        "pay": "hr.payroll.pay",
    }
    filterset_fields = ["company", "year", "status"]
    search_fields = ["number"]

    def create(self, request):
        company = Company.objects.filter(pk=request.data.get("company")).first()
        if company is None:
            raise ValidationError({"company": "Choose a company."})
        if not request.user.has_erp_permission("hr.payroll.create", company_id=company.pk):
            raise PermissionDenied("You can't run payroll for this company.")
        try:
            run = create_run(
                company,
                int(request.data.get("year") or 0),
                int(request.data.get("month") or 0),
                request.user,
            )
        except (TypeError, ValueError):
            raise ValidationError({"month": "Choose a month."}) from None
        except HRError as exc:
            _raise(exc)
        return Response(
            self.get_serializer(self.get_queryset().get(pk=run.pk)).data,
            status=status.HTTP_201_CREATED,
        )

    def perform_destroy(self, instance):
        if instance.status != PayrollRun.Status.DRAFT:
            raise ValidationError({"detail": "A posted payroll can't be deleted."})
        LeaveRequest.objects.filter(payroll_run=instance).update(payroll_run=None)
        instance.delete()

    def _fresh(self, pk):
        return Response(self.get_serializer(self.get_queryset().get(pk=pk)).data)

    @action(detail=True, methods=["post"])
    def recalculate(self, request, pk=None):
        try:
            compute_run(self.get_object(), request.user)
        except HRError as exc:
            _raise(exc)
        return self._fresh(pk)

    @extend_schema(request=serializers.PayslipUpdateSerializer)
    @action(detail=True, methods=["post"], url_path=r"payslips/(?P<slip>\d+)")
    def payslip(self, request, pk=None, slip=None):
        run = self.get_object()
        item = get_object_or_404(Payslip, pk=slip, run=run)
        data = serializers.PayslipUpdateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            update_payslip(
                item,
                data.validated_data["additions"],
                data.validated_data["deductions"],
                data.validated_data.get("notes", ""),
                request.user,
            )
        except HRError as exc:
            _raise(exc)
        return self._fresh(pk)

    # Not named "post": that would shadow the HTTP method handler on this viewset.
    @action(detail=True, methods=["post"], url_path="post")
    def post_run(self, request, pk=None):
        try:
            post_run(self.get_object(), request.user)
        except HRError as exc:
            _raise(exc)
        return self._fresh(pk)

    @extend_schema(request=serializers.PaySerializer)
    @action(detail=True, methods=["post"])
    def pay(self, request, pk=None):
        run = self.get_object()
        data = serializers.PaySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        journal = Journal.objects.filter(
            pk=data.validated_data["journal"], company=run.company
        ).first()
        if journal is None:
            raise ValidationError({"journal": "Choose a bank or cash journal."})
        try:
            pay_run(run, journal, data.validated_data["date"], request.user)
        except HRError as exc:
            _raise(exc)
        return self._fresh(pk)


# --- Self-service ------------------------------------------------------------------------------


class MeHRView(APIView):
    """GET /hr/me/: my employee record, leave balances, my requests, and requests waiting for me
    as a manager."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        employee = (
            Employee.objects.select_related("manager", "company", "department")
            .filter(user=request.user)
            .first()
        )
        waiting = (
            LeaveRequest.objects.select_related("employee", "leave_type", "approver")
            .prefetch_related("notifications")
            .filter(approver__user=request.user, status=LeaveRequest.Status.PENDING)
        )
        body = {
            "employee": None,
            "balances": [],
            "requests": [],
            "leave_types": [],
            "approvals": serializers.LeaveRequestSerializer(waiting, many=True).data,
            "weekend": [],
        }
        if employee:
            ensure_leave_types(employee.company)
            body["employee"] = {
                "id": employee.pk,
                "number": employee.number,
                "name": employee.full_name,
                "job_title": employee.job_title,
                "company": employee.company_id,
                "department": employee.department.name if employee.department_id else None,
                "manager": employee.manager.full_name if employee.manager_id else None,
                "manager_email": employee.manager.email if employee.manager_id else None,
            }
            body["balances"] = balances(employee, date.today().year)
            body["weekend"] = sorted(hr_settings(employee.company).weekend)
            body["requests"] = serializers.LeaveRequestSerializer(
                employee.leave_requests.select_related("leave_type", "approver").prefetch_related(
                    "notifications"
                )[:30],
                many=True,
            ).data
            body["leave_types"] = serializers.LeaveTypeSerializer(
                LeaveType.objects.filter(company=employee.company, is_active=True), many=True
            ).data
        return Response(body)


class MeLeaveView(APIView):
    """POST /hr/me/leave/: ask for leave."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=serializers.LeaveSubmitSerializer)
    def post(self, request):
        employee = Employee.objects.filter(user=request.user).first()
        if employee is None:
            raise ValidationError(
                {"detail": "Your login isn't linked to an employee record. Ask HR."}
            )
        data = serializers.LeaveSubmitSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            leave = submit_leave(
                employee,
                data.validated_data["leave_type"],
                data.validated_data["start_date"],
                data.validated_data["end_date"],
                data.validated_data.get("reason", ""),
                request.user,
            )
        except HRError as exc:
            _raise(exc)
        return Response(
            serializers.LeaveRequestSerializer(leave).data, status=status.HTTP_201_CREATED
        )


class MeLeaveCancelView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        leave = get_object_or_404(LeaveRequest, pk=pk, employee__user=request.user)
        try:
            leave = cancel_leave(leave, request.user)
        except HRError as exc:
            _raise(exc)
        return Response(serializers.LeaveRequestSerializer(leave).data)


class MeApprovalView(APIView):
    """POST /hr/me/approvals/<id>/: a manager decides in the app."""

    permission_classes = [IsAuthenticated]

    @extend_schema(request=serializers.DecisionSerializer)
    def post(self, request, pk):
        leave = get_object_or_404(LeaveRequest.objects.select_related("approver"), pk=pk)
        if not can_decide(request.user, leave):
            raise PermissionDenied("Only the person's manager or HR can decide this request.")
        data = serializers.DecisionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            leave = decide_leave(
                leave,
                data.validated_data["decision"] == "approve",
                _display(request.user),
                "app",
                data.validated_data.get("note", ""),
                request.user,
            )
        except HRError as exc:
            _raise(exc)
        return Response(serializers.LeaveRequestSerializer(leave).data)


class LeaveDecisionView(APIView):
    """GET/POST /hr/leave-decision/<token>/: the link in the manager's email. No sign-in: the signed
    token names the request and the manager it was sent to."""

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "leave_decision"

    def _summary(self, leave: LeaveRequest) -> dict:
        return {
            "number": leave.number,
            "company": leave.company.name,
            "employee": leave.employee.full_name,
            "employee_email": leave.employee.email,
            "job_title": leave.employee.job_title,
            "leave_type": leave.leave_type.name,
            "leave_type_ar": leave.leave_type.name_ar,
            "is_paid": leave.leave_type.is_paid,
            "start_date": leave.start_date,
            "end_date": leave.end_date,
            "days": leave.days,
            "reason": leave.reason,
            "status": leave.status,
            "status_label": leave.get_status_display(),
            "approver": leave.approver.full_name if leave.approver_id else "HR",
            "decided_by_name": leave.decided_by_name,
            "decided_at": leave.decided_at,
            "decision_note": leave.decision_note,
            "balances": [
                row
                for row in balances(leave.employee, leave.start_date.year)
                if row["leave_type"] == leave.leave_type_id
            ],
        }

    def get(self, request, token):
        try:
            leave = request_from_token(token)
        except HRError as exc:
            _raise(exc)
        return Response(self._summary(leave))

    @extend_schema(request=serializers.DecisionSerializer)
    def post(self, request, token):
        try:
            leave = request_from_token(token)
        except HRError as exc:
            _raise(exc)
        data = serializers.DecisionSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        by = leave.approver.full_name if leave.approver_id else "HR"
        try:
            decide_leave(
                leave,
                data.validated_data["decision"] == "approve",
                by,
                "email",
                data.validated_data.get("note", ""),
            )
        except HRError as exc:
            _raise(exc)
        return Response(self._summary(LeaveRequest.objects.get(pk=leave.pk)))
