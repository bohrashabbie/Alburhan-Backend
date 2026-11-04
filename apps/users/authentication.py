"""JWT authentication that keeps the signed-in user in the cache for a minute, saving a database
round trip on every request. Any change to the user (e.g. deactivation) clears the entry at once."""

from django.core.cache import cache
from django.db import transaction
from django.db.models.signals import post_delete, post_save
from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.settings import api_settings

USER_TTL_SECONDS = 60


def _key(user_id) -> str:
    return f"auth:user:{user_id}"


class CachedUserJWTAuthentication(JWTAuthentication):
    def get_user(self, validated_token):
        user_id = validated_token.get(api_settings.USER_ID_CLAIM)
        user = cache.get(_key(user_id)) if user_id is not None else None
        if user is None:
            user = super().get_user(validated_token)  # raises if missing or inactive
            cache.set(_key(user_id), user, USER_TTL_SECONDS)
        elif not user.is_active:
            return super().get_user(validated_token)
        # Per-request caches on the object must not leak between requests.
        for attr in ("_erp_grants", "_allowed_company_ids", "_allowed_branch_ids"):
            user.__dict__.pop(attr, None)
        return user


def _forget_user(sender, instance, **kwargs):
    cache.delete(_key(instance.pk))
    transaction.on_commit(lambda: cache.delete(_key(instance.pk)))


def connect():
    from .models import User

    post_save.connect(_forget_user, sender=User, dispatch_uid="auth_user_cache_save")
    post_delete.connect(_forget_user, sender=User, dispatch_uid="auth_user_cache_delete")
