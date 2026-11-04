"""Image uploads (logos, product photos): validation and a viewset mixin that adds
POST/DELETE <resource>/<id>/<field>/ to set or remove one image field."""

from pathlib import Path
from uuid import uuid4

from django.conf import settings
from django.utils.deconstruct import deconstructible
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response

ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


@deconstructible
class upload_path:  # noqa: N801 - reads like a function at the call site
    """<folder>/<record id>/<random>.<ext>: a fresh name per upload so cached links never go stale.
    A class (not a closure) so migrations can serialise it."""

    def __init__(self, folder: str):
        self.folder = folder

    def __call__(self, instance, filename):
        return f"{self.folder}/{instance.pk}/{uuid4().hex[:10]}{Path(filename).suffix.lower()}"

    def __eq__(self, other):
        return isinstance(other, upload_path) and other.folder == self.folder


class ImageUploadSerializer(serializers.Serializer):
    image = serializers.ImageField()

    def validate_image(self, file):
        if file.size > settings.MAX_IMAGE_UPLOAD_BYTES:
            limit = settings.MAX_IMAGE_UPLOAD_BYTES // (1024 * 1024)
            raise serializers.ValidationError(f"The image must be {limit} MB or smaller.")
        image_format = getattr(getattr(file, "image", None), "format", None)
        if image_format not in ALLOWED_FORMATS:
            raise serializers.ValidationError("Use a PNG, JPEG or WebP image.")
        return file


def image_url(instance, field: str) -> str | None:
    """A signed link that expires (private bucket); the frontend re-fetches it with the data."""
    file = getattr(instance, field)
    return file.url if file else None


def image_action(field: str, permission: str):
    """Build an `@action` that sets (POST multipart `image`) or removes (DELETE) `field`."""

    def handler(self, request, pk=None):
        instance = self.get_object()
        if request.method == "POST":
            # The file may be sent under the field's own name ("logo") or as "image".
            file = request.data.get(field, request.data.get("image"))
            upload = ImageUploadSerializer(data={"image": file} if file is not None else {})
            if not upload.is_valid():
                raise ValidationError({field: upload.errors.get("image", ["Choose an image."])})
        current = getattr(instance, field)
        if current:
            current.delete(save=False)  # the old file leaves the bucket
        setattr(
            instance, field, upload.validated_data["image"] if request.method == "POST" else None
        )
        if hasattr(instance, "updated_by"):
            instance.updated_by = request.user
        instance.save()
        return Response(self.get_serializer(instance).data)

    handler.__name__ = field
    return action(detail=True, methods=["post", "delete"], url_path=field)(handler)
