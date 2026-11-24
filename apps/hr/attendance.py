"""Attendance (who was in, late, on a half day or absent) and the leave and absence report.

Days on approved leave are never marked here: they come from the leave requests, so an employee is
never both "absent" and "on leave" for the same day."""

from collections import defaultdict
from datetime import date, time
from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import Attendance, Employee, LeaveAdjustment, LeaveRequest, LeaveType
from .services import HRError, ensure_leave_types, hr_settings

ZERO = Decimal("0")


def _days(value) -> str:
    return f"{Decimal(value):.1f}"


STATUSES = {choice for choice, _ in Attendance.Status.choices}


def company_today(company) -> date:
    """Today where the company is (the server may sit in another time zone)."""
    try:
        return timezone.localdate(timezone=ZoneInfo(company.timezone))
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.localdate()


def _working(employee: Employee, day: date) -> bool:
    return employee.hire_date <= day and (employee.end_date is None or employee.end_date >= day)


def _on_leave(employees, day: date) -> dict[int, str]:
    """employee id -> leave type name, for approved leave covering the day."""
    found = (
        LeaveRequest.objects.filter(
            employee__in=employees,
            status=LeaveRequest.Status.APPROVED,
            start_date__lte=day,
            end_date__gte=day,
        )
        .select_related("leave_type")
        .order_by("start_date")
    )
    return {row.employee_id: row.leave_type.name for row in found}


def register(company, day: date, branch_ids=None, branch=None) -> dict:
    """The day's sheet: every employee working that day, what is marked, and who is on leave."""
    people = Employee.objects.filter(company=company, is_active=True).select_related(
        "branch", "department"
    )
    if branch_ids is not None:
        people = people.filter(branch_id__in=branch_ids)
    if branch:
        people = people.filter(branch_id=branch)
    people = [person for person in people if _working(person, day)]
    marked = {
        row.employee_id: row for row in Attendance.objects.filter(employee__in=people, date=day)
    }
    leave = _on_leave(people, day)
    rows = []
    for person in people:
        row = marked.get(person.pk)
        rows.append(
            {
                "employee": person.pk,
                "number": person.number,
                "name": person.full_name,
                "job_title": person.job_title,
                "branch_name": person.branch.name if person.branch_id else None,
                "department_name": person.department.name if person.department_id else None,
                "on_leave": leave.get(person.pk),
                "status": row.status if row else None,
                "check_in": row.check_in.strftime("%H:%M") if row and row.check_in else None,
                "check_out": row.check_out.strftime("%H:%M") if row and row.check_out else None,
                "note": row.note if row else "",
            }
        )
    return {
        "date": day.isoformat(),
        "today": company_today(company).isoformat(),
        "weekend": day.weekday() in hr_settings(company).weekend,
        "rows": rows,
    }


@transaction.atomic
def mark(company, day: date, entries: list[dict], user, branch_ids=None) -> int:
    """Save the day's sheet. An entry with no status clears what was marked. Returns rows saved."""
    if day > company_today(company):
        raise HRError("Attendance can't be marked for a day that hasn't come yet.", "date")
    ids = [entry["employee"] for entry in entries]
    people = {person.pk: person for person in Employee.objects.filter(company=company, pk__in=ids)}
    leave = _on_leave(list(people.values()), day)
    saved = 0
    for entry in entries:
        person = people.get(entry["employee"])
        if person is None:
            raise HRError("An employee on the sheet belongs to another company.", "entries")
        if branch_ids is not None and person.branch_id not in branch_ids:
            raise HRError(f"You can't mark attendance for {person.full_name}.", "entries")
        status = entry.get("status") or ""
        if not status:
            Attendance.objects.filter(employee=person, date=day).delete()
            continue
        if status not in STATUSES:
            raise HRError(f"Unknown status {status}.", "entries")
        if not _working(person, day):
            raise HRError(f"{person.full_name} was not employed on that day.", "entries")
        if person.pk in leave:
            raise HRError(f"{person.full_name} is on approved leave that day.", "entries")
        check_in: time | None = entry.get("check_in")
        check_out: time | None = entry.get("check_out")
        if check_in and check_out and check_out < check_in:
            raise HRError(f"{person.full_name}: the time out is before the time in.", "entries")
        Attendance.objects.update_or_create(
            employee=person,
            date=day,
            defaults={
                "company": company,
                "status": status,
                "check_in": check_in,
                "check_out": check_out,
                "note": (entry.get("note") or "")[:255],
                "updated_by": user,
            },
            create_defaults={
                "company": company,
                "status": status,
                "check_in": check_in,
                "check_out": check_out,
                "note": (entry.get("note") or "")[:255],
                "created_by": user,
                "updated_by": user,
            },
        )
        saved += 1
    return saved


def leave_report(company, year: int, branch_ids=None, branch=None, department=None) -> list[dict]:
    """Per employee for the year: annual leave entitled, taken, pending and left; other leave
    taken; and the days marked absent, late or half day."""
    ensure_leave_types(company)
    people = Employee.objects.filter(company=company, is_active=True).select_related(
        "branch", "department"
    )
    if branch_ids is not None:
        people = people.filter(branch_id__in=branch_ids)
    if branch:
        people = people.filter(branch_id=branch)
    if department:
        people = people.filter(department_id=department)
    people = list(people.order_by("first_name", "last_name"))
    ids = [person.pk for person in people]

    adjust = defaultdict(lambda: ZERO)
    for row in (
        LeaveAdjustment.objects.filter(employee_id__in=ids, year=year)
        .values("employee_id")
        .annotate(days=Sum("days"))
    ):
        adjust[row["employee_id"]] = row["days"] or ZERO

    leave = defaultdict(lambda: defaultdict(lambda: ZERO))
    annual_types = set(
        LeaveType.objects.filter(company=company, uses_annual_balance=True).values_list(
            "pk", flat=True
        )
    )
    unpaid_types = set(
        LeaveType.objects.filter(company=company, is_paid=False).values_list("pk", flat=True)
    )
    for row in (
        LeaveRequest.objects.filter(
            employee_id__in=ids,
            start_date__year=year,
            status__in=(LeaveRequest.Status.APPROVED, LeaveRequest.Status.PENDING),
        )
        .values("employee_id", "leave_type_id", "status")
        .annotate(days=Sum("days"))
    ):
        bucket = leave[row["employee_id"]]
        approved = row["status"] == LeaveRequest.Status.APPROVED
        if row["leave_type_id"] in annual_types:
            bucket["annual_taken" if approved else "annual_pending"] += row["days"]
        elif approved:
            bucket["unpaid" if row["leave_type_id"] in unpaid_types else "other"] += row["days"]

    marks = {
        row["employee_id"]: row
        for row in Attendance.objects.filter(employee_id__in=ids, date__year=year)
        .values("employee_id")
        .annotate(
            absent=Count("pk", filter=Q(status=Attendance.Status.ABSENT)),
            late=Count("pk", filter=Q(status=Attendance.Status.LATE)),
            half=Count("pk", filter=Q(status=Attendance.Status.HALF_DAY)),
            marked=Count("pk"),
        )
    }

    report = []
    for person in people:
        bucket = leave[person.pk]
        entitled = person.annual_leave_days + adjust[person.pk]
        remaining = entitled - bucket["annual_taken"] - bucket["annual_pending"]
        counts = marks.get(person.pk, {})
        report.append(
            {
                "employee": person.pk,
                "number": person.number,
                "pf_number": person.pf_number,
                "name": person.full_name,
                "job_title": person.job_title,
                "department_name": person.department.name if person.department_id else None,
                "branch_name": person.branch.name if person.branch_id else None,
                "entitled": _days(entitled),
                "adjustment": _days(adjust[person.pk]),
                "annual_taken": _days(bucket["annual_taken"]),
                "annual_pending": _days(bucket["annual_pending"]),
                "remaining": _days(remaining),
                "other_leave": _days(bucket["other"]),
                "unpaid_leave": _days(bucket["unpaid"]),
                "absent": counts.get("absent", 0),
                "late": counts.get("late", 0),
                "half_day": counts.get("half", 0),
                "days_marked": counts.get("marked", 0),
            }
        )
    return report


def absences(employee: Employee, year: int) -> list[dict]:
    """The days an employee was marked absent, late or on a half day in the year."""
    rows = Attendance.objects.filter(employee=employee, date__year=year).exclude(
        status=Attendance.Status.PRESENT
    )
    return [
        {
            "date": row.date.isoformat(),
            "status": row.status,
            "check_in": row.check_in.strftime("%H:%M") if row.check_in else None,
            "note": row.note,
        }
        for row in rows.order_by("-date")
    ]
