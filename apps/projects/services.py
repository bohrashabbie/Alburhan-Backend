"""Project rules: numbering, reservations, material issues, quotations and progress invoices."""

from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from apps.accounting.models import Account, AccountingSettings
from apps.accounting.services import LineSpec, PostingError, create_and_post
from apps.catalog.models import Product
from apps.core.fields import round_money
from apps.inventory.models import Reservation, StockMove, StockStatus
from apps.inventory.services import StockError, move, reserve
from apps.organization.services import currency_decimals, next_document_number
from apps.sales.models import Invoice, InvoiceLine, Quotation, QuotationLine, ReceiptAllocation
from apps.sales.services import _settings as sales_settings
from apps.sales.services import recompute

from .models import BOQLine, MaterialIssue, Project

ZERO = Decimal("0")


class ProjectError(Exception):
    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.message = message
        self.field = field


def _materials_account(company) -> Account:
    accounts = (
        AccountingSettings.objects.filter(company=company).select_related("inventory").first()
    )
    if accounts is None or accounts.inventory is None:
        raise ProjectError("Set up accounting for this company first.")
    account = Account.objects.filter(company=company, code="5300", is_group=False).first()
    if account is None:
        raise ProjectError("Account 5300 'Installation materials and subcontractors' is missing.")
    return account


def ensure_number(project: Project) -> None:
    if not project.number:
        project.number = next_document_number(project.company, "PRJ", timezone.localdate())


@transaction.atomic
def set_status(project: Project, status: str, user=None) -> Project:
    project = Project.objects.select_for_update().get(pk=project.pk)
    allowed = {
        Project.Status.LEAD: {Project.Status.QUOTED, Project.Status.WON, Project.Status.LOST},
        Project.Status.QUOTED: {Project.Status.WON, Project.Status.LOST, Project.Status.LEAD},
        Project.Status.WON: {Project.Status.IN_PROGRESS, Project.Status.LOST},
        Project.Status.IN_PROGRESS: {Project.Status.COMPLETED},
        Project.Status.COMPLETED: {Project.Status.CLOSED, Project.Status.IN_PROGRESS},
        Project.Status.CLOSED: set(),
        Project.Status.LOST: {Project.Status.LEAD},
    }
    if status not in allowed[project.status]:
        raise ProjectError(
            f"A {project.get_status_display().lower()} project can't move to {status}.", "status"
        )
    if status == Project.Status.IN_PROGRESS and project.start_date is None:
        project.start_date = timezone.localdate()
    if status == Project.Status.COMPLETED and project.end_date is None:
        project.end_date = timezone.localdate()
    if status == Project.Status.CLOSED:
        Reservation.objects.filter(
            source_type=project._meta.label_lower,
            source_id=project.pk,
            status=Reservation.Status.ACTIVE,
        ).update(status=Reservation.Status.RELEASED)
    project.status = status
    project.updated_by = user
    project.save()
    return project


# --- Stock --------------------------------------------------------------------------------------


@transaction.atomic
def reserve_boq(project: Project, warehouse, user=None) -> list[Reservation]:
    """Hold free stock for every stock item on the BOQ that isn't already reserved or issued."""
    if warehouse.company_id != project.company_id:
        raise ProjectError("Choose a warehouse of this company.", "warehouse")
    held = {
        row["product_id"]: row["total"]
        for row in Reservation.objects.filter(
            source_type=project._meta.label_lower,
            source_id=project.pk,
            status=Reservation.Status.ACTIVE,
        )
        .values("product_id")
        .annotate(total=Sum("quantity"))
    }
    made = []
    for line in project.boq.select_related("product"):
        if line.product.product_type != Product.ProductType.STOCK:
            continue
        needed = line.quantity - line.issued_quantity - held.get(line.product_id, ZERO)
        if needed <= 0:
            continue
        try:
            made.append(
                reserve(
                    project.company,
                    warehouse,
                    line.product,
                    needed,
                    reference=project.number,
                    source=project,
                    user=user,
                )
            )
        except StockError as exc:
            raise ProjectError(f"{line.product.sku}: {exc.message}", "lines") from exc
    return made


def _consume_reservations(project: Project, warehouse, product, quantity: Decimal) -> None:
    """An issue fulfils the project's reservations for that product first."""
    remaining = quantity
    for reservation in (
        Reservation.objects.select_for_update()
        .filter(
            source_type=project._meta.label_lower,
            source_id=project.pk,
            warehouse=warehouse,
            product=product,
            status=Reservation.Status.ACTIVE,
        )
        .order_by("created_at")
    ):
        if remaining <= 0:
            break
        if reservation.quantity <= remaining:
            remaining -= reservation.quantity
            reservation.status = Reservation.Status.FULFILLED
            reservation.save(update_fields=["status"])
        else:
            reservation.quantity -= remaining
            reservation.save(update_fields=["quantity"])
            remaining = ZERO


@transaction.atomic
def post_material_issue(issue: MaterialIssue, user=None) -> MaterialIssue:
    issue = (
        MaterialIssue.objects.select_for_update(of=("self",))
        .select_related("company", "project", "warehouse")
        .get(pk=issue.pk)
    )
    if issue.status != MaterialIssue.Status.DRAFT:
        raise ProjectError("This issue has already been posted.")
    project = Project.objects.select_for_update().get(pk=issue.project_id)
    if project.status in (Project.Status.CLOSED, Project.Status.LOST):
        raise ProjectError("This project is closed.", "project")
    lines = list(issue.lines.select_related("product", "product__uom", "boq_line"))
    if not lines:
        raise ProjectError("Add at least one line.", "lines")
    accounts = (
        AccountingSettings.objects.filter(company=issue.company).select_related("inventory").first()
    )
    materials = _materials_account(issue.company)
    issue.number = next_document_number(issue.company, "MI", issue.date)
    total = ZERO
    for line in lines:
        if line.quantity <= 0:
            raise ProjectError(f"{line.product.sku}: quantity must be positive.", "lines")
        if line.product.product_type != Product.ProductType.STOCK:
            raise ProjectError(f"{line.product.sku} is not a stock item.", "lines")
        # Reserved stock for this project is ours to take; other reservations are not.
        own_hold = Reservation.objects.filter(
            source_type=project._meta.label_lower,
            source_id=project.pk,
            warehouse=issue.warehouse,
            product=line.product,
            status=Reservation.Status.ACTIVE,
        ).aggregate(total=Sum("quantity", default=ZERO))["total"]
        from apps.inventory.services import available_quantity, reserved_quantity

        free = (
            available_quantity(issue.warehouse, line.product)
            - reserved_quantity(issue.warehouse, line.product)
            + own_hold
        )
        if line.quantity > free:
            raise ProjectError(
                f"Only {max(free, ZERO).normalize():f} {line.product.sku} free in {issue.warehouse.name}.",
                "lines",
            )
        _consume_reservations(project, issue.warehouse, line.product, line.quantity)
        try:
            stock_move = move(
                issue.company,
                issue.warehouse,
                line.product,
                StockStatus.AVAILABLE,
                -line.quantity,
                on_date=issue.date,
                move_type=StockMove.MoveType.ISSUE,
                affects_cost=True,
                reference=issue.number,
                source=issue,
                user=user,
            )
        except StockError as exc:
            raise ProjectError(exc.message, exc.field) from exc
        line.unit_cost = stock_move.unit_cost
        line.value = -stock_move.value
        line.save(update_fields=["unit_cost", "value"])
        total += line.value
        if line.boq_line_id:
            BOQLine.objects.filter(pk=line.boq_line_id).update(
                issued_quantity=F("issued_quantity") + line.quantity
            )
    issue.cost_total = total
    if total > 0:
        try:
            issue.journal_entry = create_and_post(
                issue.company,
                "JV",
                issue.date,
                [
                    LineSpec(
                        materials,
                        debit=total,
                        branch=project.branch,
                        customer=project.customer,
                        description=f"{issue.number} · {project.number}",
                    ),
                    LineSpec(accounts.inventory, credit=total, description=issue.number),
                ],
                description=f"Materials to site {issue.number} · {project.name}",
                reference=issue.number,
                source_type=issue._meta.label_lower,
                source_id=issue.pk,
                user=user,
            )
        except PostingError as exc:
            raise ProjectError(exc.message, "date" if exc.field == "date" else None) from exc
    issue.status = MaterialIssue.Status.POSTED
    issue.posted_at = timezone.now()
    issue.updated_by = user
    issue.save()
    if project.status == Project.Status.WON:
        project.status = Project.Status.IN_PROGRESS
        project.start_date = project.start_date or issue.date
        project.save(update_fields=["status", "start_date"])
    return issue


# --- Quotations and progress invoices --------------------------------------------------------------


def _boq_lines_payload(project: Project, items: list[dict]) -> list[tuple[BOQLine, Decimal]]:
    by_id = {line.pk: line for line in project.boq.select_related("product")}
    chosen = []
    for item in items:
        line = by_id.get(item["boq_line"])
        if line is None:
            raise ProjectError("A BOQ line belongs to another project.", "lines")
        quantity = Decimal(item.get("quantity") or line.quantity)
        if quantity <= 0:
            raise ProjectError(f"{line.product.sku}: quantity must be positive.", "lines")
        chosen.append((line, quantity))
    if not chosen:
        raise ProjectError("Choose at least one BOQ line.", "lines")
    return chosen


@transaction.atomic
def make_quotation(project: Project, user=None) -> Quotation:
    """A draft sales quotation with every BOQ line at its BOQ price."""
    project = (
        Project.objects.select_for_update().select_related("company", "customer").get(pk=project.pk)
    )
    lines = list(project.boq.select_related("product"))
    if not lines:
        raise ProjectError("Add BOQ lines first.", "lines")
    ensure_number(project)
    tax = sales_settings(project.company).default_tax_rate
    quotation = Quotation.objects.create(
        company=project.company,
        date=timezone.localdate(),
        branch=project.branch,
        customer=project.customer,
        salesperson=project.manager,
        currency_id=project.company.currency_id,
        exchange_rate=Decimal(1),
        reference=project.number,
        project=project,
        notes=f"Project {project.name}",
        created_by=user,
        updated_by=user,
    )
    objects = [
        QuotationLine(
            quotation=quotation,
            product=line.product,
            description=line.description,
            quantity=line.quantity,
            unit_price=line.unit_price,
            tax_rate=tax,
            sequence=index,
            boq_line=line,
        )
        for index, line in enumerate(lines)
    ]
    recompute(quotation, objects)
    QuotationLine.objects.bulk_create(objects)
    quotation.save()
    if project.status == Project.Status.LEAD:
        project.status = Project.Status.QUOTED
    project.updated_by = user
    project.save()
    return quotation


@transaction.atomic
def make_invoice(project: Project, items: list[dict], user=None) -> Invoice:
    """A draft progress invoice for the chosen BOQ lines and quantities."""
    project = (
        Project.objects.select_for_update().select_related("company", "customer").get(pk=project.pk)
    )
    if project.status in (
        Project.Status.LEAD,
        Project.Status.QUOTED,
        Project.Status.LOST,
        Project.Status.CLOSED,
    ):
        raise ProjectError("Only won or running projects can be invoiced.", "status")
    chosen = _boq_lines_payload(project, items)
    ensure_number(project)
    today = timezone.localdate()
    terms = (
        project.customer.payment_terms_days
        or sales_settings(project.company).default_payment_terms_days
    )
    tax = sales_settings(project.company).default_tax_rate
    invoice = Invoice.objects.create(
        company=project.company,
        date=today,
        due_date=today + timedelta(days=terms),
        branch=project.branch,
        customer=project.customer,
        salesperson=project.manager,
        currency_id=project.company.currency_id,
        exchange_rate=Decimal(1),
        reference=project.number,
        project=project,
        notes=f"Project {project.name}",
        created_by=user,
        updated_by=user,
    )
    objects = [
        InvoiceLine(
            invoice=invoice,
            product=line.product,
            description=line.description,
            quantity=quantity,
            unit_price=line.unit_price,
            tax_rate=tax,
            sequence=index,
            boq_line=line,
        )
        for index, (line, quantity) in enumerate(chosen)
    ]
    recompute(invoice, objects)
    InvoiceLine.objects.bulk_create(objects)
    invoice.save()
    project.updated_by = user
    project.save()
    return invoice


# --- Summary --------------------------------------------------------------------------------------


def project_summary(project: Project) -> dict:
    """Money in and out so far, in the company currency."""
    decimals = currency_decimals(project.company.currency_id)
    money = lambda value: str(round_money(value or ZERO, decimals))  # noqa: E731
    open_statuses = (Invoice.Status.POSTED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.PAID)
    invoices = Invoice.objects.filter(project=project, status__in=open_statuses)
    invoiced = invoices.aggregate(n=Sum("subtotal", default=ZERO))["n"]
    received = ReceiptAllocation.objects.filter(
        invoice__in=invoices, receipt__status="posted"
    ).aggregate(n=Sum("amount", default=ZERO))["n"]
    credited = invoices.aggregate(n=Sum("amount_credited", default=ZERO))["n"]
    materials = MaterialIssue.objects.filter(
        project=project, status=MaterialIssue.Status.POSTED
    ).aggregate(n=Sum("cost_total", default=ZERO))["n"]
    labour = ZERO
    try:
        from apps.installation.models import WorkOrder

        labour = WorkOrder.objects.filter(
            project=project, status=WorkOrder.Status.COMPLETED
        ).aggregate(n=Sum("labour_cost", default=ZERO))["n"]
    except Exception:  # pragma: no cover - installation app always present
        pass
    boq_value = sum((line.quantity * line.unit_price for line in project.boq.all()), ZERO)
    boq_cost = sum((line.quantity * line.unit_cost for line in project.boq.all()), ZERO)
    reserved = Reservation.objects.filter(
        source_type=project._meta.label_lower,
        source_id=project.pk,
        status=Reservation.Status.ACTIVE,
    ).aggregate(n=Sum("quantity", default=ZERO))["n"]
    income = invoiced - credited
    cost = materials + labour
    return {
        "boq_value": money(boq_value),
        "boq_cost": money(boq_cost),
        "invoiced": money(income),
        "received": money(received),
        "outstanding": money(income - received),
        "materials_cost": money(materials),
        "labour_cost": money(labour),
        "margin": money(income - cost),
        "margin_percent": str(round((income - cost) / income * 100, 1)) if income else None,
        "reserved_units": str(reserved),
        "invoice_count": invoices.count(),
    }


def boq_progress(project: Project) -> list[dict]:
    """BOQ lines with quantities issued and invoiced (from posted invoices)."""
    invoiced = {
        row["boq_line_id"]: row["total"]
        for row in InvoiceLine.objects.filter(
            boq_line__project=project,
            invoice__status__in=(
                Invoice.Status.POSTED,
                Invoice.Status.PARTIALLY_PAID,
                Invoice.Status.PAID,
            ),
        )
        .values("boq_line_id")
        .annotate(total=Sum("quantity"))
    }
    rows = []
    for line in project.boq.select_related("product", "product__uom"):
        rows.append(
            {
                "id": line.pk,
                "product": line.product_id,
                "sku": line.product.sku,
                "product_name": line.product.name,
                "uom": line.product.uom.code,
                "product_type": line.product.product_type,
                "description": line.description,
                "quantity": str(line.quantity),
                "unit_price": str(line.unit_price),
                "unit_cost": str(line.unit_cost),
                "amount": str(line.quantity * line.unit_price),
                "issued_quantity": str(line.issued_quantity),
                "invoiced_quantity": str(
                    Decimal(invoiced.get(line.pk, ZERO)).quantize(Decimal("0.0001"))
                ),
            }
        )
    return rows
