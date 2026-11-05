import pytest

pytestmark = pytest.mark.django_db


def test_login_with_email_refresh_and_logout(api, make_user):
    make_user(email="sara@alburhan.test")

    login = api.post(
        "/api/v1/auth/login/", {"email": "sara@alburhan.test", "password": "pass-12345"}
    )
    assert login.status_code == 200
    tokens = login.json()

    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    assert api.get("/api/v1/me/").json()["email"] == "sara@alburhan.test"

    refreshed = api.post("/api/v1/auth/refresh/", {"refresh": tokens["refresh"]})
    assert refreshed.status_code == 200
    new_refresh = refreshed.json()["refresh"]

    # Rotated refresh tokens cannot be reused.
    assert api.post("/api/v1/auth/refresh/", {"refresh": tokens["refresh"]}).status_code == 401

    assert api.post("/api/v1/auth/logout/", {"refresh": new_refresh}).status_code == 200
    assert api.post("/api/v1/auth/refresh/", {"refresh": new_refresh}).status_code == 401


def test_wrong_password_uses_standard_error_shape(api, make_user):
    make_user(email="sara@alburhan.test")
    response = api.post("/api/v1/auth/login/", {"email": "sara@alburhan.test", "password": "wrong"})
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "not_authenticated"
    assert body["message"]
    assert body["fields"] == {}


def test_unauthenticated_request_is_rejected(api):
    response = api.get("/api/v1/me/")
    assert response.status_code == 401
    assert response.json()["code"] == "not_authenticated"


def test_me_can_change_language_and_default_company(api, make_role, make_user, kuwait, uae):
    role = make_role("viewer", "organization.warehouse.view")
    user = make_user((role, kuwait, None))
    api.force_authenticate(user)

    ok = api.patch("/api/v1/me/", {"preferred_language": "ar", "default_company": kuwait.id})
    denied = api.patch("/api/v1/me/", {"default_company": uae.id})

    assert ok.status_code == 200
    assert ok.json()["preferred_language"] == "ar"
    assert denied.status_code == 400
    assert "default_company" in denied.json()["fields"]


# --- Profile: photo and own password -------------------------------------------------------------


def _png():
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "purple").save(buffer, format="PNG")
    return SimpleUploadedFile("me.png", buffer.getvalue(), content_type="image/png")


def test_profile_photo_upload_and_remove(api, make_user):
    user = make_user()
    api.force_authenticate(user)
    assert api.get("/api/v1/me/").json()["avatar_url"] is None
    uploaded = api.post("/api/v1/me/avatar/", {"avatar": _png()}, format="multipart")
    assert uploaded.status_code == 200, uploaded.json()
    assert uploaded.json()["avatar_url"]
    listed = api.get("/api/v1/me/").json()
    assert listed["avatar_url"] and "avatars/" in listed["avatar_url"]
    removed = api.delete("/api/v1/me/avatar/")
    assert removed.status_code == 200 and removed.json()["avatar_url"] is None


def test_profile_photo_rejects_non_images(api, make_user):
    from django.core.files.uploadedfile import SimpleUploadedFile

    api.force_authenticate(make_user())
    bad = SimpleUploadedFile("x.txt", b"not an image", content_type="text/plain")
    assert api.post("/api/v1/me/avatar/", {"avatar": bad}, format="multipart").status_code == 400


def test_change_own_password(api, make_user):
    user = make_user()
    api.force_authenticate(user)
    wrong = api.post(
        "/api/v1/me/password/",
        {"current_password": "nope", "new_password": "Better-pass-2026"},
        format="json",
    )
    assert wrong.status_code == 400 and "current_password" in wrong.json()["fields"]
    weak = api.post(
        "/api/v1/me/password/",
        {"current_password": "pass-12345", "new_password": "12345678"},
        format="json",
    )
    assert weak.status_code == 400
    ok = api.post(
        "/api/v1/me/password/",
        {"current_password": "pass-12345", "new_password": "Better-pass-2026"},
        format="json",
    )
    assert ok.status_code == 204
    user.refresh_from_db()
    assert user.check_password("Better-pass-2026")
