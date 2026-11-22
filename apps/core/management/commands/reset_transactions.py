"""Clear every transaction and keep the master data, so a demo or a training run can start over.

Removed: quotations, invoices, deliveries, receipts, credit notes, POS shifts and sales, purchase
orders, goods receipts, supplier bills and payments, stock moves and balances, reservations,
adjustments, transfers, counts, journal entries, projects, work orders, leave requests, HR
notifications and payroll runs. Document numbering that restarts each year goes back to 00001.

Kept: companies, branches, warehouses, users, roles, products, prices, customers, suppliers, the
chart of accounts, journals, periods, settings, registers, technicians, employees and salaries.

    manage.py reset_transactions --dry-run   # show what would go, change nothing
    manage.py reset_transactions --yes       # do it
"""

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import IntegrityError, models, transaction
from django.db.models.deletion import ProtectedError, RestrictedError

# Every model listed here is emptied. Order does not matter: optional links between them are
# cleared first, then they are deleted in passes until nothing is left.
TRANSACTION_MODELS = [
    "sales.ReceiptAllocation",
    "sales.Receipt",
    "sales.CreditNoteLine",
    "sales.CreditNote",
    "sales.DeliveryLine",
    "sales.DeliveryNote",
    "sales.InvoiceLine",
    "sales.Invoice",
    "sales.QuotationLine",
    "sales.Quotation",
    "pos.SalePayment",
    "pos.Sale",
    "pos.Shift",
    "purchasing.PaymentAllocation",
    "purchasing.SupplierPayment",
    "purchasing.SupplierInvoiceLine",
    "purchasing.SupplierInvoice",
    "purchasing.GoodsReceiptLine",
    "purchasing.GoodsReceipt",
    "purchasing.PurchaseOrderLine",
    "purchasing.PurchaseOrder",
    "projects.MaterialIssueLine",
    "projects.MaterialIssue",
    "projects.BOQLine",
    "projects.Project",
    "installation.WorkOrder",
    "inventory.Reservation",
    "inventory.StockAdjustmentLine",
    "inventory.StockAdjustment",
    "inventory.StockTransferLine",
    "inventory.StockTransfer",
    "inventory.StockCountLine",
    "inventory.StockCount",
    "inventory.StockMove",
    "inventory.StockBalance",
    "inventory.ProductCost",
    "hr.Payslip",
    "hr.PayrollRun",
    "hr.Notification",
    "hr.LeaveRequest",
    "accounting.JournalLine",
    "accounting.JournalEntry",
]


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Delete all transactions (sales, purchasing, stock, accounting, projects, HR requests) and keep master data."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Really delete.")
        parser.add_argument(
            "--dry-run", action="store_true", help="Show what would be deleted, then roll back."
        )

    def handle(self, *args, **options):
        if not options["yes"] and not options["dry_run"]:
            raise CommandError(
                "This deletes every transaction. Run with --dry-run first, then --yes."
            )

        from auditlog.context import disable_auditlog

        targets = [apps.get_model(label) for label in TRANSACTION_MODELS]
        target_set = set(targets)
        deleted: dict[str, int] = {}
        try:
            with disable_auditlog(), transaction.atomic():
                self._unlink(targets, target_set)
                self._delete(targets, deleted)
                reset = self._reset_numbering()
                extra = sorted(
                    label
                    for label in set(deleted) - set(TRANSACTION_MODELS)
                    if not apps.get_model(label)._meta.auto_created  # many-to-many link tables
                )
                if extra:
                    raise CommandError(f"Deleting would also remove {extra}; nothing was changed.")
                if options["dry_run"]:
                    raise _DryRun
        except _DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing was changed."))

        for label in TRANSACTION_MODELS:
            if deleted.get(label):
                self.stdout.write(f"  {label:<34} {deleted[label]:>6}")
        self.stdout.write(f"  numbering restarted for {reset} document types")
        if not options["dry_run"]:
            self.stdout.write(self.style.SUCCESS("Transactions cleared; master data kept."))

    @staticmethod
    def _unlink(targets, target_set):
        """Clear optional links between transactions (self-references, reversals, back-links)."""
        for model in targets:
            fields = [
                f.name
                for f in model._meta.get_fields()
                if isinstance(f, models.ForeignKey | models.OneToOneField)
                and f.null
                and f.related_model in target_set
            ]
            if fields:
                model.objects.update(**{name: None for name in fields})

    @staticmethod
    def _delete(targets, deleted):
        remaining = list(targets)
        while remaining:
            progress = False
            for model in list(remaining):
                if not model.objects.exists():
                    remaining.remove(model)
                    progress = True
                    continue
                try:
                    with transaction.atomic():
                        _total, per_model = model.objects.all().delete()
                except (ProtectedError, RestrictedError, IntegrityError):
                    continue
                for label, count in per_model.items():
                    deleted[label] = deleted.get(label, 0) + count
                remaining.remove(model)
                progress = True
            if not progress:
                names = ", ".join(m._meta.label for m in remaining)
                raise CommandError(f"Could not clear {names}: something else still points to them.")

    @staticmethod
    def _reset_numbering():
        sequence = apps.get_model("organization", "DocumentSequence")
        # Yearly sequences are the documents; customer, supplier and employee codes keep counting.
        return sequence.objects.filter(reset_yearly=True).update(next_number=1, current_year=None)
