from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("accounts", views.AccountViewSet)
router.register("journals", views.JournalViewSet)
router.register("periods", views.PeriodViewSet)
router.register("entries", views.JournalEntryViewSet)
router.register("settings", views.AccountingSettingsViewSet)

urlpatterns = [
    path("setup/", views.SetupView.as_view(), name="accounting_setup"),
    path("reports/trial-balance/", views.TrialBalanceView.as_view(), name="trial_balance"),
    path("reports/general-ledger/", views.GeneralLedgerView.as_view(), name="general_ledger"),
    *router.urls,
]
