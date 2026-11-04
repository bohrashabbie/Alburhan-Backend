from datetime import date
from decimal import Decimal

import pytest

from apps.organization.models import ExchangeRate
from apps.organization.services import (
    ExchangeRateMissing,
    convert_amount,
    get_exchange_rate,
    next_document_number,
)

pytestmark = pytest.mark.django_db


def test_reference_data_loaded_with_kwd_three_decimals():
    from apps.organization.models import Country, Currency

    assert Currency.objects.get(code="KWD").decimal_places == 3
    assert Country.objects.get(code="AE").default_currency_id == "AED"


def test_document_numbers_increment_per_company_and_type(kuwait, uae):
    on = date(2026, 3, 1)
    assert next_document_number(kuwait, "INV", on) == "KW-INV-2026-00001"
    assert next_document_number(kuwait, "INV", on) == "KW-INV-2026-00002"
    assert next_document_number(kuwait, "QT", on) == "KW-QT-2026-00001"
    assert next_document_number(uae, "INV", on) == "AE-INV-2026-00001"


def test_document_numbers_restart_each_year(kuwait):
    next_document_number(kuwait, "INV", date(2026, 12, 31))
    assert next_document_number(kuwait, "INV", date(2027, 1, 1)) == "KW-INV-2027-00001"


def _rate(src, dst, rate, on):
    ExchangeRate.objects.create(
        currency_id=src, to_currency_id=dst, rate=Decimal(rate), effective_date=on
    )


def test_exchange_rate_uses_latest_rate_on_or_before_date():
    _rate("KWD", "AED", "11.90", date(2026, 1, 1))
    _rate("KWD", "AED", "12.00", date(2026, 6, 1))
    assert get_exchange_rate("KWD", "AED", date(2026, 3, 1)) == Decimal("11.90")
    assert get_exchange_rate("KWD", "AED", date(2026, 7, 1)) == Decimal("12.00")


def test_exchange_rate_falls_back_to_inverse_and_rounds_to_target_decimals():
    _rate("KWD", "AED", "12.00", date(2026, 1, 1))
    # 100 AED -> KWD = 100 / 12 = 8.3333.. -> 3 decimals for KWD
    assert convert_amount(Decimal("100"), "AED", "KWD", date(2026, 2, 1)) == Decimal("8.333")
    assert convert_amount(Decimal("1"), "KWD", "AED", date(2026, 2, 1)) == Decimal("12.00")


def test_missing_rate_raises():
    with pytest.raises(ExchangeRateMissing):
        get_exchange_rate("KWD", "CNY", date(2026, 1, 1))


def test_convert_endpoint(api, make_user):
    _rate("KWD", "USD", "3.25", date(2026, 1, 1))
    api.force_authenticate(make_user())
    response = api.get(
        "/api/v1/organization/exchange-rates/convert/",
        {"amount": "10", "from_currency": "KWD", "to_currency": "USD", "date": "2026-02-01"},
    )
    assert response.status_code == 200
    assert response.json()["converted_amount"] == "32.50"


def test_warehouse_branch_must_match_company(api, make_user, kuwait, uae):
    from apps.organization.models import Branch

    dubai = Branch.objects.create(company=uae, code="DXB", name="Dubai", branch_type="office")
    api.force_authenticate(make_user(is_superuser=True))
    response = api.post(
        "/api/v1/organization/warehouses/",
        {
            "company": kuwait.id,
            "branch": dubai.id,
            "code": "X",
            "name": "X",
            "warehouse_type": "main",
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "validation_error"
    assert "branch" in body["fields"]


def test_exchange_rate_cache_refreshes_when_rates_change(django_capture_on_commit_callbacks):
    _rate("KWD", "AED", "12.00", date(2026, 1, 1))
    assert get_exchange_rate("KWD", "AED", date(2026, 2, 1)) == Decimal("12.00")
    with django_capture_on_commit_callbacks(execute=True):
        _rate("KWD", "AED", "12.10", date(2026, 1, 15))
    assert get_exchange_rate("KWD", "AED", date(2026, 2, 1)) == Decimal("12.10")


# --- All countries --------------------------------------------------------------------


def test_every_country_is_loaded_with_its_currency():
    from apps.organization.models import Country, Currency

    assert Country.objects.count() > 240
    saudi = Country.objects.get(code="SA")
    assert (saudi.name, saudi.default_currency_id) == ("Saudi Arabia", "SAR")
    assert saudi.name_ar
    assert Currency.objects.get(code="BHD").decimal_places == 3
    assert Currency.objects.get(code="KWD").symbol == "KD"  # existing values are kept


def test_country_list_can_return_everything_in_one_page(api, make_user):
    api.force_authenticate(make_user())
    body = api.get("/api/v1/organization/countries/", {"page_size": 500}).json()
    assert body["count"] == len(body["results"]) > 240


# --- Company logo ---------------------------------------------------------------------


def _image(fmt="PNG", size=(64, 32), name="logo.png"):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", size, (10, 10, 10)).save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def test_upload_replace_and_remove_logo(api, make_user, kuwait):
    from django.core.files.storage import default_storage

    api.force_authenticate(make_user(is_superuser=True))
    url = f"/api/v1/organization/companies/{kuwait.id}/logo/"

    first = api.post(url, {"logo": _image()}, format="multipart")
    assert first.status_code == 200, first.json()
    assert first.json()["logo_url"]
    kuwait.refresh_from_db()
    old_name = kuwait.logo.name
    assert old_name.startswith(f"companies/{kuwait.id}/logo-")

    second = api.post(url, {"logo": _image("JPEG", name="logo.jpg")}, format="multipart")
    assert second.status_code == 200
    kuwait.refresh_from_db()
    assert kuwait.logo.name != old_name
    assert not default_storage.exists(old_name)  # replaced file is removed

    removed = api.delete(url)
    assert removed.status_code == 200
    assert removed.json()["logo_url"] is None


def test_logo_must_be_a_real_image(api, make_user, kuwait):
    from django.core.files.uploadedfile import SimpleUploadedFile

    api.force_authenticate(make_user(is_superuser=True))
    url = f"/api/v1/organization/companies/{kuwait.id}/logo/"
    fake = SimpleUploadedFile("logo.png", b"<svg onload=alert(1)>", content_type="image/png")
    gif = _image("GIF", name="logo.gif")

    for upload in (fake, gif):
        response = api.post(url, {"logo": upload}, format="multipart")
        assert response.status_code == 400
        assert "logo" in response.json()["fields"]


def test_logo_size_limit(api, make_user, kuwait, settings):
    settings.MAX_IMAGE_UPLOAD_BYTES = 100
    api.force_authenticate(make_user(is_superuser=True))
    response = api.post(
        f"/api/v1/organization/companies/{kuwait.id}/logo/", {"logo": _image()}, format="multipart"
    )
    assert response.status_code == 400
    assert "MB" in response.json()["fields"]["logo"]


def test_logo_needs_update_permission_in_that_company(api, make_role, make_user, kuwait, uae):
    editor = make_role("company-editor", "organization.company.view", "organization.company.update")
    api.force_authenticate(make_user((editor, kuwait, None)))
    own = api.post(
        f"/api/v1/organization/companies/{kuwait.id}/logo/", {"logo": _image()}, format="multipart"
    )
    other = api.post(
        f"/api/v1/organization/companies/{uae.id}/logo/", {"logo": _image()}, format="multipart"
    )
    assert own.status_code == 200
    assert other.status_code == 404
