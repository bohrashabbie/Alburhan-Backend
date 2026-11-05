from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("quotations", views.QuotationViewSet)
router.register("invoices", views.InvoiceViewSet)
router.register("deliveries", views.DeliveryViewSet)
router.register("receipts", views.ReceiptViewSet)
router.register("credit-notes", views.CreditNoteViewSet)
router.register("settings", views.SalesSettingsViewSet)

urlpatterns = [
    path(
        "customers/<int:pk>/balance/", views.CustomerBalanceView.as_view(), name="customer_balance"
    ),
    *router.urls,
]
