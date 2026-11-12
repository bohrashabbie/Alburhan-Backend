from django.apps import AppConfig


class HrConfig(AppConfig):
    name = "apps.hr"
    label = "hr"
    verbose_name = "HR"

    def ready(self):
        from auditlog.registry import auditlog

        from . import models

        for model in (
            models.HRSettings,
            models.Employee,
            models.LeaveType,
            models.LeaveRequest,
            models.PayrollRun,
            models.Payslip,
            models.SalaryChange,
        ):
            auditlog.register(model)
