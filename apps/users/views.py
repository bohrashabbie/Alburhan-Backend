from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from apps.core.images import ImageUploadSerializer
from apps.core.permissions import required_permission

from .models import Reminder, User
from .serializers import (
    MeSerializer,
    PasswordChangeSerializer,
    ReminderSerializer,
    UserSerializer,
    assert_can_manage,
)


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


class MeAvatarView(APIView):
    """POST (multipart `image` or `avatar`) sets your profile photo; DELETE removes it."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file = request.data.get("avatar", request.data.get("image"))
        upload = ImageUploadSerializer(data={"image": file} if file is not None else {})
        upload.is_valid(raise_exception=True)
        user = request.user.__class__.objects.get(pk=request.user.pk)
        with transaction.atomic():
            if user.avatar:
                user.avatar.delete(save=False)
            user.avatar = upload.validated_data["image"]
            user.save(update_fields=["avatar"])
        request.user.avatar = user.avatar
        return Response(MeSerializer(user, context={"request": request}).data)

    def delete(self, request):
        user = request.user.__class__.objects.get(pk=request.user.pk)
        if user.avatar:
            user.avatar.delete(save=False)
            user.avatar = ""
            user.save(update_fields=["avatar"])
        request.user.avatar = user.avatar
        return Response(MeSerializer(user, context={"request": request}).data)


class MePasswordView(APIView):
    """POST {current_password, new_password}: change your own password."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request):
        data = PasswordChangeSerializer(data=request.data, context={"request": request})
        data.is_valid(raise_exception=True)
        user = request.user.__class__.objects.get(pk=request.user.pk)
        user.set_password(data.validated_data["new_password"])
        user.save(update_fields=["password"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserViewSet(viewsets.ModelViewSet):
    queryset = (
        User.objects.select_related("default_company", "default_branch")
        .prefetch_related("user_roles__company", "user_roles__branch", "user_roles__role")
        .order_by("username")
    )
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

    def perform_destroy(self, instance):
        assert_can_manage(self.request.user, instance)
        if instance.pk == self.request.user.pk:
            raise ValidationError({"detail": "You can't delete your own account."})
        instance.delete()


class ReminderViewSet(mixins.CreateModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    """Notes between colleagues. Everyone sees what was sent to them and what they sent."""

    serializer_class = ReminderSerializer
    permission_classes = [IsAuthenticated]
    queryset = Reminder.objects.select_related("sender", "recipient")
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        box = self.request.query_params.get("box")
        queryset = super().get_queryset()
        if box == "sent":
            return queryset.filter(sender=user)[:100]
        today = timezone.localdate()
        return queryset.filter(recipient=user, done_at__isnull=True).filter(
            Q(remind_on__isnull=True) | Q(remind_on__lte=today)
        )[:100]

    def perform_create(self, serializer):
        serializer.save(sender=self.request.user)

    @action(detail=True, methods=["post"])
    def done(self, request, pk=None):
        updated = Reminder.objects.filter(
            pk=pk, recipient=request.user, done_at__isnull=True
        ).update(done_at=timezone.now())
        return Response({"done": bool(updated)})

    @action(detail=False, methods=["get"])
    def colleagues(self, request):
        """People this user can send a note to: everyone who shares a company with them."""
        user = request.user
        people = User.objects.filter(is_active=True).exclude(pk=user.pk)
        if not user.is_superuser:
            people = people.filter(
                Q(user_roles__company_id__in=user.allowed_company_ids()) | Q(is_superuser=True)
            ).distinct()
        return Response(
            [
                {"id": person.pk, "name": person.get_full_name() or person.username}
                for person in people.order_by("first_name", "username")[:500]
            ]
        )
