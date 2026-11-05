from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("registers", views.RegisterViewSet)
router.register("shifts", views.ShiftViewSet)
router.register("sales", views.SaleViewSet)

urlpatterns = router.urls
