# Al-Burhan ERP: Backend

Django 5.2 LTS + Django REST Framework + PostgreSQL. A single backend for the ERP web app and the
POS web app, covering every country company (Kuwait, UAE, China, Egypt).

## Run locally

```bash
uv sync                                  # install dependencies
cp .env.example .env                     # then set DATABASE_URL (SQLite is used if it is unset)
uv run python manage.py migrate          # also syncs the permission catalogue
uv run python manage.py seed_demo        # demo companies, showrooms, warehouses (DEBUG only)
uv run python manage.py createsuperuser
uv run python manage.py runserver
```

- API docs: http://localhost:8000/api/docs/
- Admin: http://localhost:8000/admin/
- Tests: `uv run pytest` · Lint: `uv run ruff check . && uv run ruff format .`
- With Docker: `docker compose up` (PostgreSQL 17 + API)

## Structure

```
config/            settings (base/dev/test/prod), urls
apps/core/         shared base models, money/quantity fields, permission + company-scope mixins
apps/organization/ currencies, countries, companies, branches, warehouses, FX rates, numbering
apps/access/       roles & permissions: Permission, Role, RolePermission, UserRole
apps/users/        user accounts, /me
```

## Key rules for every module

- **Countries are data.** Every business record carries `company`. Viewsets extend
  `CompanyScopedModelViewSet`, so users only see rows in companies and branches they have access to.
- **Access = UserRole.** A user holds a role *in a company*, optionally limited to *one branch*.
  Each role's permissions come from RolePermission. Each app declares its permission codes in
  `permissions_catalog.py`, and they sync to the database on `migrate`. A viewset sets
  `permission_resource = "<app>.<thing>"`; list/retrieve/create/update/destroy then need
  `.view/.create/.update/.delete`. Custom actions go in `action_permissions` and are denied if missing.
- **No privilege escalation.** You can only grant a permission, or assign a role, that you
  already hold in that same company or branch.
- **Money** uses `MoneyField` (3 decimals, because KWD has 3). Round to the currency's own
  decimal places with `round_money`. The group reporting currency is `ERP_BASE_CURRENCY = "KWD"`.
- **Document numbers** come from `next_document_number(company, "INV")`, giving e.g. `KW-INV-2026-00001`.
  Numbers are allocated under a row lock, per company and document type, and restart each year.
- **Audit:** `AuditedModel` records who created and last changed each row. django-auditlog records
  field-level history.
