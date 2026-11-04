from datetime import date
from decimal import Decimal

import pytest

from apps.catalog.models import PriceList, PriceListItem, Product, ProductCategory, UnitOfMeasure
from apps.catalog.services import get_unit_price

pytestmark = pytest.mark.django_db


@pytest.fixture
def family(db):
    return ProductCategory.objects.create(code="recessed-down-light", name="Recessed Down Light")


@pytest.fixture
def pcs(db):
    return UnitOfMeasure.objects.get(code="PCS")


@pytest.fixture
def downlight(family, pcs):
    return Product.objects.create(
        sku="MS-DL-R01-10W", name="Downlight 10W", category=family, uom=pcs
    )


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


def product_payload(family, pcs, **extra):
    return {"sku": "ms-dl-01", "name": "Downlight", "category": family.id, "uom": pcs.id, **extra}


# --- Products ---------------------------------------------------------------------------


def test_units_are_preloaded():
    assert UnitOfMeasure.objects.get(code="M").allow_decimal
    assert not UnitOfMeasure.objects.get(code="PCS").allow_decimal


def test_create_product_with_lighting_spec(admin_api, family, pcs):
    response = admin_api.post(
        "/api/v1/catalog/products/",
        product_payload(
            family,
            pcs,
            wattage="10.5",
            lumens=950,
            cct="3000K",
            cri=90,
            beam_angle="24°",
            ip_rating="ip44",
            dimming="dali",
            driver="Meanwell",
        ),
    )
    assert response.status_code == 201, response.json()
    body = response.json()
    assert body["sku"] == "MS-DL-01"  # normalised to upper case
    assert body["ip_rating"] == "IP44"
    assert body["category_name"] == "Recessed Down Light"
    assert body["uom_code"] == "PCS"


def test_sku_must_be_unique(admin_api, family, pcs, downlight):
    response = admin_api.post(
        "/api/v1/catalog/products/", product_payload(family, pcs, sku="ms-dl-r01-10w")
    )
    assert response.status_code == 400
    assert "sku" in response.json()["fields"]


def test_barcode_optional_but_unique(admin_api, family, pcs):
    first = admin_api.post("/api/v1/catalog/products/", product_payload(family, pcs, sku="A"))
    second = admin_api.post("/api/v1/catalog/products/", product_payload(family, pcs, sku="B"))
    assert first.status_code == second.status_code == 201  # two products without barcode

    with_code = admin_api.post(
        "/api/v1/catalog/products/", product_payload(family, pcs, sku="C", barcode="6291234")
    )
    clash = admin_api.post(
        "/api/v1/catalog/products/", product_payload(family, pcs, sku="D", barcode="6291234")
    )
    assert with_code.status_code == 201
    assert clash.status_code == 400
    assert "barcode" in clash.json()["fields"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ip_rating", "IP99"),
        ("ip_rating", "44"),
        ("cri", 120),
        ("wattage", "-5"),
        ("ik_rating", "IK11"),
    ],
)
def test_invalid_specs_rejected(admin_api, family, pcs, field, value):
    response = admin_api.post(
        "/api/v1/catalog/products/", product_payload(family, pcs, **{field: value})
    )
    assert response.status_code == 400
    assert field in response.json()["fields"]


def test_product_search_by_sku_and_name(admin_api, downlight, family, pcs):
    Product.objects.create(sku="MS-FL-50W", name="Flood light 50W", category=family, uom=pcs)
    by_sku = admin_api.get("/api/v1/catalog/products/", {"search": "DL-R01"}).json()
    by_name = admin_api.get("/api/v1/catalog/products/", {"search": "flood"}).json()
    assert [p["sku"] for p in by_sku["results"]] == ["MS-DL-R01-10W"]
    assert [p["sku"] for p in by_name["results"]] == ["MS-FL-50W"]


def test_product_in_use_cannot_be_deleted(admin_api, downlight, kuwait):
    price_list = PriceList.objects.create(
        company=kuwait, code="retail", name="Retail", currency_id="KWD"
    )
    PriceListItem.objects.create(
        price_list=price_list, product=downlight, unit_price=Decimal("4.500")
    )

    response = admin_api.delete(f"/api/v1/catalog/products/{downlight.id}/")

    assert response.status_code == 409
    assert response.json()["code"] == "in_use"
    assert Product.objects.filter(pk=downlight.pk).exists()


def test_category_cannot_be_its_own_parent(admin_api, family):
    child = ProductCategory.objects.create(code="child", name="Child", parent=family)
    response = admin_api.patch(f"/api/v1/catalog/categories/{family.id}/", {"parent": child.id})
    assert response.status_code == 400
    assert "parent" in response.json()["fields"]


def test_catalog_needs_permission(api, make_role, make_user, kuwait, downlight):
    viewer = make_role("catalog-viewer", "catalog.product.view")
    api.force_authenticate(make_user((viewer, kuwait, None)))

    assert api.get("/api/v1/catalog/products/").status_code == 200
    created = api.post("/api/v1/catalog/products/", {"sku": "X", "name": "X"})
    assert created.status_code == 403


# --- Price lists ------------------------------------------------------------------------


def test_only_one_default_price_list_per_company(admin_api, kuwait, uae):
    base = {"currency": "KWD", "is_default": True}
    first = admin_api.post(
        "/api/v1/catalog/price-lists/",
        {**base, "company": kuwait.id, "code": "retail", "name": "Retail"},
    )
    second = admin_api.post(
        "/api/v1/catalog/price-lists/",
        {**base, "company": kuwait.id, "code": "trade", "name": "Trade"},
    )
    other_company = admin_api.post(
        "/api/v1/catalog/price-lists/",
        {
            "company": uae.id,
            "currency": "AED",
            "is_default": True,
            "code": "retail",
            "name": "Retail",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 400
    assert "is_default" in second.json()["fields"]
    assert other_company.status_code == 201  # same code and default flag allowed in another company


def test_price_decimals_follow_currency(admin_api, kuwait, uae, downlight):
    kwd = PriceList.objects.create(company=kuwait, code="kw", name="KW", currency_id="KWD")
    aed = PriceList.objects.create(company=uae, code="ae", name="AE", currency_id="AED")
    url = "/api/v1/catalog/price-list-items/"

    ok_kwd = admin_api.post(
        url, {"price_list": kwd.id, "product": downlight.id, "unit_price": "4.125"}
    )
    bad_aed = admin_api.post(
        url, {"price_list": aed.id, "product": downlight.id, "unit_price": "45.125"}
    )
    ok_aed = admin_api.post(
        url, {"price_list": aed.id, "product": downlight.id, "unit_price": "45.13"}
    )

    assert ok_kwd.status_code == 201
    assert bad_aed.status_code == 400
    assert "unit_price" in bad_aed.json()["fields"]
    assert ok_aed.status_code == 201


def test_whole_units_only_for_pieces(admin_api, kuwait, downlight):
    price_list = PriceList.objects.create(company=kuwait, code="kw", name="KW", currency_id="KWD")
    response = admin_api.post(
        "/api/v1/catalog/price-list-items/",
        {
            "price_list": price_list.id,
            "product": downlight.id,
            "unit_price": "4",
            "min_quantity": "2.5",
        },
    )
    assert response.status_code == 400
    assert "min_quantity" in response.json()["fields"]


def test_price_breaks_and_validity(kuwait, downlight):
    price_list = PriceList.objects.create(
        company=kuwait,
        code="kw",
        name="KW",
        currency_id="KWD",
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
    )
    for qty, price in (("1", "5.000"), ("10", "4.500"), ("50", "4.000")):
        PriceListItem.objects.create(
            price_list=price_list,
            product=downlight,
            min_quantity=Decimal(qty),
            unit_price=Decimal(price),
        )
    on = date(2026, 6, 1)
    assert get_unit_price(price_list, downlight, Decimal("1"), on) == Decimal("5.000")
    assert get_unit_price(price_list, downlight, Decimal("9"), on) == Decimal("5.000")
    assert get_unit_price(price_list, downlight, Decimal("10"), on) == Decimal("4.500")
    assert get_unit_price(price_list, downlight, Decimal("200"), on) == Decimal("4.000")
    assert get_unit_price(price_list, downlight, Decimal("1"), date(2027, 1, 1)) is None


def test_price_endpoint(admin_api, kuwait, downlight):
    price_list = PriceList.objects.create(company=kuwait, code="kw", name="KW", currency_id="KWD")
    PriceListItem.objects.create(
        price_list=price_list, product=downlight, unit_price=Decimal("4.750")
    )
    body = admin_api.get(
        f"/api/v1/catalog/price-lists/{price_list.id}/price/",
        {"product": downlight.id, "quantity": "3"},
    ).json()
    assert body["unit_price"] == "4.750"
    assert body["currency"] == "KWD"


def test_price_lists_are_company_scoped(api, make_role, make_user, kuwait, uae, downlight):
    kw_list = PriceList.objects.create(company=kuwait, code="kw", name="KW", currency_id="KWD")
    ae_list = PriceList.objects.create(company=uae, code="ae", name="AE", currency_id="AED")
    PriceListItem.objects.create(price_list=ae_list, product=downlight, unit_price=Decimal("50"))
    editor = make_role("pricing", "catalog.price_list.view", "catalog.price_list.update")
    api.force_authenticate(make_user((editor, kuwait, None)))

    lists = api.get("/api/v1/catalog/price-lists/").json()["results"]
    items = api.get("/api/v1/catalog/price-list-items/").json()["results"]
    add_to_uae = api.post(
        "/api/v1/catalog/price-list-items/",
        {
            "price_list": ae_list.id,
            "product": downlight.id,
            "unit_price": "60",
            "min_quantity": "5",
        },
    )
    add_to_kw = api.post(
        "/api/v1/catalog/price-list-items/",
        {"price_list": kw_list.id, "product": downlight.id, "unit_price": "4.500"},
    )

    assert [row["id"] for row in lists] == [kw_list.id]
    assert items == []  # UAE prices are hidden
    assert add_to_uae.status_code == 403
    assert add_to_kw.status_code == 201


def test_branch_salesperson_can_read_company_price_lists(
    api, make_role, make_user, kuwait, showroom1
):
    PriceList.objects.create(company=kuwait, code="kw", name="KW", currency_id="KWD")
    seller = make_role("seller", "catalog.price_list.view")
    api.force_authenticate(make_user((seller, kuwait, showroom1)))
    assert api.get("/api/v1/catalog/price-lists/").json()["count"] == 1


def test_price_list_currency_locked_once_priced(admin_api, kuwait, downlight):
    price_list = PriceList.objects.create(company=kuwait, code="kw", name="KW", currency_id="KWD")
    PriceListItem.objects.create(price_list=price_list, product=downlight, unit_price=Decimal("4"))
    response = admin_api.patch(f"/api/v1/catalog/price-lists/{price_list.id}/", {"currency": "USD"})
    assert response.status_code == 400
    assert "currency" in response.json()["fields"]


# --- Images -------------------------------------------------------------------------------


def _png(name="photo.png"):
    import io

    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), (200, 200, 200)).save(buffer, "PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def test_family_and_product_photos(admin_api, family, downlight):
    for url in (
        f"/api/v1/catalog/categories/{family.id}/image/",
        f"/api/v1/catalog/products/{downlight.id}/image/",
    ):
        uploaded = admin_api.post(url, {"image": _png()}, format="multipart")
        assert uploaded.status_code == 200, uploaded.json()
        assert uploaded.json()["image_url"]
        removed = admin_api.delete(url)
        assert removed.json()["image_url"] is None


def test_photo_must_be_an_image(admin_api, downlight):
    from django.core.files.uploadedfile import SimpleUploadedFile

    fake = SimpleUploadedFile("x.png", b"not an image", content_type="image/png")
    response = admin_api.post(
        f"/api/v1/catalog/products/{downlight.id}/image/", {"image": fake}, format="multipart"
    )
    assert response.status_code == 400
    assert "image" in response.json()["fields"]


def test_photo_needs_update_permission(api, make_role, make_user, kuwait, downlight):
    viewer = make_role("viewer", "catalog.product.view")
    api.force_authenticate(make_user((viewer, kuwait, None)))
    response = api.post(
        f"/api/v1/catalog/products/{downlight.id}/image/", {"image": _png()}, format="multipart"
    )
    assert response.status_code == 403
