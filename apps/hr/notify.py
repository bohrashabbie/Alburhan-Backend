"""HR emails. Every message is logged as a Notification (sent or failed) so HR can see what went
out. Links open a page in the ERP that asks for the decision: an email scanner following the link
can't approve anything by itself."""

import logging
from html import escape

from django.conf import settings
from django.core.mail import EmailMultiAlternatives

from .models import LeaveRequest, Notification

log = logging.getLogger(__name__)


def _hr_email(company) -> str:
    from .services import hr_settings

    return hr_settings(company).hr_email


def _when(request: LeaveRequest) -> str:
    first, last = request.start_date, request.end_date
    span = f"{first:%a %d %b %Y}" if first == last else f"{first:%a %d %b} – {last:%a %d %b %Y}"
    return f"{span} ({request.days.normalize():f} working day{'s' if request.days != 1 else ''})"


def _send(request: LeaveRequest, to: str, kind: str, subject: str, lines: list[str], button=None):
    if not to:
        return
    text = "\n\n".join(lines + ([f"{button[0]}: {button[1]}"] if button else []))
    html_lines = "".join(f"<p style='margin:0 0 12px'>{escape(line)}</p>" for line in lines)
    html_button = (
        f"<p style='margin:20px 0'><a href='{escape(button[1])}' style='background:#111;color:#fff;"
        f"padding:12px 20px;border-radius:10px;text-decoration:none;font-weight:600'>{escape(button[0])}</a></p>"
        if button
        else ""
    )
    html = (
        "<div style='font-family:Inter,Segoe UI,Arial,sans-serif;font-size:15px;color:#111;max-width:560px'>"
        f"<p style='font-weight:700;font-size:18px;margin:0 0 16px'>{escape(request.company.name)}</p>"
        f"{html_lines}{html_button}"
        "<p style='color:#777;font-size:12px;margin-top:24px'>Al-Burhan ERP · automatic message</p></div>"
    )
    record = Notification.objects.create(
        company=request.company, leave_request=request, to=to, kind=kind, subject=subject[:200]
    )
    try:
        message = EmailMultiAlternatives(subject, text, settings.DEFAULT_FROM_EMAIL, [to])
        message.attach_alternative(html, "text/html")
        message.send()
        record.sent = True
        record.save(update_fields=["sent"])
    except Exception as exc:  # noqa: BLE001 - an email failure must never undo the leave request
        log.exception("HR email to %s failed", to)
        record.error = str(exc)[:255]
        record.save(update_fields=["error"])


def leave_submitted(request: LeaveRequest, token: str) -> None:
    request = LeaveRequest.objects.select_related(
        "employee", "leave_type", "approver", "company"
    ).get(pk=request.pk)
    employee = request.employee
    who = f"{employee.full_name} ({employee.email})" if employee.email else employee.full_name
    link = f"{settings.FRONTEND_URL.rstrip('/')}/leave-decision/{token}"
    details = [
        f"{request.leave_type.name}: {_when(request)}.",
        f"Reason: {request.reason}" if request.reason else "No reason given.",
    ]
    hr = _hr_email(request.company)
    approver = request.approver
    if approver and approver.email:
        _send(
            request,
            approver.email,
            "approval",
            f"Leave request {request.number} from {employee.full_name}",
            [
                f"Hello {approver.first_name},",
                f"{who} asks for leave.",
                *details,
                "Approve or reject it from the link below. طلب إجازة بانتظار موافقتك.",
            ],
            ("Approve or reject", link),
        )
        if hr:
            _send(
                request,
                hr,
                "hr_copy",
                f"[HR] Leave request {request.number} from {employee.full_name}",
                [
                    f"{who} asks for leave.",
                    *details,
                    f"Sent to {approver.full_name} ({approver.email}) for approval.",
                ],
            )
    elif hr:
        # No manager on file: HR decides.
        _send(
            request,
            hr,
            "approval",
            f"Leave request {request.number} from {employee.full_name}",
            [f"{who} asks for leave and has no manager on file, so HR decides.", *details],
            ("Approve or reject", link),
        )
    if employee.email:
        target = approver.full_name if approver else "HR"
        _send(
            request,
            employee.email,
            "received",
            f"Your leave request {request.number} was sent",
            [
                f"Hello {employee.first_name},",
                f"Your request for {_when(request)} went to {target}.",
                "You'll get an email when it is decided. سيتم إشعارك بالقرار.",
            ],
        )


def leave_decided(request: LeaveRequest) -> None:
    request = LeaveRequest.objects.select_related("employee", "leave_type", "company").get(
        pk=request.pk
    )
    employee = request.employee
    outcome = "approved" if request.status == LeaveRequest.Status.APPROVED else "rejected"
    note = [f"Note: {request.decision_note}"] if request.decision_note else []
    if employee.email:
        _send(
            request,
            employee.email,
            outcome,
            f"Your leave {request.number} was {outcome}",
            [
                f"Hello {employee.first_name},",
                f"Your {request.leave_type.name.lower()} for {_when(request)} was {outcome} by {request.decided_by_name}.",
                *note,
            ],
        )
    hr = _hr_email(request.company)
    if hr:
        paid = (
            ""
            if request.leave_type.is_paid
            else " Unpaid: it will be deducted in the month's payroll."
        )
        _send(
            request,
            hr,
            f"hr_{outcome}",
            f"[HR] Leave {request.number} {outcome}",
            [
                f"{employee.full_name}: {request.leave_type.name.lower()} {_when(request)} was {outcome} "
                f"by {request.decided_by_name} ({request.decided_via}).{paid}",
                *note,
            ],
        )


def leave_cancelled(request: LeaveRequest, was_approved: bool) -> None:
    request = LeaveRequest.objects.select_related("employee", "approver", "company").get(
        pk=request.pk
    )
    targets = {request.approver.email if request.approver else "", _hr_email(request.company)}
    for to in filter(None, targets):
        _send(
            request,
            to,
            "cancelled",
            f"Leave {request.number} cancelled",
            [
                f"{request.employee.full_name} cancelled their {'approved' if was_approved else 'pending'} leave for {_when(request)}."
            ],
        )
