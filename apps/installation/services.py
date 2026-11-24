"""Installation request life cycle (request -> schedule -> do -> sign) and billing."""

import base64
import binascii
import io
from datetime import date, time, timedelta
from decimal import Decimal

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from apps.core.fields import round_money
from apps.organization.services import currency_decimals, next_document_number
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.services import _settings as sales_settings
from apps.sales.services import recompute
from apps.users.models import Reminder

from .models import Technician, WorkOrder

ZERO = Decimal("0")
MAX_SIGNATURE_BYTES = 1024 * 1024


class InstallationError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def ensure_number(order: WorkOrder) -> None:
    if not order.number:
        order.number = next_document_number(
            order.company, "WO", order.scheduled_date or timezone.localdate()
        )


def is_supervisor(user, order: WorkOrder) -> bool:
    return user.is_superuser or user.has_erp_permission(
        "installation.work_order.schedule", order.company_id, order.branch_id
    )


def may_work_on(user, order: WorkOrder) -> bool:
    """A technician works only on the jobs they are assigned to; the supervisor on any of them."""
    if is_supervisor(user, order):
        return True
    return order.technicians.filter(user=user).exists()


@transaction.atomic
def schedule_work(
    order: WorkOrder,
    user,
    scheduled_date: date,
    scheduled_time: time | None,
    duration_hours: Decimal,
    technicians: list[Technician],
) -> WorkOrder:
    """The supervisor sets the day, time and technicians, and the job goes on the schedule board.
    The same call moves a job that is already scheduled."""
    order = WorkOrder.objects.select_for_update().select_related("customer").get(pk=order.pk)
    if order.status not in (WorkOrder.Status.REQUESTED, WorkOrder.Status.SCHEDULED):
        raise InstallationError("Only requested or scheduled jobs can be scheduled.")
    if duration_hours <= 0:
        raise InstallationError("Give the expected hours.", "duration_hours")
    if not technicians:
        raise InstallationError("Choose at least one technician.", "technicians")
    for tech in technicians:
        if tech.company_id != order.company_id:
            raise InstallationError("Choose technicians from the same company.", "technicians")
        if not tech.is_active:
            raise InstallationError(f"{tech.name} is inactive.", "technicians")
    before = {
        "techs": set(order.technicians.values_list("pk", flat=True)),
        "when": (order.scheduled_date, order.scheduled_time),
    }
    order.scheduled_date = scheduled_date
    order.scheduled_time = scheduled_time
    order.duration_hours = duration_hours
    order.status = WorkOrder.Status.SCHEDULED
    order.scheduled_by = user
    order.scheduled_at = timezone.now()
    order.updated_by = user
    ensure_number(order)
    order.save()
    order.technicians.set(technicians)
    _tell_technicians(order, user, before)
    return order


def _tell_technicians(order: WorkOrder, user, before: dict) -> None:
    """A note under the bell for each technician with a login: new on the job, or the job moved."""
    moved = before["when"] != (order.scheduled_date, order.scheduled_time)
    when = order.scheduled_date.strftime("%d/%m/%Y")
    if order.scheduled_time:
        when += " " + order.scheduled_time.strftime("%H:%M")
    address = order.site_address.strip()
    place = address.splitlines()[0] if address else ""
    for tech in order.technicians.select_related("user"):
        person = tech.user
        if person is None or person == user or not person.is_active:
            continue
        if tech.pk in before["techs"] and not moved:
            continue
        lead = "Installation moved" if tech.pk in before["techs"] else "Installation job"
        message = f"{lead} {order.number}: {when}, {order.customer.name}"
        Reminder.objects.create(
            sender=user, recipient=person, message=f"{message}, {place}" if place else message
        )


@transaction.atomic
def start_work(order: WorkOrder, user=None) -> WorkOrder:
    order = WorkOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != WorkOrder.Status.SCHEDULED:
        raise InstallationError("Only scheduled jobs can be started.")
    if user is not None and not may_work_on(user, order):
        raise InstallationError("Only the technicians on this job can start it.")
    order.status = WorkOrder.Status.IN_PROGRESS
    order.started_at = timezone.now()
    order.updated_by = user
    order.save()
    return order


@transaction.atomic
def complete_work(
    order: WorkOrder, user, labour_hours: Decimal, notes: str = "", signed_by: str = ""
) -> WorkOrder:
    order = WorkOrder.objects.select_for_update().select_related("company").get(pk=order.pk)
    if order.status == WorkOrder.Status.REQUESTED:
        raise InstallationError("Schedule the job before completing it.")
    if order.status not in (WorkOrder.Status.SCHEDULED, WorkOrder.Status.IN_PROGRESS):
        raise InstallationError("This job is already closed.")
    if user is not None and not may_work_on(user, order):
        raise InstallationError("Only the technicians on this job can complete it.")
    if labour_hours < 0:
        raise InstallationError("Hours can't be negative.", "labour_hours")
    technicians = list(order.technicians.all())
    if not technicians:
        raise InstallationError("Assign at least one technician before completing.", "technicians")
    decimals = currency_decimals(order.company.currency_id)
    rate = sum((tech.hourly_cost for tech in technicians), ZERO)
    order.labour_hours = labour_hours
    order.labour_cost = round_money(labour_hours * rate, decimals)
    order.status = WorkOrder.Status.COMPLETED
    order.completed_at = timezone.now()
    order.completed_by = user
    order.completion_notes = notes
    if signed_by:
        order.signed_by = signed_by
    order.updated_by = user
    order.save()
    return order


def read_signature(data_url: str) -> bytes:
    """The PNG a signature pad sends as a data URL. Refuses anything else, or an empty pad."""
    header, _, body = data_url.partition(",")
    if header.strip().lower() != "data:image/png;base64" or not body:
        raise InstallationError("Send the signature as a PNG image.", "signature")
    try:
        raw = base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError):
        raise InstallationError("The signature could not be read.", "signature") from None
    if len(raw) > MAX_SIGNATURE_BYTES:
        raise InstallationError("The signature image is too large.", "signature")
    try:
        with Image.open(io.BytesIO(raw)) as image:
            if image.format != "PNG":
                raise InstallationError("Send the signature as a PNG image.", "signature")
            image.load()
            drawn = image.convert("RGBA").getchannel("A").getbbox()
    except (UnidentifiedImageError, OSError):
        raise InstallationError("The signature could not be read.", "signature") from None
    if drawn is None:
        raise InstallationError("The customer has not signed yet.", "signature")
    return raw


@transaction.atomic
def sign_off(order: WorkOrder, user, signed_by: str, signature: str) -> WorkOrder:
    """The customer confirms on the completion form that the installation is done."""
    order = WorkOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != WorkOrder.Status.COMPLETED:
        raise InstallationError("The technician marks the job completed before the customer signs.")
    if order.signed_at is not None:
        raise InstallationError("The customer has already signed this job.")
    if not may_work_on(user, order):
        raise InstallationError("Only the technicians on this job can take the signature.")
    name = signed_by.strip()
    if not name:
        raise InstallationError("Write the name of the person signing.", "signed_by")
    raw = read_signature(signature)
    order.signature.save("signature.png", ContentFile(raw), save=False)
    order.signed_by = name[:120]
    order.signed_at = timezone.now()
    order.updated_by = user
    order.save()
    return order


@transaction.atomic
def cancel_work(order: WorkOrder, user, reason: str) -> WorkOrder:
    order = WorkOrder.objects.select_for_update().get(pk=order.pk)
    if order.status == WorkOrder.Status.COMPLETED:
        raise InstallationError("Completed jobs can't be cancelled.")
    if order.status == WorkOrder.Status.CANCELLED:
        raise InstallationError("This job is already cancelled.")
    if not reason.strip():
        raise InstallationError("Give a reason.", "reason")
    order.status = WorkOrder.Status.CANCELLED
    order.cancel_reason = reason.strip()[:255]
    order.updated_by = user
    order.save()
    return order


@transaction.atomic
def make_invoice(
    order: WorkOrder, product, quantity: Decimal, unit_price: Decimal, user=None
) -> Invoice:
    """A draft sales invoice for the installation service (credits the product's income account)."""
    order = (
        WorkOrder.objects.select_for_update()
        .select_related("company", "customer", "project")
        .get(pk=order.pk)
    )
    if order.status != WorkOrder.Status.COMPLETED:
        raise InstallationError("Complete the job before billing it.")
    if quantity <= 0 or unit_price < 0:
        raise InstallationError("Enter a quantity and a price.", "quantity")
    today = timezone.localdate()
    terms = (
        order.customer.payment_terms_days
        or sales_settings(order.company).default_payment_terms_days
    )
    invoice = Invoice.objects.create(
        company=order.company,
        date=today,
        due_date=today + timedelta(days=terms),
        branch=order.branch,
        customer=order.customer,
        salesperson=user,
        currency_id=order.company.currency_id,
        exchange_rate=Decimal(1),
        reference=order.number,
        project=order.project,
        work_order=order,
        notes=order.title,
        created_by=user,
        updated_by=user,
    )
    line = InvoiceLine(
        invoice=invoice,
        product=product,
        description=order.title,
        quantity=quantity,
        unit_price=unit_price,
        tax_rate=sales_settings(order.company).default_tax_rate,
    )
    recompute(invoice, [line])
    line.save()
    invoice.save()
    return invoice


def calendar(company, start: date, end: date, branch_ids=None) -> list[dict]:
    """Scheduled jobs per day for the schedule board (requests without a date are not on it)."""
    orders = (
        WorkOrder.objects.filter(company=company, scheduled_date__range=(start, end))
        .exclude(status__in=(WorkOrder.Status.CANCELLED, WorkOrder.Status.REQUESTED))
        .select_related("customer", "branch", "project")
        .prefetch_related("technicians")
        .order_by("scheduled_date", "scheduled_time", "id")
    )
    if branch_ids:
        orders = orders.filter(branch_id__in=branch_ids)
    days: dict[str, list] = {}
    for order in orders:
        days.setdefault(order.scheduled_date.isoformat(), []).append(
            {
                "id": order.pk,
                "number": order.number,
                "title": order.title,
                "customer_name": order.customer.name,
                "project_number": order.project.number if order.project else None,
                "branch_name": order.branch.name,
                "time": order.scheduled_time.strftime("%H:%M") if order.scheduled_time else None,
                "duration_hours": str(order.duration_hours),
                "status": order.status,
                "signed": order.signed_at is not None,
                "technicians": [tech.name for tech in order.technicians.all()],
                "technician_ids": [tech.pk for tech in order.technicians.all()],
                "site_address": order.site_address,
            }
        )
    return [{"date": day, "jobs": jobs} for day, jobs in sorted(days.items())]
