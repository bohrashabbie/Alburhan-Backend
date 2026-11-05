"""General ledger: chart of accounts, journals, periods and double-entry journal entries.

Every module (sales, purchasing, inventory, …) ends up here as posted journal entries, so these
rules are strict:
- an entry posts only if it balances, in its own currency and in the company currency;
- posted entries never change; mistakes are corrected by a reversing entry;
- nothing posts into a closed period.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.fields import MoneyField, RateField
from apps.core.models import AuditedModel, CompanyScopedModel


class AccountType(models.TextChoices):
    ASSET = "asset", _("Asset")
    LIABILITY = "liability", _("Liability")
    EQUITY = "equity", _("Equity")
    INCOME = "income", _("Income")
    EXPENSE = "expense", _("Expense")


#: Account types whose balance is normally on the debit side.
DEBIT_NORMAL = {AccountType.ASSET, AccountType.EXPENSE}


class Account(CompanyScopedModel):
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=150)
    name_ar = models.CharField(max_length=150, blank=True)
    account_type = models.CharField(max_length=10, choices=AccountType.choices)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="children"
    )
    is_group = models.BooleanField(
        default=False, help_text=_("A heading that groups accounts; entries can't post to it.")
    )
    currency = models.ForeignKey(
        "organization.Currency",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Only for accounts kept in a foreign currency, e.g. a USD bank account."),
    )
    is_reconcilable = models.BooleanField(
        default=False, help_text=_("Receivable/payable accounts tracked per customer or supplier.")
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_account_code")
        ]

    def __str__(self):
        return f"{self.code} {self.name}"

    @property
    def is_debit_normal(self) -> bool:
        return self.account_type in DEBIT_NORMAL


class Journal(CompanyScopedModel):
    class JournalType(models.TextChoices):
        GENERAL = "general", _("General")
        SALES = "sales", _("Sales")
        PURCHASE = "purchase", _("Purchases")
        CASH = "cash", _("Cash")
        BANK = "bank", _("Bank")

    code = models.CharField(
        max_length=10, help_text=_("Also the document prefix, e.g. JV → KW-JV-2026-00001.")
    )
    name = models.CharField(max_length=100)
    name_ar = models.CharField(max_length=100, blank=True)
    journal_type = models.CharField(max_length=10, choices=JournalType.choices)
    default_account = models.ForeignKey(
        Account,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        help_text=_("Cash or bank account used by this journal."),
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["company__code", "code"]
        constraints = [
            models.UniqueConstraint(fields=["company", "code"], name="uniq_journal_code")
        ]

    def __str__(self):
        return f"{self.company.code} {self.code}"


class AccountingPeriod(CompanyScopedModel):
    class Status(models.TextChoices):
        OPEN = "open", _("Open")
        CLOSED = "closed", _("Closed")

    name = models.CharField(max_length=40, help_text=_("e.g. 2026-01"))
    start_date = models.DateField()
    end_date = models.DateField()
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    closed_at = models.DateTimeField(null=True, blank=True, editable=False)
    closed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )

    class Meta:
        ordering = ["company__code", "start_date"]
        constraints = [
            models.UniqueConstraint(fields=["company", "name"], name="uniq_period_name"),
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="period_end_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.company.code} {self.name}"


class JournalEntry(CompanyScopedModel):
    class Status(models.TextChoices):
        DRAFT = "draft", _("Draft")
        POSTED = "posted", _("Posted")
        CANCELLED = "cancelled", _("Cancelled")

    journal = models.ForeignKey(Journal, on_delete=models.PROTECT, related_name="entries")
    number = models.CharField(max_length=40, blank=True, db_index=True)
    date = models.DateField(db_index=True)
    reference = models.CharField(max_length=100, blank=True)
    description = models.CharField(max_length=255, blank=True)
    currency = models.ForeignKey(
        "organization.Currency", on_delete=models.PROTECT, related_name="+"
    )
    exchange_rate = RateField(
        default=1, help_text=_("1 unit of the entry currency in company currency.")
    )
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    posted_at = models.DateTimeField(null=True, blank=True, editable=False)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        editable=False,
    )
    reversal_of = models.OneToOneField(
        "self",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_by",
        editable=False,
    )
    # Which document created this entry (e.g. "sales.invoice", 42); empty for manual entries.
    source_type = models.CharField(max_length=50, blank=True, db_index=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["-date", "-id"]
        verbose_name_plural = "journal entries"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "number"],
                condition=~models.Q(number=""),
                name="uniq_entry_number",
            ),
            models.CheckConstraint(
                condition=models.Q(exchange_rate__gt=0), name="entry_rate_positive"
            ),
        ]
        indexes = [models.Index(fields=["company", "status", "date"])]

    def __str__(self):
        return self.number or f"Draft #{self.pk}"


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="lines")
    description = models.CharField(max_length=255, blank=True)
    # In the entry currency…
    debit = MoneyField()
    credit = MoneyField()
    # …and in the company currency, fixed when the entry is posted.
    debit_base = MoneyField()
    credit_base = MoneyField()
    customer = models.ForeignKey(
        "partners.Customer", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    supplier = models.ForeignKey(
        "partners.Supplier", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    sequence = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["entry_id", "sequence", "id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(debit__gte=0) & models.Q(credit__gte=0),
                name="line_amounts_not_negative",
            ),
            models.CheckConstraint(
                condition=~(models.Q(debit__gt=0) & models.Q(credit__gt=0)),
                name="line_one_side_only",
            ),
        ]
        indexes = [models.Index(fields=["account", "entry"])]

    def __str__(self):
        return f"{self.account.code} D{self.debit} C{self.credit}"


class AccountingSettings(AuditedModel):
    """Default accounts per company, used when other modules post automatically."""

    company = models.OneToOneField(
        "organization.Company", on_delete=models.CASCADE, related_name="accounting_settings"
    )

    def _account(help_text):  # noqa: N805 - small field factory
        return models.ForeignKey(
            Account,
            on_delete=models.PROTECT,
            null=True,
            blank=True,
            related_name="+",
            help_text=help_text,
        )

    receivable = _account(_("Customer balances (accounts receivable)."))
    payable = _account(_("Supplier balances (accounts payable)."))
    customer_advances = _account(_("Down payments received from customers."))
    supplier_advances = _account(_("Advances paid to suppliers."))
    grni = _account(_("Goods received but not yet invoiced by the supplier."))
    purchase_expenses = _account(_("Freight, customs and other landed costs."))
    sales = _account(_("Sales income."))
    sales_returns = _account(_("Sales returns and allowances."))
    installation_income = _account(_("Installation service income."))
    inventory = _account(_("Stock value."))
    cost_of_sales = _account(_("Cost of goods sold."))
    inventory_adjustment = _account(_("Stock gains and losses from counts and adjustments."))
    vat_output = _account(_("VAT collected on sales."))
    vat_input = _account(_("VAT paid on purchases."))
    fx_difference = _account(_("Exchange rate gains and losses."))
    retained_earnings = _account(_("Where the year's profit is closed to."))
    del _account

    class Meta:
        verbose_name_plural = "accounting settings"

    def __str__(self):
        return f"Accounting settings · {self.company.code}"
