from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("technicians", views.TechnicianViewSet)
router.register("work-orders", views.WorkOrderViewSet)

urlpatterns = router.urls
