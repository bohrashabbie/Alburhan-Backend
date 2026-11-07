from apps.access.catalog import PermissionDef, crud

PERMISSIONS = [
    *crud("hr.employee", "employees"),
    *crud("hr.department", "departments"),
    *crud("hr.leave_type", "leave types"),
    *crud("hr.leave", "leave requests"),
    PermissionDef("hr.leave.approve", "Approve or reject anyone's leave"),
    *crud("hr.payroll", "payroll"),
    PermissionDef("hr.payroll.post", "Post payroll to the books"),
    PermissionDef("hr.payroll.pay", "Record salary payments"),
    PermissionDef("hr.settings.view", "View HR settings"),
    PermissionDef("hr.settings.update", "Edit HR settings"),
]
