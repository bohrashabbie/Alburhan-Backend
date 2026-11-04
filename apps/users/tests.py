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
