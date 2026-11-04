from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("customers", views.CustomerViewSet)
router.register("suppliers", views.SupplierViewSet)

urlpatterns = router.urls
