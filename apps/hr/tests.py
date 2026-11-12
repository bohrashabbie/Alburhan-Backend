from datetime import date
from decimal import Decimal

import pytest
from django.core import mail

from apps.accounting.models import Journal, JournalLine
from apps.accounting.setup import set_up_company
from apps.hr.models import Employee, LeaveRequest, LeaveType
from apps.hr.services import decision_token, hr_settings

pytestmark = pytest.mark.django_db

ME = "/api/v1/hr/me/"
EMP = "/api/v1/hr/employees/"
LEAVE = "/api/v1/hr/leave/"
PAY = "/api/v1/hr/payroll/"
DECIDE = "/api/v1/hr/leave-decision/{}/"


@pytest.fixture(autouse=True)
def _emails_send_at_once(monkeypatch):
    # Emails go out after the transaction commits; tests never commit, so run them straight away.
    monkeypatch.setattr(
        "django.db.transaction.on_commit", lambda func, using=None, robust=False: func()
    )


@pytest.fixture
def books(kuwait):
    set_up_company(kuwait, 2026)


@pytest.fixture
def team(kuwait, books, make_user):
    settings = hr_settings(kuwait)
    settings.hr_email = "hr@alburhan.test"
    settings.save()
    boss_login = make_user(email="khaled@alburhan.test")
    boss = Employee.objects.create(
        company=kuwait,
        first_name="Khaled",
        last_name="Al-Sabah",
        email="khaled@alburhan.test",
        user=boss_login,
        hire_date=date(2020, 1, 1),
        basic_salary=Decimal("1500"),
    )
    worker_login = make_user(email="ravi@alburhan.test")
    worker = Employee.objects.create(
        company=kuwait,
        first_name="Ravi",
        last_name="Menon",
        email="ravi@alburhan.test",
        user=worker_login,
        manager=boss,
        hire_date=date(2024, 3, 1),
        basic_salary=Decimal("600"),
        housing_allowance=Decimal("150"),
    )
    return {"boss": boss, "boss_login": boss_login, "worker": worker, "worker_login": worker_login}


def _type(company, code):
    return LeaveType.objects.get(company=company, code=code)


def test_leave_request_emails_the_manager_and_is_decided_from_the_link(api, kuwait, team):
    api.force_authenticate(team["worker_login"])
    me = api.get(ME).json()
    assert me["employee"]["manager"] == "Khaled Al-Sabah"
    annual = next(row for row in me["balances"] if row["code"] == "annual")
    assert annual["remaining"] == "30.0"

    # Sun 8 – Thu 12 Nov 2026: five working days (Fri/Sat are the Kuwait weekend).
    response = api.post(
        ME + "leave/",
        {
            "leave_type": _type(kuwait, "annual").pk,
            "start_date": "2026-11-08",
            "end_date": "2026-11-14",
            "reason": "Family visit",
        },
    )
    assert response.status_code == 201, response.json()
    leave = response.json()
    assert (
        leave["days"] == "5.0"
        and leave["status"] == "pending"
        and leave["approver_name"] == "Khaled Al-Sabah"
    )

    sent = {m.to[0]: m for m in mail.outbox}
    assert set(sent) == {"khaled@alburhan.test", "hr@alburhan.test", "ravi@alburhan.test"}
    to_manager = sent["khaled@alburhan.test"]
    assert "ravi@alburhan.test" in to_manager.body  # the employee's email is in the message
    link = next(
        word
        for word in to_manager.body.split()
        if word.startswith("https://erp.test/leave-decision/")
    )
    token = link.rsplit("/", 1)[1]

    api.force_authenticate(None)
    summary = api.get(DECIDE.format(token)).json()
    assert summary["employee"] == "Ravi Menon" and summary["status"] == "pending"
    mail.outbox.clear()
    decided = api.post(DECIDE.format(token), {"decision": "approve"})
    assert decided.status_code == 200 and decided.json()["status"] == "approved"
    stored = LeaveRequest.objects.get(pk=leave["id"])
    assert stored.decided_via == "email" and stored.decided_by_name == "Khaled Al-Sabah"
    assert {m.to[0] for m in mail.outbox} == {"ravi@alburhan.test", "hr@alburhan.test"}
    # A link works once.
    again = api.post(DECIDE.format(token), {"decision": "reject", "note": "No"})
    assert again.status_code == 400

    api.force_authenticate(team["worker_login"])
    annual = next(row for row in api.get(ME).json()["balances"] if row["code"] == "annual")
    assert annual["taken"] == "5.0" and annual["remaining"] == "25.0"


def test_links_cannot_be_forged_and_rejecting_needs_a_reason(api, kuwait, team):
    api.force_authenticate(team["worker_login"])
    leave = api.post(
        ME + "leave/",
        {
            "leave_type": _type(kuwait, "unpaid").pk,
            "start_date": "2026-11-15",
            "end_date": "2026-11-16",
        },
    ).json()
    token = decision_token(LeaveRequest.objects.get(pk=leave["id"]))
    api.force_authenticate(None)
    assert api.get(DECIDE.format(token[:-3] + "abc")).status_code == 400
    assert api.post(DECIDE.format(token), {"decision": "reject"}).status_code == 400
    rejected = api.post(
        DECIDE.format(token), {"decision": "reject", "note": "Stock count that week"}
    )
    assert rejected.json()["status"] == "rejected"

    # Moving the request to another approver invalidates the old link.

    api.force_authenticate(team["worker_login"])
    second = api.post(
        ME + "leave/",
        {
            "leave_type": _type(kuwait, "unpaid").pk,
            "start_date": "2026-11-22",
            "end_date": "2026-11-22",
        },
    ).json()
    request = LeaveRequest.objects.get(pk=second["id"])
    old = decision_token(request)
    request.approver = None
    request.save()
    api.force_authenticate(None)
    assert api.get(DECIDE.format(old)).status_code == 400


def test_balance_overlap_and_manager_in_app(api, kuwait, team, make_user):
    api.force_authenticate(team["worker_login"])
    too_long = api.post(
        ME + "leave/",
        {
            "leave_type": _type(kuwait, "sick").pk,
            "start_date": "2026-11-01",
            "end_date": "2026-11-30",
        },
    )
    assert too_long.status_code == 400 and "left this year" in str(too_long.json())
    first = api.post(
        ME + "leave/",
        {
            "leave_type": _type(kuwait, "annual").pk,
            "start_date": "2026-12-06",
            "end_date": "2026-12-07",
        },
    ).json()
    clash = api.post(
        ME + "leave/",
        {
            "leave_type": _type(kuwait, "annual").pk,
            "start_date": "2026-12-07",
            "end_date": "2026-12-08",
        },
    )
    assert clash.status_code == 400 and first["number"] in str(clash.json())

    # Someone else can't decide it; the manager can, in the app.
    api.force_authenticate(make_user())
    assert api.post(ME + f"approvals/{first['id']}/", {"decision": "approve"}).status_code == 403
    api.force_authenticate(team["boss_login"])
    waiting = api.get(ME).json()["approvals"]
    assert [item["id"] for item in waiting] == [first["id"]]
    assert (
        api.post(ME + f"approvals/{first['id']}/", {"decision": "approve"}).json()["decided_via"]
        == "app"
    )

    # The employee can cancel approved leave that hasn't been paid yet.
    api.force_authenticate(team["worker_login"])
    assert api.post(ME + f"leave/{first['id']}/cancel/").json()["status"] == "cancelled"


def test_payroll_deducts_unpaid_leave_posts_and_pays(api, kuwait, team, make_user):
    unpaid = LeaveRequest.objects.create(
        company=kuwait,
        employee=team["worker"],
        leave_type=_type(kuwait, "unpaid"),
        start_date=date(2026, 11, 15),
        end_date=date(2026, 11, 16),
        days=Decimal("2"),
        status=LeaveRequest.Status.APPROVED,
    )
    # Joined on 16 Nov: paid for 15 of 30 days.
    Employee.objects.create(
        company=kuwait,
        first_name="Sara",
        last_name="Haddad",
        hire_date=date(2026, 11, 16),
        basic_salary=Decimal("900"),
    )
    api.force_authenticate(make_user(is_superuser=True))
    run = api.post(PAY, {"company": kuwait.pk, "year": 2026, "month": 11})
    assert run.status_code == 201, run.json()
    run = run.json()
    slips = {slip["employee_name"]: slip for slip in run["payslips"]}
    ravi = slips["Ravi Menon"]
    assert ravi["gross"] == "750.000" and ravi["unpaid_leave_days"] == "2.0"
    assert ravi["unpaid_leave_deduction"] == "57.692"  # 750 ÷ 26 × 2
    assert slips["Sara Haddad"]["gross"] == "450.000"
    unpaid.refresh_from_db()
    assert unpaid.payroll_run_id == run["id"]

    # A bonus typed on a payslip survives a recalculation.
    api.post(
        PAY + f"{run['id']}/payslips/{ravi['id']}/",
        {"additions": "25", "deductions": "0", "notes": "Stock count overtime"},
    )
    run = api.post(PAY + f"{run['id']}/recalculate/").json()
    ravi = next(slip for slip in run["payslips"] if slip["employee_name"] == "Ravi Menon")
    assert ravi["additions"] == "25.000" and ravi["net"] == "717.308"

    posted = api.post(PAY + f"{run['id']}/post/").json()
    assert posted["status"] == "posted" and posted["journal_entry_number"]
    lines = JournalLine.objects.filter(entry_id=posted["journal_entry"])
    by_code = {line.account.code: (line.debit, line.credit) for line in lines}
    total = Decimal(posted["total_net"])
    assert by_code["6100"] == (total, Decimal("0")) and by_code["2150"] == (Decimal("0"), total)
    assert api.post(PAY + f"{run['id']}/recalculate/").status_code == 400

    bank = Journal.objects.get(company=kuwait, code="BK")
    paid = api.post(PAY + f"{run['id']}/pay/", {"journal": bank.pk, "date": "2026-11-30"}).json()
    assert paid["status"] == "paid" and paid["payment_entry_number"]
    assert api.delete(PAY + f"{run['id']}/").status_code == 400


def test_hr_access_follows_country(api, kuwait, uae, team, make_role, make_user):
    Employee.objects.create(company=uae, first_name="Dubai", hire_date=date(2025, 1, 1))
    hr = make_role("hr-officer", "hr.employee.view", "hr.leave.view")
    api.force_authenticate(make_user((hr, kuwait, None)))
    names = {row["full_name"] for row in api.get(EMP).json()["results"]}
    assert names == {"Khaled Al-Sabah", "Ravi Menon"}


def test_salaries_page_sets_pay_with_history(api, kuwait, team, make_role, make_user):
    hr = make_role(
        "hr-pay", "hr.employee.view", "hr.employee.update", "hr.salary.view", "hr.salary.update"
    )
    viewer = make_role("hr-view", "hr.employee.view", "hr.employee.update")
    worker = team["worker"]
    api.force_authenticate(make_user((viewer, kuwait, None)))
    # Salary can't be changed from the employee form, nor seen without the salary permission.
    api.patch(EMP + f"{worker.pk}/", {"basic_salary": "999"})
    worker.refresh_from_db()
    assert worker.basic_salary == Decimal("600")
    assert api.get("/api/v1/hr/salaries/").status_code == 403

    api.force_authenticate(make_user((hr, kuwait, None)))
    url = f"/api/v1/hr/salaries/{worker.pk}/set/"
    body = api.post(
        url,
        {
            "effective_from": "2026-12-01",
            "basic_salary": "650",
            "housing_allowance": "150",
            "note": "Yearly raise",
        },
    ).json()
    assert body["monthly_pay"] == "800.000" and body["changed_on"] == "2026-12-01"
    assert (
        body["history"][0]["note"] == "Yearly raise"
        and body["history"][0]["monthly_pay"] == "800.000"
    )
    worker.refresh_from_db()
    assert worker.basic_salary == Decimal("650")
    assert api.post(url, {"effective_from": "2026-12-01", "basic_salary": "-1"}).status_code == 400
    rows = {
        r["full_name"]: r
        for r in api.get(f"/api/v1/hr/salaries/?company={kuwait.pk}").json()["results"]
    }
    assert rows["Ravi Menon"]["monthly_pay"] == "800.000"
