"""Announcements from management: to everyone in the company, or to the staff of chosen stores.

Who is "in a store": an employee record in that store, or a role given for that store. Everyone
with access to the company receives a company-wide announcement. Each person sees it under the
bell until they tick it off (or until its last day)."""

import logging
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Q
from django.utils import timezone

from apps.access.models import UserRole

from .models import Announcement, AnnouncementRead, Employee, Notification

log = logging.getLogger(__name__)


def user_branches(user) -> set[int]:
    branches = set(
        UserRole.objects.filter(user=user, branch__isnull=False).values_list("branch_id", flat=True)
    )
    branches |= set(
        Employee.objects.filter(user=user, branch__isnull=False).values_list("branch_id", flat=True)
    )
    return branches


def inbox(user, include_read: bool = False):
    """Announcements addressed to the user that are still showing."""
    today = timezone.localdate()
    # Staff with a login but no role still get their company's announcements through their
    # employee record.
    companies = set(user.allowed_company_ids()) | set(
        Employee.objects.filter(user=user, is_active=True).values_list("company_id", flat=True)
    )
    found = (
        Announcement.objects.filter(company_id__in=companies)
        .filter(Q(show_until__isnull=True) | Q(show_until__gte=today))
        .filter(Q(branches__isnull=True) | Q(branches__in=user_branches(user)))
        .select_related("created_by")
        .prefetch_related("branches")
        .distinct()
    )
    if not include_read:
        found = found.exclude(reads__user=user)
    return found.order_by("-created_at")


def mark_read(announcement: Announcement, user) -> None:
    AnnouncementRead.objects.get_or_create(announcement=announcement, user=user)


def recipients(announcement: Announcement) -> list[Employee]:
    """Active employees the announcement is for (used for the email copy and the count)."""
    people = Employee.objects.filter(company=announcement.company, is_active=True)
    branches = list(announcement.branches.values_list("pk", flat=True))
    if branches:
        people = people.filter(branch_id__in=branches)
    return list(people)


def email_out(announcement: Announcement) -> int:
    """Send the email copy to every recipient with an address; logged like the leave emails."""
    sender = announcement.created_by
    by = (sender.get_full_name() or sender.username) if sender else ""
    lines = [line for line in announcement.body.splitlines()]
    text = "\n".join(lines + ([f"\n— {by}"] if by else []))
    body_html = "".join(
        f"<p style='margin:0 0 10px'>{escape(line) or '&nbsp;'}</p>" for line in lines
    )
    html = (
        "<div style='font-family:Inter,Segoe UI,Arial,sans-serif;font-size:15px;color:#111;max-width:560px'>"
        f"<p style='font-weight:700;font-size:18px;margin:0 0 4px'>{escape(announcement.company.name)}</p>"
        f"<p style='font-weight:600;font-size:16px;margin:0 0 16px'>{escape(announcement.title)}</p>"
        f"{body_html}"
        + (f"<p style='color:#555;margin-top:16px'>— {escape(by)}</p>" if by else "")
        + "<p style='color:#777;font-size:12px;margin-top:24px'>Al-Burhan ERP · announcement</p></div>"
    )
    sent = 0
    seen: set[str] = set()
    for person in recipients(announcement):
        address = (person.email or "").strip().lower()
        if not address or address in seen:
            continue
        seen.add(address)
        record = Notification.objects.create(
            company=announcement.company,
            to=address,
            kind="announcement",
            subject=announcement.title[:200],
        )
        try:
            message = EmailMultiAlternatives(
                announcement.title, text, settings.DEFAULT_FROM_EMAIL, [address]
            )
            message.attach_alternative(html, "text/html")
            message.send()
            record.sent = True
            record.save(update_fields=["sent"])
            sent += 1
        except Exception as exc:  # noqa: BLE001 - one bad address must not stop the rest
            log.exception("Announcement email to %s failed", address)
            record.error = str(exc)[:255]
            record.save(update_fields=["error"])
    return sent
