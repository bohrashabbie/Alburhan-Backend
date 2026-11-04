from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("adjustments", views.AdjustmentViewSet)
router.register("transfers", views.TransferViewSet)
router.register("counts", views.CountViewSet)
router.register("reservations", views.ReservationViewSet)
router.register("reorder-rules", views.ReorderRuleViewSet)
router.register("moves", views.MoveViewSet)

urlpatterns = [
    path("stock/", views.StockLevelsView.as_view(), name="stock_levels"),
    path("reports/reorder/", views.ReorderReportView.as_view(), name="reorder_report"),
    path("reports/slow-moving/", views.SlowMovingReportView.as_view(), name="slow_moving_report"),
    *router.urls,
]
