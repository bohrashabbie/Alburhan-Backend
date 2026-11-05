from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("profit-and-loss/", views.ProfitAndLossView.as_view(), name="profit_and_loss"),
    path("balance-sheet/", views.BalanceSheetView.as_view(), name="balance_sheet"),
    path("ageing/", views.AgeingView.as_view(), name="ageing"),
    path("compliance/vat-return/", views.VatReturnView.as_view(), name="vat_return"),
    path("compliance/xbrl/", views.XbrlView.as_view(), name="xbrl"),
    path(
        "compliance/egypt-einvoice/<int:pk>/",
        views.EgyptEInvoiceView.as_view(),
        name="egypt_einvoice",
    ),
]
