from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from apps.accounting.setup import set_up_company
from apps.organization.models import Company


class Command(BaseCommand):
    help = "Create the standard chart of accounts, journals, default accounts and periods."

    def add_arguments(self, parser):
        parser.add_argument("--company", required=True, help="Company code, e.g. KW, or ALL")
        parser.add_argument("--year", type=int, default=timezone.localdate().year)

    def handle(self, *args, company, year, **options):
        companies = (
            Company.objects.all()
            if company.upper() == "ALL"
            else Company.objects.filter(code=company.upper())
        )
        if not companies:
            raise CommandError(f"No company {company}.")
        for item in companies:
            self.stdout.write(f"{item.code}: {set_up_company(item, year)}")
