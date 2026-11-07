from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("settings", views.HRSettingsViewSet)
router.register("departments", views.DepartmentViewSet)
router.register("leave-types", views.LeaveTypeViewSet)
router.register("employees", views.EmployeeViewSet)
router.register("leave", views.LeaveRequestViewSet)
router.register("payroll", views.PayrollRunViewSet)

urlpatterns = [
    path("me/", views.MeHRView.as_view(), name="hr_me"),
    path("me/leave/", views.MeLeaveView.as_view(), name="hr_me_leave"),
    path("me/leave/<int:pk>/cancel/", views.MeLeaveCancelView.as_view(), name="hr_me_leave_cancel"),
    path("me/approvals/<int:pk>/", views.MeApprovalView.as_view(), name="hr_me_approval"),
    path(
        "leave-decision/<str:token>/", views.LeaveDecisionView.as_view(), name="hr_leave_decision"
    ),
    *router.urls,
]
