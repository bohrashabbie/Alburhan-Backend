"""Bring the previous system's records into one company: warehouses, brands, categories,
products with prices, customer groups, customers, suppliers, and stock as opening balances.

It reads the JSON files saved from the previous system's own lists (warehouses.json,
brands.json, categories.json, units.json, groups.json, products.json, customers.json,
suppliers.json, stock.json). Safe to run again: what is already there is left alone.

    manage.py import_legacy <folder> --company KW --dry-run
    manage.py import_legacy <folder> --company KW
    manage.py import_legacy <folder> --company KW --stock      # opening stock, once
"""

import html
import json
import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

MARK = "Imported from the previous system"
UNITS = {"piece": "PCS", "single": "PCS", "meter": "M", "roll": "ROLL", "packet": "BOX"}


def text(value) -> str:
    """Plain text from a table cell that may hold HTML."""
    value = re.sub(r"<br\s*/?>", "\n", str(value or ""), flags=re.I)
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def number(value) -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip() or "0")
    except InvalidOperation:
        return Decimal("0")


class _DryRun(Exception):
    pass


class Command(BaseCommand):
    help = "Import the previous system's master data (and, with --stock, opening stock)."

    def add_arguments(self, parser):
        parser.add_argument("folder")
        parser.add_argument("--company", default="KW")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument(
            "--stock", action="store_true", help="Post opening stock per warehouse."
        )
        parser.add_argument("--date", default=None, help="Opening stock date (YYYY-MM-DD).")
        parser.add_argument(
            "--documents", action="store_true", help="Load past documents into the archive."
        )

    def load(self, name):
        path = self.folder / f"{name}.json"
        if not path.exists():
            raise CommandError(f"{path} is missing.")
        return json.loads(path.read_text(encoding="utf-8"))

    def handle(self, *args, **options):
        from auditlog.context import disable_auditlog

        from apps.organization.models import Company
        from apps.users.models import User

        self.folder = Path(options["folder"])
        self.company = Company.objects.get(code=options["company"])
        self.user = User.objects.filter(is_superuser=True).order_by("id").first()
        self.audit = {"created_by": self.user, "updated_by": self.user}
        self.report = {}
        try:
            with disable_auditlog(), transaction.atomic():
                warehouses = self.warehouses()
                if options["documents"]:
                    self.documents()
                elif options["stock"]:
                    self.stock(warehouses, options["date"])
                else:
                    self.catalog()
                    self.partners()
                    self.extras()
                if options["dry_run"]:
                    raise _DryRun
        except _DryRun:
            self.stdout.write(self.style.WARNING("Dry run: nothing was saved."))
        for key, value in self.report.items():
            self.stdout.write(f"  {key:<34} {value}")

    # --- Warehouses ---------------------------------------------------------------------------

    def warehouses(self) -> dict:
        """Old warehouse name → Warehouse. Showrooms and the office also get a branch."""
        from apps.organization.models import Branch, Warehouse

        names = {text(row[2]): row for row in self.load("warehouses")}
        stock_file = self.folder / "stock.json"
        if stock_file.exists():
            for entry in json.loads(stock_file.read_text(encoding="utf-8")).values():
                for name in (entry.get("product_warehouse") or [[]])[0]:
                    names.setdefault(name, None)
        result, made = {}, 0
        for name, row in names.items():
            found = Warehouse.objects.filter(company=self.company, name__iexact=name).first()
            if found is None:
                is_store = "store" in name.lower()
                code = (slugify(name).upper().replace("-", "")[:16] or f"W{len(result)}")[:20]
                while Warehouse.objects.filter(company=self.company, code=code).exists():
                    code = f"{code[:17]}{len(result)}"
                branch = None
                if not is_store:
                    branch, _ = Branch.objects.get_or_create(
                        company=self.company,
                        code=code,
                        defaults={
                            "name": name,
                            "branch_type": "head_office"
                            if "office" in name.lower()
                            else "showroom",
                            "address": text(row[5]) if row else "",
                            "phone": text(row[3])[:32] if row else "",
                            **self.audit,
                        },
                    )
                found = Warehouse.objects.create(
                    company=self.company,
                    code=code,
                    name=name,
                    warehouse_type="main" if is_store else "showroom",
                    address=text(row[5]) if row else "",
                    branch=branch,
                    **self.audit,
                )
                made += 1
            result[name] = found
        self.report["warehouses added"] = made
        return result

    # --- Brands, categories, products, prices ---------------------------------------------------

    def catalog(self):
        from apps.catalog.models import (
            Brand,
            PriceList,
            PriceListItem,
            Product,
            ProductCategory,
            UnitOfMeasure,
        )

        units = {unit.code: unit for unit in UnitOfMeasure.objects.all()}

        def named(model, label, max_length, cache):
            label = label.strip() or "Uncategorised"
            key = label.lower()
            if key not in cache:
                found = model.objects.filter(name__iexact=label).first()
                if found is None:
                    code = slugify(label)[:max_length] or f"x{len(cache)}"
                    while model.objects.filter(code=code).exists():
                        code = f"{code[: max_length - 4]}-{len(cache)}"
                    found = model.objects.create(code=code, name=label[:100], **self.audit)
                    self.report[f"{model.__name__} added"] = (
                        self.report.get(f"{model.__name__} added", 0) + 1
                    )
                cache[key] = found
            return cache[key]

        brands, categories = {}, {}
        for row in self.load("brands"):
            named(Brand, text(row[2]), 30, brands)
        for row in self.load("categories"):
            named(ProductCategory, text(row["name"]), 40, categories)

        existing = set(Product.objects.values_list("sku", flat=True))
        taken = {sku.lower() for sku in existing}
        new, prices = [], {}
        for row in self.load("products"):
            sku = text(row["code"])[:50] or f"LEGACY-{row['id']}"
            if sku.lower() in taken:
                if sku in existing:
                    continue  # already imported
                sku = f"{sku[:42]}-{row['id']}"  # the old system let two products share a code
                if sku.lower() in taken:
                    continue
            taken.add(sku.lower())
            name = text(row["name"]) or sku
            new.append(
                Product(
                    sku=sku,
                    name=name[:200],
                    model_code=name[:50],
                    category=named(ProductCategory, text(row["category"]), 40, categories),
                    brand=named(Brand, text(row["brand"]), 30, brands)
                    if text(row["brand"])
                    else None,
                    uom=units[UNITS.get(text(row["unit"]).lower(), "PCS")],
                    notes=f"{MARK} (id {row['id']}).",
                    **self.audit,
                )
            )
            prices[sku] = number(row["price"])
        Product.objects.bulk_create(new, batch_size=500)
        self.report["products added"] = len(new)

        price_list = PriceList.objects.filter(company=self.company, is_default=True).first()
        if price_list is not None and new:
            ids = dict(Product.objects.filter(sku__in=list(prices)).values_list("sku", "id"))
            priced = set(
                PriceListItem.objects.filter(price_list=price_list).values_list(
                    "product_id", flat=True
                )
            )
            items = [
                PriceListItem(
                    price_list=price_list,
                    product_id=ids[sku],
                    min_quantity=1,
                    unit_price=price,
                    **self.audit,
                )
                for sku, price in prices.items()
                if price > 0 and ids[sku] not in priced
            ]
            PriceListItem.objects.bulk_create(items, batch_size=500)
            self.report["prices added"] = len(items)

    # --- Customers and suppliers -----------------------------------------------------------------

    def partners(self):
        from apps.organization.models import DocumentSequence
        from apps.partners.models import Customer, CustomerGroup, Supplier

        groups = {}
        for row in self.load("groups"):
            name = text(row[1])
            groups[name.lower()], made = CustomerGroup.objects.get_or_create(
                company=self.company,
                name=name[:100],
                defaults={"discount_percent": min(abs(number(row[3])), Decimal(100)), **self.audit},
            )
            self.report["customer groups added"] = (
                self.report.get("customer groups added", 0) + made
            )

        sequence, _ = DocumentSequence.objects.get_or_create(
            company=self.company,
            doc_type="CUS",
            defaults={"prefix": f"{self.company.code}-CUS-", "reset_yearly": False},
        )
        done = {
            int(match.group(1))
            for note in Customer.objects.filter(
                company=self.company, notes__startswith=MARK
            ).values_list("notes", flat=True)
            if (match := re.search(r"\(id (\d+)\)", note))
        }
        new, owing = [], []
        next_number = sequence.next_number
        for row in self.load("customers"):
            found = re.search(r'data-customer-id="(\d+)"', str(row[1])) or re.match(
                r"\s*(\d+)\s*$", text(row[1])
            )
            old_id = int(found.group(1)) if found else 0
            if not old_id or old_id in done:
                continue
            kind = "business" if "company" in str(row[3]).lower() else "individual"
            lines = [line.strip() for line in text(row[3]).split("\n") if line.strip()]
            lines = lines[1:] if lines and lines[0].lower() in ("individual", "company") else lines
            name = (lines[0] if lines else f"Customer {old_id}")[:200]
            phone = lines[1][:32] if len(lines) > 1 else ""
            address = " ".join(lines[2:]).strip(" ,") if len(lines) > 2 else ""
            deposit, due = number(row[4]), number(row[5])
            note = f"{MARK} (id {old_id})."
            if due or deposit:
                note += f" Balance there on {date.today():%d %b %Y}: due {due}, deposit {deposit}."
                owing.append((old_id, name, phone, due, deposit))
            new.append(
                Customer(
                    company=self.company,
                    code=f"{sequence.prefix}{next_number:0{sequence.padding}d}",
                    name=name,
                    customer_type=kind,
                    mobile=phone,
                    address=address,
                    group=groups.get(text(row[2]).lower()),
                    notes=note,
                    **self.audit,
                )
            )
            next_number += 1
        Customer.objects.bulk_create(new, batch_size=1000)
        sequence.next_number = next_number
        sequence.save(update_fields=["next_number", "updated_at"])
        self.report["customers added"] = len(new)
        self.report["customers with a balance there"] = len(owing)
        if owing:
            lines = ["old_id,name,phone,due,deposit"] + [
                f'{i},"{n.replace(chr(34), "")}",{p},{due},{dep}' for i, n, p, due, dep in owing
            ]
            (self.folder / "customer-balances-to-enter.csv").write_text(
                "\n".join(lines), encoding="utf-8"
            )

        added = 0
        for row in self.load("suppliers"):
            lines = [line.strip() for line in text(row[2]).split("\n") if line.strip()]
            if (
                not lines
                or Supplier.objects.filter(company=self.company, name__iexact=lines[0]).exists()
            ):
                continue
            from apps.organization.services import next_document_number

            Supplier.objects.create(
                company=self.company,
                code=next_document_number(self.company, "SUP", yearly=False),
                name=lines[0][:200],
                currency_id=self.company.currency_id,
                notes=f"{MARK}. Due there: {text(row[3])}.",
                **self.audit,
            )
            added += 1
        self.report["suppliers added"] = added

    # --- Expense categories, bank accounts, user accounts --------------------------------------

    ROLES = {
        "salesman": "salesperson",
        "accountant": "accountant",
        "product stock management": "storekeeper",
        "product transfer delivery quatation": "storekeeper",
    }

    def extras(self):
        from apps.access.models import Role, UserRole
        from apps.accounting.models import Account, ExpenseCategory, Journal
        from apps.users.models import User

        def rows(name):
            path = self.folder / f"{name}.json"
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []

        def cells(row):
            return [text(cell) for cell in (row if isinstance(row, list) else row.values())]

        other = Account.objects.filter(company=self.company, code="6900").first()
        added = 0
        for row in rows("expense_categories"):
            code, name = cells(row)[1:3]
            if not name or other is None:
                continue
            if ExpenseCategory.objects.filter(company=self.company, name__iexact=name).exists():
                continue
            code = code[:20] or slugify(name)[:20]
            if ExpenseCategory.objects.filter(company=self.company, code=code).exists():
                code = f"{code[:16]}-{added}"
            ExpenseCategory.objects.create(
                company=self.company, code=code, name=name[:100], account=other, **self.audit
            )
            added += 1
        self.report["expense categories added"] = added

        main_bank = Account.objects.filter(company=self.company, code="1120").first()
        added = 0
        for row in rows("accounts"):
            number, name = cells(row)[1:3]
            if "bank" not in name.lower() or main_bank is None:
                continue
            if Journal.objects.filter(company=self.company, name__iexact=name).exists():
                continue
            code = next(
                str(candidate)
                for candidate in range(1121, 1140)
                if not Account.objects.filter(company=self.company, code=str(candidate)).exists()
            )
            account = Account.objects.create(
                company=self.company,
                code=code,
                name=f"{name} ({number})"[:150],
                account_type=main_bank.account_type,
                parent=main_bank.parent,
                **self.audit,
            )
            journal_code = "".join(word[0] for word in name.split()).upper()[:8] or f"B{code}"
            while Journal.objects.filter(company=self.company, code=journal_code).exists():
                journal_code = f"{journal_code[:6]}{code[-2:]}"
            Journal.objects.create(
                company=self.company,
                code=journal_code,
                name=name[:100],
                journal_type="bank",
                default_account=account,
                **self.audit,
            )
            added += 1
        self.report["bank accounts added"] = added

        # People keep their sign-in name; the account stays switched off until an administrator
        # gives it a password and checks its role.
        roles = {role.code: role for role in Role.objects.all()}
        added = 0
        for row in rows("users"):
            username, email, old_role = cells(row)[1:4]
            username = re.sub(r"[^\w.@+-]", "_", username)[:150]
            if not username or "@" not in email:
                continue
            if (
                User.objects.filter(username__iexact=username).exists()
                or User.objects.filter(email__iexact=email).exists()
            ):
                continue
            person = User(
                username=username, email=email, is_active=False, default_company=self.company
            )
            person.set_unusable_password()
            person.save()
            role = roles.get(self.ROLES.get(old_role.lower(), ""))
            if role is not None:
                UserRole.objects.create(user=person, role=role, company=self.company)
            added += 1
        self.report["user accounts added (switched off)"] = added

    # --- Opening stock ---------------------------------------------------------------------------

    def stock(self, warehouses: dict, on_date: str | None):
        """One opening-stock adjustment per warehouse, at the old system's cost, against the
        owners' account. Minus and fractional piece quantities there are left out and counted."""
        from apps.accounting.models import Account
        from apps.catalog.models import Product
        from apps.inventory.models import StockAdjustment, StockAdjustmentLine, StockBalance

        if StockAdjustment.objects.filter(company=self.company, note__startswith=MARK).exists():
            raise CommandError("Opening stock from the previous system is already posted.")
        equity = Account.objects.get(company=self.company, code="3300")
        day = date.fromisoformat(on_date) if on_date else date.today()
        by_old_id = {}
        for product in Product.objects.filter(notes__startswith=MARK).select_related("uom"):
            if match := re.search(r"\(id (\d+)\)", product.notes):
                by_old_id[int(match.group(1))] = product
        costs = {row["id"]: number(row["cost"]) for row in self.load("products")}
        per_warehouse = defaultdict(dict)
        skipped = defaultdict(int)
        for old_id, entry in json.loads(
            (self.folder / "stock.json").read_text(encoding="utf-8")
        ).items():
            product = by_old_id.get(int(old_id))
            table = entry.get("product_warehouse") or []
            if product is None or len(table) < 2:
                skipped["products not found"] += 1
                continue
            for name, quantity in zip(table[0], table[1], strict=False):
                quantity = number(quantity)
                if quantity <= 0:
                    skipped["zero or minus quantities"] += quantity < 0
                    continue
                if not product.uom.allow_decimal and quantity != quantity.to_integral_value():
                    quantity = quantity.to_integral_value(rounding="ROUND_FLOOR")
                    skipped["fractions of a piece rounded down"] += 1
                    if quantity <= 0:
                        continue
                row = per_warehouse[name]
                row[product.pk] = row.get(product.pk, Decimal(0)) + quantity
        # Thousands of lines: written in bulk (one adjustment, its moves and one journal entry per
        # warehouse) with exactly what posting line by line would leave behind.
        from django.utils import timezone

        from apps.accounting.models import AccountingSettings
        from apps.accounting.services import LineSpec, create_and_post
        from apps.core.fields import round_money
        from apps.inventory.models import ProductCost, StockMove, StockStatus
        from apps.organization.services import currency_decimals, next_document_number

        inventory = AccountingSettings.objects.get(company=self.company).inventory
        if inventory is None:
            raise CommandError("Set the inventory account under Accounts → Default accounts first.")
        decimals = currency_decimals(self.company.currency_id)
        old_ids = {product.pk: old for old, product in by_old_id.items()}
        if ProductCost.objects.filter(
            company=self.company, product_id__in=old_ids, quantity__gt=0
        ).exists():
            raise CommandError("Some of these products already have stock.")
        ProductCost.objects.filter(company=self.company, product_id__in=old_ids).delete()
        totals: dict[int, list] = {}
        total_lines = 0
        for name, lines in per_warehouse.items():
            warehouse = warehouses[name]
            if StockBalance.objects.filter(warehouse=warehouse, quantity__gt=0).exists():
                self.report[f"{name}: skipped"] = "already has stock"
                continue
            StockBalance.objects.filter(warehouse=warehouse, product_id__in=lines).delete()
            adjustment = StockAdjustment.objects.create(
                company=self.company,
                date=day,
                warehouse=warehouse,
                reason=StockAdjustment.Reason.OPENING,
                counter_account=equity,
                note=f"{MARK}: opening stock.",
                number=next_document_number(self.company, "ADJ", day),
                **self.audit,
            )
            adjustment_lines, moves, balances, value = [], [], [], Decimal(0)
            for product_id, quantity in lines.items():
                unit = max(costs.get(old_ids[product_id], Decimal(0)), Decimal(0))
                line_value = round_money(quantity * unit, decimals)
                value += line_value
                total = totals.setdefault(product_id, [Decimal(0), Decimal(0)])
                total[0] += quantity
                total[1] += line_value
                adjustment_lines.append(
                    StockAdjustmentLine(
                        adjustment=adjustment,
                        product_id=product_id,
                        quantity=quantity,
                        unit_cost=unit,
                    )
                )
                balances.append(
                    StockBalance(
                        company=self.company,
                        warehouse=warehouse,
                        product_id=product_id,
                        status=StockStatus.AVAILABLE,
                        quantity=quantity,
                    )
                )
                moves.append(
                    StockMove(
                        company=self.company,
                        date=day,
                        warehouse=warehouse,
                        product_id=product_id,
                        status=StockStatus.AVAILABLE,
                        quantity=quantity,
                        unit_cost=unit,
                        value=line_value,
                        move_type=StockMove.MoveType.ADJUSTMENT,
                        reference=adjustment.number,
                        source_type="inventory.stockadjustment",
                        source_id=adjustment.pk,
                        note="Opening stock",
                        created_by=self.user,
                    )
                )
            StockAdjustmentLine.objects.bulk_create(adjustment_lines, batch_size=1000)
            StockBalance.objects.bulk_create(balances, batch_size=1000)
            StockMove.objects.bulk_create(moves, batch_size=1000)
            if value:
                description = f"Stock adjustment {adjustment.number}"
                adjustment.journal_entry = create_and_post(
                    self.company,
                    "JV",
                    day,
                    [
                        LineSpec(inventory, debit=value, description=description),
                        LineSpec(equity, credit=value, description=description),
                    ],
                    description=description,
                    reference=adjustment.number,
                    source_type="inventory.stockadjustment",
                    source_id=adjustment.pk,
                    user=self.user,
                )
            adjustment.status = "posted"
            adjustment.posted_at = timezone.now()
            adjustment.save()
            total_lines += len(lines)
            self.report[f"{name}: products / value"] = f"{len(lines)} / {value}"
        ProductCost.objects.bulk_create(
            [
                ProductCost(
                    company=self.company,
                    product_id=product_id,
                    quantity=quantity,
                    value=value,
                    average_cost=(value / quantity).quantize(Decimal("0.000001")),
                )
                for product_id, (quantity, value) in totals.items()
            ],
            batch_size=1000,
        )
        self.report["stock lines posted"] = total_lines
        self.report["stock value"] = sum((v for _q, v in totals.values()), Decimal(0))
        for key, value in skipped.items():
            self.report[key] = value

    # --- Past documents: the archive ------------------------------------------------------------

    #: file → (kind, column positions for lists that come as plain rows)
    DOCUMENTS = {
        "sales": ("sale", None),
        "quotations": ("quotation", None),
        "purchases": ("purchase", None),
        "sale_returns": ("sale_return", None),
        "purchase_returns": ("purchase_return", None),
        "transfers": ("transfer", None),
        "expenses": ("expense", None),
        "delivery_notes": ("delivery", None),
        "adjustments": (
            "adjustment",
            {"date": 1, "number": 2, "warehouse": 3, "products": 4, "note": 5},
        ),
        "project_invoices": (
            "project_invoice",
            {"number": 1, "date": 2, "party": 3, "total": 4, "user": 5},
        ),
        "money_transfers": (
            "money_transfer",
            {"date": 1, "number": 2, "from_account": 3, "to_account": 4, "total": 5},
        ),
        "customer_transactions": (
            "customer_payment",
            {
                "number": 2,
                "party": 3,
                "type": 4,
                "total": 5,
                "payment_method": 6,
                "bank": 7,
                "note": 8,
            },
        ),
    }

    def documents(self):
        """Replace the archive with what the previous system's lists hold now."""
        from datetime import datetime

        from apps.reports.models import LegacyDocument

        def day(value):
            value = text(value)
            for pattern, length in (("%Y-%m-%d", 10), ("%d-%m-%Y", 10), ("%d/%m/%Y", 10)):
                try:
                    return datetime.strptime(value[:length], pattern).date()
                except ValueError:
                    continue
            return None

        def amount(value):
            value = text(value)
            return number(value) if re.search(r"\d", value) else None

        def first(row, *names):
            for name in names:
                if text(row.get(name)):
                    return text(row[name])
            return ""

        for file, (kind, columns) in self.DOCUMENTS.items():
            path = self.folder / f"{file}.json"
            if not path.exists():
                continue
            rows = json.loads(path.read_text(encoding="utf-8"))
            new = []
            for raw in rows:
                if columns is not None:
                    cells = (
                        raw if isinstance(raw, list) else [raw.get(str(i)) for i in range(len(raw))]
                    )
                    row = {
                        name: cells[index] for name, index in columns.items() if index < len(cells)
                    }
                else:
                    row = raw
                used = {
                    "date",
                    "number",
                    "reference_no",
                    "party",
                    "customer",
                    "supplier",
                    "warehouse",
                    "total",
                    "grand_total",
                    "amount",
                    "paid_amount",
                    "paid",
                    "due",
                    "status",
                    "sale_status",
                    "purchase_status",
                    "payment_status",
                    "options",
                    "key",
                }
                details = {}
                for name, value in row.items():
                    if name in used or str(name).isdigit():
                        continue
                    if isinstance(value, list):  # the row's own detail line, as the list held it
                        value = " | ".join(
                            text(part).strip(' []"') for part in value if text(part).strip(' []"')
                        )
                    value = text(value)
                    if value and value != "[object Object]":
                        details[name] = value[:2000]
                warehouse = first(row, "warehouse")
                if not warehouse and (row.get("from_warehouse") or row.get("to_warehouse")):
                    warehouse = (
                        f"{text(row.get('from_warehouse'))} → {text(row.get('to_warehouse'))}"
                    )
                    details.pop("from_warehouse", None)
                    details.pop("to_warehouse", None)
                new.append(
                    LegacyDocument(
                        company=self.company,
                        kind=kind,
                        number=first(row, "number", "reference_no")[:80],
                        date=day(row.get("date")),
                        party=first(row, "party", "customer", "supplier")[:200],
                        warehouse=warehouse[:200],
                        status=first(row, "status", "sale_status", "purchase_status")[:60],
                        payment_status=first(row, "payment_status")[:60],
                        total=amount(row.get("total", row.get("grand_total", row.get("amount")))),
                        paid=amount(row.get("paid_amount", row.get("paid"))),
                        due=amount(row.get("due")),
                        details=details,
                    )
                )
            LegacyDocument.objects.filter(company=self.company, kind=kind).delete()
            LegacyDocument.objects.bulk_create(new, batch_size=2000)
            self.report[f"archive: {kind}"] = len(new)
