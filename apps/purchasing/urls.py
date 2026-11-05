from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("orders", views.PurchaseOrderViewSet)
router.register("receipts", views.GoodsReceiptViewSet)
router.register("bills", views.SupplierInvoiceViewSet)
router.register("payments", views.SupplierPaymentViewSet)

urlpatterns = [
    path(
        "suppliers/<int:pk>/balance/", views.SupplierBalanceView.as_view(), name="supplier_balance"
    ),
    *router.urls,
]
