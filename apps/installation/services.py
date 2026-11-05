"""Work order life cycle and billing."""

from datetime import date, timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from apps.core.fields import round_money
from apps.organization.services import currency_decimals, next_document_number
from apps.sales.models import Invoice, InvoiceLine
from apps.sales.services import _settings as sales_settings
from apps.sales.services import recompute

from .models import WorkOrder

ZERO = Decimal("0")


class InstallationError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def ensure_number(order: WorkOrder) -> None:
    if not order.number:
        order.number = next_document_number(order.company, "WO", order.scheduled_date)


@transaction.atomic
def start_work(order: WorkOrder, user=None) -> WorkOrder:
    order = WorkOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != WorkOrder.Status.SCHEDULED:
        raise InstallationError("Only scheduled jobs can be started.")
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
    if order.status not in (WorkOrder.Status.SCHEDULED, WorkOrder.Status.IN_PROGRESS):
        raise InstallationError("This job is already closed.")
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
    order.completion_notes = notes
    order.signed_by = signed_by
    order.updated_by = user
    order.save()
    return order


@transaction.atomic
def cancel_work(order: WorkOrder, user, reason: str) -> WorkOrder:
    order = WorkOrder.objects.select_for_update().get(pk=order.pk)
    if order.status == WorkOrder.Status.COMPLETED:
        raise InstallationError("Completed jobs can't be cancelled.")
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
    """Jobs per day for the schedule board."""
    orders = (
        WorkOrder.objects.filter(company=company, scheduled_date__range=(start, end))
        .exclude(status=WorkOrder.Status.CANCELLED)
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
                "technicians": [tech.name for tech in order.technicians.all()],
                "site_address": order.site_address,
            }
        )
    return [{"date": day, "jobs": jobs} for day, jobs in sorted(days.items())]
