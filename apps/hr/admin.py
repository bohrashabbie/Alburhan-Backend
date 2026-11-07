from django.contrib import admin

from . import models

for model in (
    models.Department,
    models.Employee,
    models.LeaveType,
    models.LeaveRequest,
    models.PayrollRun,
    models.Notification,
):
    admin.site.register(model)
