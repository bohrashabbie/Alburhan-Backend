from django.urls import path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenBlacklistView, TokenRefreshView

from . import views

router = DefaultRouter()
router.register("users", views.UserViewSet)

urlpatterns = [
    path("auth/login/", views.LoginView.as_view(), name="auth_login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="auth_refresh"),
    path("auth/logout/", TokenBlacklistView.as_view(), name="auth_logout"),
    path("me/", views.MeView.as_view(), name="me"),
    *router.urls,
]
