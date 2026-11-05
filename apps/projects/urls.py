from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("projects", views.ProjectViewSet)
router.register("issues", views.MaterialIssueViewSet)

urlpatterns = router.urls
