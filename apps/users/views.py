from django.db.models import Q
from rest_framework import generics, viewsets
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.permissions import required_permission

from .models import User
from .serializers import MeSerializer, UserSerializer


class LoginView(TokenObtainPairView):
    """Email + password → {access, refresh}. Rate-limited per client IP."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.select_related("default_company", "default_branch").order_by("username")
    serializer_class = UserSerializer
    permission_resource = "users.user"
    filterset_fields = ["is_active", "default_company"]
    search_fields = ["username", "email", "first_name", "last_name"]

    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_superuser:
            return queryset
        # Users whose home company, or any role, is in a company where the actor has this permission.
        companies, _ = user.permission_scope(required_permission(self))
        return queryset.filter(
            Q(default_company__in=companies) | Q(user_roles__company__in=companies)
        ).distinct()
