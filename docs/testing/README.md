# Al-Burhan ERP: test scripts

Each module has two layers of tests:

1. **Automated tests.** They run in seconds and check the business rules and security on every change:
   ```bash
   cd Alburhan-Backend
   uv run pytest
   ```
   They must all pass before any release.
2. **Manual test scripts** (this folder). A person follows them in the browser to confirm the
   screens work as people expect. Mark each line **Pass** or **Fail**. For a fail, write what
   happened and attach a screenshot.

| Script | Covers |
|---|---|
| [01-sign-in-and-layout.md](01-sign-in-and-layout.md) | Sign-in, language, company switch, menus |
| [02-organization.md](02-organization.md) | Companies (logo, country), branches, warehouses, exchange rates |
| [03-roles-and-users.md](03-roles-and-users.md) | Roles, the permission grid, users, role assignments, access limits |
| [04-catalog.md](04-catalog.md) | Product families, brands, units, products, price lists |
| [05-customers-and-suppliers.md](05-customers-and-suppliers.md) | Customers, suppliers, contacts, automatic codes |
| [06-accounting.md](06-accounting.md) | Chart of accounts, journals, periods, journal entries, reversals, trial balance, ledger |

## Before you start

- Web app: http://localhost:3000 (or the test server address).
- Admin account: `info@alburhanregional.com`.
- For the access tests, create two test users as described in script 03.
- Test in **Chrome or Edge**. Repeat the scripts marked *RTL* with the language set to **عربي**.
- Use made-up data only (e.g. customer "Test Customer 01"). Delete or deactivate it afterwards.

## Result sheet (copy for each test round)

| Script | Tester | Date | Passed | Failed | Notes |
|---|---|---|---|---|---|
| 01 | | | | | |
| 02 | | | | | |
| 03 | | | | | |
| 04 | | | | | |
| 05 | | | | | |
| 06 | | | | | |
