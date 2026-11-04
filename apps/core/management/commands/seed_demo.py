"""Development-only demo structure. Names are placeholders until the real legal names, showroom
names and addresses are confirmed by Al-Burhan. Safe to run repeatedly."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.organization.models import Branch, Company, Warehouse

COMPANIES = [
    # code, name, country, currency, timezone
    ("KW", "Al-Burhan Regional Kuwait", "KW", "KWD", "Asia/Kuwait"),
    ("AE", "Al-Burhan Regional UAE", "AE", "AED", "Asia/Dubai"),
    ("CN", "MS Lighting China", "CN", "CNY", "Asia/Shanghai"),
]

BRANCHES = {
    "KW": [
        ("HQ", "Hawally Head Office", Branch.BranchType.HEAD_OFFICE),
        ("SR1", "Showroom 1", Branch.BranchType.SHOWROOM),
        ("SR2", "Showroom 2", Branch.BranchType.SHOWROOM),
        ("SR3", "Showroom 3", Branch.BranchType.SHOWROOM),
        ("SR4", "Showroom 4", Branch.BranchType.SHOWROOM),
        ("PRJ", "Projects Department", Branch.BranchType.PROJECTS),
    ],
    "AE": [("DXB", "Dubai Office", Branch.BranchType.OFFICE)],
    "CN": [("JM", "Jiangmen Factory", Branch.BranchType.FACTORY)],
}

WAREHOUSES = {
    "KW": [
        ("MAIN", "Main Warehouse", Warehouse.WarehouseType.MAIN, "HQ"),
        ("SR1", "Showroom 1 Stock", Warehouse.WarehouseType.SHOWROOM, "SR1"),
        ("SR2", "Showroom 2 Stock", Warehouse.WarehouseType.SHOWROOM, "SR2"),
        ("SR3", "Showroom 3 Stock", Warehouse.WarehouseType.SHOWROOM, "SR3"),
        ("SR4", "Showroom 4 Stock", Warehouse.WarehouseType.SHOWROOM, "SR4"),
        ("TRANSIT", "Goods in Transit", Warehouse.WarehouseType.TRANSIT, None),
    ],
    "AE": [
        ("MAIN", "Dubai Warehouse", Warehouse.WarehouseType.MAIN, "DXB"),
        ("TRANSIT", "Goods in Transit", Warehouse.WarehouseType.TRANSIT, None),
    ],
    "CN": [("FG", "Factory Finished Goods", Warehouse.WarehouseType.MAIN, "JM")],
}


class Command(BaseCommand):
    help = "Create demo companies, branches and warehouses for local development."

    @transaction.atomic
    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("seed_demo is for development only (DEBUG must be on).")

        for code, name, country, currency, tz in COMPANIES:
            company, _ = Company.objects.update_or_create(
                code=code,
                defaults={
                    "name": name,
                    "country_id": country,
                    "currency_id": currency,
                    "timezone": tz,
                },
            )
            branches = {}
            for b_code, b_name, b_type in BRANCHES[code]:
                branches[b_code], _ = Branch.objects.update_or_create(
                    company=company, code=b_code, defaults={"name": b_name, "branch_type": b_type}
                )
            for w_code, w_name, w_type, branch_code in WAREHOUSES[code]:
                Warehouse.objects.update_or_create(
                    company=company,
                    code=w_code,
                    defaults={
                        "name": w_name,
                        "warehouse_type": w_type,
                        "branch": branches.get(branch_code),
                    },
                )
        self.stdout.write(self.style.SUCCESS("Demo organisation created."))
