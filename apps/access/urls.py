from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("permissions", views.PermissionViewSet)
router.register("roles", views.RoleViewSet)
router.register("user-roles", views.UserRoleViewSet)

urlpatterns = router.urls
