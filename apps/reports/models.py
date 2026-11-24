"""The archive of the previous system's documents.

Invoices, quotations, purchases, returns, transfers, expenses and payments made before the move
are kept here to look up. They are history only: they touch neither stock nor the accounts,
which start from the opening balances.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class LegacyDocument(models.Model):
    class Kind(models.TextChoices):
        SALE = "sale", _("Invoice")
        QUOTATION = "quotation", _("Quotation")
        PURCHASE = "purchase", _("Purchase")
        SALE_RETURN = "sale_return", _("Sale return")
        PURCHASE_RETURN = "purchase_return", _("Purchase return")
        TRANSFER = "transfer", _("Transfer")
        EXPENSE = "expense", _("Expense")
        ADJUSTMENT = "adjustment", _("Stock adjustment")
        DELIVERY = "delivery", _("Delivery note")
        PROJECT_INVOICE = "project_invoice", _("Project invoice")
        CUSTOMER_PAYMENT = "customer_payment", _("Customer transaction")
        MONEY_TRANSFER = "money_transfer", _("Money transfer")
        SALARY = "salary", _("Monthly salary")

    company = models.ForeignKey("organization.Company", on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    number = models.CharField(max_length=80, blank=True, db_index=True)
    date = models.DateField(null=True, blank=True)
    party = models.CharField(max_length=200, blank=True, help_text=_("Customer or supplier."))
    warehouse = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=60, blank=True)
    payment_status = models.CharField(max_length=60, blank=True)
    total = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    paid = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    due = models.DecimalField(max_digits=18, decimal_places=3, null=True, blank=True)
    details = models.JSONField(default=dict, help_text=_("Every other column, as it was shown."))

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [models.Index(fields=["company", "kind", "date"])]

    def __str__(self):
        return f"{self.kind} {self.number}"
