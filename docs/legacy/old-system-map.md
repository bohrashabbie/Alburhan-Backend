# Old system → new ERP: module map

The system Al-Burhan uses today (alburhan-regional.co, built by Techinvest on a POS template) was
reviewed page by page on 24 Nov 2026, view only: 65 pages, every list, form and pop-up. Goal: the
new ERP has every module in the same place, under the same names, so staff find things where they
expect them, with the new ERP's controls (accounting, permissions, multi-country) underneath and a
cleaner screen design.

Scale of their data: 540 quotations this year, ~26,900 invoices, 663 delivery schedules,
13,988 customers, 11 warehouses (Hawally 1st and 2nd branch, Shuweikh showroom, …), 47 brands,
45 categories.

Legend: **Have** = the new ERP already does it · **Extend** = exists, needs the old system's extra
fields or views · **Build** = missing.

## Their menu, in their order

| # | Old menu → page | New ERP | Status |
|---|---|---|---|
| 1 | **Dashboard**: total sale, sale return, purchase return, profit (today / 7 days / month / year); cash flow chart (received vs sent); month donut (purchase / revenue / expense); yearly purchased vs sold bars; recent sale / purchase / quotation / payment; best sellers (month, year by qty, year by value) | Overview | **Build**: their dashboard layout on our figures |
| 2 | **Product** → Category (image, Arabic name, parent, product count, stock qty, stock worth) | Product families | **Extend**: stock count and worth columns, CSV import |
| | Product List (image, name, code, brand, category, qty, unit, price, cost, stock worth) | Products | **Extend**: qty and stock worth columns, CSV import |
| | Add Product: model no, code, barcode, brand, category, unit, sale/purchase unit, cost, price, finish, power, cover, mounting, IP, colour kelvin, beam, warranty, size, **rack no**, featured, opening stock per warehouse, details, **datasheet** | Products | **Extend**: rack no, mounting, cover, featured, datasheet file, alert qty; the rest exists |
| | Print Barcode | – | **Build** |
| | Adjustment List / Add Adjustment | Adjustments | Have |
| | Stock Count (by warehouse, category, brand; full or partial; upload count file) | Stock counts | **Extend**: start by category/brand, count file upload |
| 3 | **Purchase** → Add Purchase / Purchase List (status, paid, due, payment status, add payment from the list) | Purchase orders, Goods receipts, Supplier bills, Supplier payments | **Extend**: one "Purchase list" with paid / due and pay-from-list |
| 4 | **POS** (customer, warehouse, biller, drafts, discount, coupon, order tax, shipping, gift card) | POS till | Have (**Extend**: held drafts) |
| | Cash register (user, warehouse, cash in hand, opened/closed) | POS shifts | Have |
| 5 | **Invoice** → Create Invoice / Invoice List (biller, sale status, payment status, paid, due, returned; add payment and add delivery from the list; filter by type and payment method) | Invoices, Receipts | **Extend**: paid / due / returned columns, pay and deliver from the list |
| | **Dummy Invoice** (looks like an invoice, no stock or accounting) | – | **Build**: pro-forma invoice |
| | Delivery Notes | Delivery notes | Have |
| | **Frozen stock delivery notes**: stock held for quotations, delivered later | Reservations (projects only) | **Build**: freeze stock on a quotation, deliver against it |
| 6 | **Delivery Schedule**: every delivery note gets a date, time and status; **daily limits per warehouse** | – | **Build** |
| 7 | **Project Invoice**: free-text lines (description, qty, unit, price), separate project customers | Projects, progress invoices | **Extend**: description-only invoice lines |
| 8 | **Expense** → Expense List (date, warehouse, category, amount, paid from account, attachment) / Expense Category | – (journal entries only) | **Build** |
| 9 | **Quotation** → Create (stock by branch pop-up, product images, customer type) / List (status, **revision history**, snapshot before edit, change status) | Quotations, item card | **Extend**: revision history, stock-by-branch pop-up in the form |
| 10 | **Transfer** → Create / List / **Import by CSV** | Transfers | **Extend**: CSV import |
| 11 | **Return** → Sale return | Credit notes (restock) | Have |
| | Purchase return | – | **Build** |
| 12 | **Accounting** → Bank accounts (initial balance, balance adjustment), **Money transfer**, Balance sheet per account, account statement | Chart of accounts, journals, reports | **Extend**: simple "Bank & cash accounts" and "Money transfer" screens on top of the ledger |
| | Customer list / groups / **transactions** (cheque, bank) / **customer balance sheet** / **customer statement** | Customers, receipts, ageing | **Build**: customer statement and balance sheet; **Extend**: customer groups with a discount % |
| 13 | **Salary** → Employee / Monthly Salary (basic, transportation, overtime, accommodation, other; salary slip) | HR employees, salaries, payroll | Have (**Extend**: overtime per month, printable slip) |
| 14 | **User** → users with role, warehouses, biller, **monthly target**; billers; suppliers | Users and roles, companies, suppliers | **Extend**: monthly sales target per user |
| 15 | **Customer** → customers with group, type, default warehouse; **deposits**, clear due | Customers, receipts (advance) | **Extend**: deposit and "clear due" shortcuts |
| 16 | **Reports** → best seller; daily / monthly sale (calendar); daily / monthly purchase (calendar); warehouse stock chart; product expiry; quotation frozen stock by company; quantity alert; **daily sale objective** | P&L, balance sheet, ageing, reorder alerts, slow-moving | **Build**: best seller, sale and purchase calendars, warehouse stock chart, frozen stock, sales-target report. Product expiry not needed (lighting has no expiry) |
| 17 | **Settings** → roles and permissions, warehouses, brands, units (with conversions) | Roles, warehouses, brands, units | Have (**Extend**: unit conversions) |
| – | Send notification with reminder date (top bar) | HR notifications | **Build** (small) |

## What the new ERP adds that the old one doesn't have

Double-entry accounting behind every document, VAT and XBRL filings, several countries and
currencies, branch-level permissions, projects with BOQ and reservations from several warehouses,
installation work orders, leave and payroll with manager approval by email, the item card,
printed A4 documents with photos, Arabic.

## Built so far (24 Nov 2026)

| Old system | Where it is now |
|---|---|
| Menu order and names | One sidebar: Dashboard, Product, Purchase, POS, Invoice, Delivery schedule, Projects, Expense, Quotation, Transfer, Return, Accounting, Salary, User, Customer, Reports, Settings |
| Dashboard | Dashboard: period totals, cash flow, month at a glance, yearly bought vs sold, recent transactions, best sellers |
| Expense list / category | Expense → Expense list, Expense category (posts to the accounts; cancel instead of edit) |
| Delivery schedule + daily limits | Delivery schedule (date, time, status per delivery note; limit per warehouse) |
| Purchase list paid / due | Purchase → Purchase list (Paid, Due columns from the order's bills) |
| Invoice list paid / due / returned | Invoice → Invoice list |
| Dummy invoice | Invoice → Dummy invoice (a pro-forma quotation; prints as "Pro-forma invoice") |
| Frozen stock | "Freeze stock" on a quotation; Product → Frozen stock lists what is held |
| Purchase return | Return → Purchase return (stock out, supplier balance down, open bill settled first) |
| Bank accounts, money transfer | Accounting → Bank & cash accounts, Money transfer |
| Customer statement / balance sheet | Customer → Customer statement, Customer balance sheet |
| Customer group with % | Customer → Customer group; the % is the starting discount on new lines |
| Monthly target, sale objective | User list → Monthly sales target; Reports → Sales target report |
| Best seller, daily / monthly sale and purchase, warehouse stock chart | Reports |
| Print barcode | Product → Print barcode |
| Import by CSV | "Import CSV" on Product list and Customer list |
| Quotation revision history | "Revision history" inside a quotation |
| Product extras | Mounting, cover, rack no., datasheet link, featured |

Added after that: stock count filled by category or brand, transfer from a CSV file, unit
conversions (base unit and how many), a receipt attached to an expense, and the bell with
notifications and reminder dates.

## Their data (moved 24 Nov 2026)

Read from the previous system's own lists, view only. The demo records were removed first
(practice transactions, the demo MS products, customers, suppliers, Showroom 1-4, the training
users and employees). Three commands, each safe to repeat:

    manage.py import_legacy <folder> --company KW              # master data
    manage.py import_legacy <folder> --company KW --stock      # opening stock, once
    manage.py import_legacy <folder> --company KW --documents  # past documents → archive

| What | Count | Notes |
|---|---|---|
| Warehouses | 11 | Their 10 plus "Shuwaikh Small Showroom", which only their stock figures mention. Showrooms and the office also became branches. |
| Brands, categories | 45, 44 | Matched by name. |
| Products | 5,034 | Their code is our SKU, their model number is the name. 165 shared a code with another product; those got the old id added. |
| Prices | 4,202 | Into the Kuwait retail list. |
| Customers, groups | 13,983, 4 | Name, phone, address, group; the note carries the old id. |
| Suppliers | 3 | |
| Expense categories | 8 | All booked to 6900 Other expenses until each is pointed at its own account. |
| Bank accounts | 2 | Warba Bank and Commercial Bank, each with its own account and journal. |
| Users | 26 | **Switched off**, no password, with the closest role (salesman → salesperson, accountant, stock → storekeeper; their "Admin" users get no role). An administrator switches each on, sets a password and checks the role and branch. |
| Opening stock | 14,667 lines | Quantities above zero, at their cost, against 3300 Owners' current account. |
| Past documents | 50,541 | Reports → **Previous system**: 26,846 invoices, 20,105 purchases, 1,791 sale returns, 864 quotations, 663 delivery notes, 212 adjustments, 23 transfers, 16 expenses and the rest. Header details only (date, number, party, status, amounts). |

**To decide or finish**

- **The stock value is not real.** It comes to 26.5 million KWD, against about 1.28 million KWD
  of sales in two years. 1,675 products sit at exactly 5,000 pieces in Shuwaikh Store (a
  placeholder there, worth 22.7 million at their costs), and some costs look like placeholders
  too (500.000 for a 1 W light). Count Shuwaikh Store and post the count, or re-run the stock
  step without that warehouse.
- **Minus stock**: 926 warehouse quantities are minus there; those start at zero.
- **Customer balances**: 41 customers have a due or deposit there. The amount is in each
  customer's note and in `customer-balances-to-enter.csv`; nothing is booked.
- **Product lines of past documents** are not in the archive (that needs one request per
  document, about 50,000). Their totals and status are.
- Past documents are history: the new books start from the opening stock, with no earlier
  sales, cash or bank balances. Enter opening bank and cash balances by journal entry.
- Product photos and descriptive fields (finish, colour…) did not come over.
- The till has no register yet: add one per showroom under POS → Registers.

## Not built yet

- POS held drafts, coupons and gift cards.
- CSV import for categories, brands, units, suppliers and warehouses on their own screens (the
  import command above covers them for the move).
- Product expiry report (not needed for lighting).

## Build order

1. **Look and place**: sidebar in their order and names (Dashboard, Product, Purchase, POS,
   Invoice, Delivery Schedule, Project Invoice, Expense, Quotation, Transfer, Return, Accounting,
   Salary, User, Customer, Reports, Settings), and their dashboard on our figures.
2. **Daily-work gaps**: expenses, delivery schedule, frozen stock on quotations, dummy invoice,
   purchase list with paid / due, invoice list with paid / due and pay-from-list, purchase return.
3. **Money and customers**: bank & cash accounts with money transfer, customer statement and
   balance sheet, customer groups, deposits.
4. **Reports**: best seller, sale / purchase calendars, warehouse stock chart, sales targets.
5. **Odds and ends**: barcode printing, CSV imports, quotation revision history, product extra
   fields, unit conversions, notifications.
6. **Move their data**: customers, products, opening stock and balances from the old system.
