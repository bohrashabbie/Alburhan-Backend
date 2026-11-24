from django.urls import path

from . import views

urlpatterns = [
    path("dashboard/", views.DashboardView.as_view(), name="dashboard"),
    path("profit-and-loss/", views.ProfitAndLossView.as_view(), name="profit_and_loss"),
    path("balance-sheet/", views.BalanceSheetView.as_view(), name="balance_sheet"),
    path("ageing/", views.AgeingView.as_view(), name="ageing"),
    path("customer-statement/", views.CustomerStatementView.as_view(), name="customer_statement"),
    path("customer-balances/", views.CustomerBalancesView.as_view(), name="customer_balances"),
    path("best-sellers/", views.BestSellersView.as_view(), name="best_sellers"),
    path("legacy-documents/", views.LegacyDocumentsView.as_view(), name="legacy_documents"),
    path("trade-calendar/", views.TradeCalendarView.as_view(), name="trade_calendar"),
    path("warehouse-stock/", views.WarehouseStockView.as_view(), name="warehouse_stock"),
    path("sales-targets/", views.SalesTargetsView.as_view(), name="sales_targets"),
    path("compliance/vat-return/", views.VatReturnView.as_view(), name="vat_return"),
    path("compliance/xbrl/", views.XbrlView.as_view(), name="xbrl"),
    path("compliance/xbrl/check/", views.XbrlCheckView.as_view(), name="xbrl_check"),
    path(
        "compliance/egypt-einvoice/<int:pk>/",
        views.EgyptEInvoiceView.as_view(),
        name="egypt_einvoice",
    ),
]
