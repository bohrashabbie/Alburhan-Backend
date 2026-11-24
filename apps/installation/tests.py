import pytest

from apps.accounting.setup import set_up_company
from apps.catalog.models import Product, ProductCategory, UnitOfMeasure
from apps.installation.models import Technician
from apps.partners.models import Customer

pytestmark = pytest.mark.django_db

TECH = "/api/v1/installation/technicians/"
WO = "/api/v1/installation/work-orders/"
INV = "/api/v1/sales/invoices/"


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


@pytest.fixture
def customer(kuwait):
    return Customer.objects.create(company=kuwait, code="KW-CUS-00001", name="Ahmad Al-Sabah")


@pytest.fixture
def install_service(kuwait, books):
    family = ProductCategory.objects.create(code="svc", name="Services")
    return Product.objects.create(
        sku="INSTALL",
        name="Installation",
        category=family,
        uom=UnitOfMeasure.objects.get(code="HR"),
        product_type="service",  # no income account of its own: services default to 4300
    )


@pytest.fixture
def admin_api(api, make_user):
    api.force_authenticate(make_user(is_superuser=True))
    return api


@pytest.fixture
def techs(admin_api, kuwait, showroom1):
    made = []
    for name, cost in (("Rajesh", "3"), ("Omar", "4")):
        response = admin_api.post(
            TECH,
            {
                "company": kuwait.id,
                "name": name,
                "branch": showroom1.id,
                "hourly_cost": cost,
                "skills": "electrical",
            },
            format="json",
        )
        assert response.status_code == 201, response.json()
        made.append(response.json())
    return made


REQUEST = {
    "title": "Install 24 downlights",
    "site_address": "Salwa block 4, street 12, house 7",
    "contact_name": "Ahmad",
    "contact_phone": "+96550000000",
    "location_link": "https://maps.google.com/?q=29.29,48.07",
    "requested_date": "2026-03-11",
}


def schedule(client, job_id, techs, day="2026-03-12", at="09:00", hours="3"):
    return client.post(
        f"{WO}{job_id}/schedule/",
        {
            "scheduled_date": day,
            "scheduled_time": at,
            "duration_hours": hours,
            "technicians": [tech["id"] for tech in techs],
        },
        format="json",
    )


@pytest.fixture
def request_job(admin_api, kuwait, showroom1, customer):
    response = admin_api.post(
        WO,
        {"company": kuwait.id, "customer": customer.id, "branch": showroom1.id, **REQUEST},
        format="json",
    )
    assert response.status_code == 201, response.json()
    return response.json()


@pytest.fixture
def job(admin_api, request_job, techs):
    response = schedule(admin_api, request_job["id"], techs)
    assert response.status_code == 200, response.json()
    return response.json()


def signature_png(blank=False) -> str:
    import base64
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (300, 120), (0, 0, 0, 0))
    if not blank:
        ImageDraw.Draw(image).line([(20, 80), (120, 30), (260, 90)], fill=(0, 0, 0, 255), width=3)
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()


def test_request_waits_for_the_supervisor(admin_api, kuwait, request_job, techs):
    assert request_job["status"] == "requested" and request_job["number"].startswith("KW-WO-")
    assert request_job["scheduled_date"] is None and request_job["technicians"] == []
    assert request_job["requested_date"] == "2026-03-11"
    assert request_job["location_link"].startswith("https://maps.google.com")
    # Not on the board, and the date and technicians can't be slipped in through an edit.
    cal = admin_api.get(
        f"{WO}calendar/", {"company": kuwait.id, "from": "2026-03-09", "to": "2026-03-15"}
    ).json()
    assert cal == []
    edited = admin_api.patch(
        f"{WO}{request_job['id']}/",
        {"scheduled_date": "2026-03-12", "technicians": [techs[0]["id"]], "contact_name": "Sara"},
        format="json",
    ).json()
    assert edited["scheduled_date"] is None and edited["technicians"] == []
    assert edited["contact_name"] == "Sara"
    assert (
        admin_api.post(
            f"{WO}{request_job['id']}/complete/", {"labour_hours": "1"}, format="json"
        ).status_code
        == 400
    )
    waiting = admin_api.get(WO, {"status": "requested"}).json()["results"]
    assert [row["id"] for row in waiting] == [request_job["id"]]


def test_work_order_lifecycle_and_billing(admin_api, kuwait, job, install_service, techs):
    assert job["status"] == "scheduled" and job["scheduled_date"] == "2026-03-12"
    assert job["scheduled_by_name"] and job["scheduled_at"]
    assert sorted(job["technician_names"]) == ["Omar", "Rajesh"]
    assert admin_api.get(f"{TECH}{techs[0]['id']}/").json()["open_jobs"] == 1

    cal = admin_api.get(
        f"{WO}calendar/", {"company": kuwait.id, "from": "2026-03-09", "to": "2026-03-15"}
    ).json()
    assert cal[0]["date"] == "2026-03-12" and cal[0]["jobs"][0]["time"] == "09:00"

    assert (
        admin_api.post(
            f"{WO}{job['id']}/bill/",
            {"product": install_service.id, "unit_price": "50"},
            format="json",
        ).status_code
        == 400
    )
    started = admin_api.post(f"{WO}{job['id']}/start/").json()
    assert started["status"] == "in_progress" and started["started_at"]
    done = admin_api.post(
        f"{WO}{job['id']}/complete/",
        {"labour_hours": "3.5", "signed_by": "Ahmad", "completion_notes": "All good"},
        format="json",
    )
    assert done.status_code == 200, done.json()
    assert done.json()["labour_cost"] == "24.500"  # 3.5 h × (3 + 4)
    assert admin_api.patch(f"{WO}{job['id']}/", {"title": "x"}, format="json").status_code == 400

    bill = admin_api.post(
        f"{WO}{job['id']}/bill/",
        {"product": install_service.id, "quantity": "3.5", "unit_price": "10"},
        format="json",
    )
    assert bill.status_code == 201, bill.json()
    invoice = bill.json()
    assert invoice["work_order"] == job["id"] and invoice["total"] == "35.000"
    posted = admin_api.post(f"{INV}{invoice['id']}/post/").json()
    from apps.accounting.models import JournalEntry

    entry = JournalEntry.objects.get(pk=posted["journal_entry"])
    assert {(ln.account.code, str(ln.credit)) for ln in entry.lines.filter(credit__gt=0)} == {
        ("4300", "35.000")
    }
    assert admin_api.get(f"{WO}{job['id']}/").json()["invoice_numbers"] == [posted["number"]]


def test_cancel_and_rules(admin_api, kuwait, showroom1, customer, job, techs, uae):
    assert (
        admin_api.post(f"{WO}{job['id']}/cancel/", {"reason": ""}, format="json").status_code == 400
    )
    cancelled = admin_api.post(
        f"{WO}{job['id']}/cancel/", {"reason": "Customer postponed"}, format="json"
    ).json()
    assert cancelled["status"] == "cancelled" and cancelled["cancel_reason"] == "Customer postponed"
    assert admin_api.post(f"{WO}{job['id']}/start/").status_code == 400

    Technician.objects.filter(pk=techs[0]["id"]).update(is_active=False)
    fresh = admin_api.post(
        WO,
        {"company": kuwait.id, "title": "y", "customer": customer.id, "branch": showroom1.id},
        format="json",
    ).json()
    bad = schedule(admin_api, fresh["id"], [techs[0]])
    assert bad.status_code == 400 and "technicians" in bad.json()["fields"]
    assert schedule(admin_api, fresh["id"], []).json()["fields"]["technicians"]
    assert (
        "duration_hours" in schedule(admin_api, fresh["id"], [techs[1]], hours="0").json()["fields"]
    )
    # A request that never started can be deleted.
    assert admin_api.delete(f"{WO}{fresh['id']}/").status_code == 204


def test_branch_scope(api, make_role, make_user, kuwait, showroom1, showroom2, customer, job):
    role = make_role("installer", "installation.work_order.view", "installation.technician.view")
    api.force_authenticate(make_user((role, kuwait, showroom2)))
    assert api.get(WO).json()["results"] == []
    assert (
        api.get(
            f"{WO}calendar/", {"company": kuwait.id, "from": "2026-03-09", "to": "2026-03-15"}
        ).json()
        == []
    )
    api.force_authenticate(make_user((role, kuwait, showroom1)))
    assert len(api.get(WO).json()["results"]) == 1
    assert len(api.get(TECH).json()["results"]) == 2


def test_supervisor_schedules_and_technicians_work_their_own_jobs(
    api, make_role, make_user, kuwait, showroom1, customer, request_job, techs
):
    from apps.users.models import Reminder

    staff_role = make_role(
        "staff", "installation.work_order.view", "installation.work_order.create"
    )
    tech_role = make_role(
        "tech", "installation.work_order.view", "installation.work_order.complete"
    )
    boss_role = make_role(
        "supervisor",
        "installation.work_order.view",
        "installation.work_order.update",
        "installation.work_order.schedule",
        "installation.technician.view",
    )
    staff = make_user((staff_role, kuwait, None))
    rajesh = make_user((tech_role, kuwait, None))
    omar = make_user((tech_role, kuwait, None))
    boss = make_user((boss_role, kuwait, None))
    Technician.objects.filter(pk=techs[0]["id"]).update(user=rajesh)
    Technician.objects.filter(pk=techs[1]["id"]).update(user=omar)

    # 1. Staff can submit a request but not schedule it.
    api.force_authenticate(staff)
    submitted = api.post(
        WO,
        {"company": kuwait.id, "customer": customer.id, "branch": showroom1.id, **REQUEST},
        format="json",
    )
    assert submitted.status_code == 201, submitted.json()
    job_id = submitted.json()["id"]
    assert submitted.json()["requested_by_name"]
    assert schedule(api, job_id, techs[:1]).status_code == 403

    # 2-3. The supervisor sets the date, time and the technician; Rajesh gets a note.
    api.force_authenticate(boss)
    done = schedule(api, job_id, techs[:1], day="2026-03-14", at="10:30")
    assert done.status_code == 200, done.json()
    assert done.json()["status"] == "scheduled" and done.json()["technician_names"] == ["Rajesh"]
    note = Reminder.objects.get(recipient=rajesh)
    assert done.json()["number"] in note.message and "14/03/2026 10:30" in note.message
    assert not Reminder.objects.filter(recipient=omar).exists()
    # Moving the job tells the same technician again.
    schedule(api, job_id, techs[:1], day="2026-03-15", at="10:30")
    assert Reminder.objects.filter(recipient=rajesh).count() == 2

    # 4. On the schedule board.
    cal = api.get(
        f"{WO}calendar/", {"company": kuwait.id, "from": "2026-03-09", "to": "2026-03-22"}
    ).json()
    assert [job["id"] for day in cal for job in day["jobs"]] == [job_id]

    # 5. Only the assigned technician completes it; "mine" lists their own jobs.
    api.force_authenticate(omar)
    assert api.get(WO, {"mine": "true"}).json()["results"] == []
    assert api.post(f"{WO}{job_id}/start/").status_code == 400
    assert schedule(api, job_id, techs).status_code == 403
    api.force_authenticate(rajesh)
    assert [row["id"] for row in api.get(WO, {"mine": "true"}).json()["results"]] == [job_id]
    assert api.post(f"{WO}{job_id}/start/").json()["status"] == "in_progress"
    completed = api.post(
        f"{WO}{job_id}/complete/",
        {"labour_hours": "2", "completion_notes": "12 downlights fitted and tested"},
        format="json",
    ).json()
    assert completed["status"] == "completed" and completed["awaiting_signature"] is True
    assert completed["completed_by_name"]
    waiting = api.get(WO, {"awaiting_signature": "true"}).json()["results"]
    assert [row["id"] for row in waiting] == [job_id]

    # 6. The customer signs on Rajesh's screen.
    api.force_authenticate(omar)
    assert (
        api.post(
            f"{WO}{job_id}/sign/",
            {"signed_by": "Ahmad", "signature": signature_png()},
            format="json",
        ).status_code
        == 400
    )
    api.force_authenticate(rajesh)
    signed = api.post(
        f"{WO}{job_id}/sign/", {"signed_by": "Ahmad", "signature": signature_png()}, format="json"
    )
    assert signed.status_code == 200, signed.json()
    signed = signed.json()
    assert signed["signed_by"] == "Ahmad" and signed["signed_at"]
    assert signed["signature_url"].endswith(".png") and signed["awaiting_signature"] is False
    again = api.post(
        f"{WO}{job_id}/sign/", {"signed_by": "Ahmad", "signature": signature_png()}, format="json"
    )
    assert again.status_code == 400


def test_signature_is_checked(admin_api, job):
    url = f"{WO}{job['id']}/sign/"
    good = {"signed_by": "Ahmad", "signature": signature_png()}
    assert admin_api.post(url, good, format="json").status_code == 400  # not completed yet
    admin_api.post(f"{WO}{job['id']}/complete/", {"labour_hours": "1"}, format="json")
    for body, field in (
        ({"signed_by": "Ahmad", "signature": signature_png(blank=True)}, "signature"),
        (
            {"signed_by": "Ahmad", "signature": "data:image/png;base64,bm90IGFuIGltYWdl"},
            "signature",
        ),
        ({"signed_by": "Ahmad", "signature": "data:text/html;base64,PGI+"}, "signature"),
        ({"signed_by": "  ", "signature": signature_png()}, "signed_by"),
    ):
        response = admin_api.post(url, body, format="json")
        assert response.status_code == 400 and field in response.json()["fields"], body
    assert admin_api.post(url, good, format="json").status_code == 200
