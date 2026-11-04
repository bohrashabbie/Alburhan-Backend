from django.contrib import admin

from .models import (
    Account,
    AccountingPeriod,
    AccountingSettings,
    Journal,
    JournalEntry,
    JournalLine,
)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "company", "account_type", "is_group", "is_active"]
    list_filter = ["company", "account_type", "is_group"]
    search_fields = ["code", "name"]


@admin.register(Journal)
class JournalAdmin(admin.ModelAdmin):
    list_display = ["code", "name", "company", "journal_type", "is_active"]


@admin.register(AccountingPeriod)
class PeriodAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "start_date", "end_date", "status"]
    list_filter = ["company", "status"]


class LineInline(admin.TabularInline):
    model = JournalLine
    extra = 0
    readonly_fields = ["debit_base", "credit_base"]


@admin.register(JournalEntry)
class EntryAdmin(admin.ModelAdmin):
    list_display = ["number", "date", "company", "journal", "status", "description"]
    list_filter = ["company", "journal", "status"]
    search_fields = ["number", "reference", "description"]
    inlines = [LineInline]

    def has_change_permission(self, request, obj=None):
        # Posted entries are corrected by reversal, never edited.
        return obj is None or obj.status == JournalEntry.Status.DRAFT


admin.site.register(AccountingSettings)
