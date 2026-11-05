# 13: Dashboard, financial reports and compliance

Needs: some posted sales, purchases and payments (scripts 08–12). A user with
"View the management dashboard" for the dashboard, "View financial reports" for the statements,
and "View VAT returns and compliance exports" for the compliance pages.

## Dashboard (Home)

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 13.1 | Open **Home**. | Four tiles: Sales today, Sales this month (with % vs last month), Receivables (amber with the overdue amount when any invoice is past due), Cash & bank (with each cash/bank account). | |
| 13.2 | Hover a column in **Sales by day**. | A dark tooltip with the date and amount. Today's column is purple; earlier days are light purple. | |
| 13.3 | **By branch** and **Top products**. | Branches ranked by this month's sales with bars; top 6 products with quantity and sales. | |
| 13.4 | **Operations**: click each figure. | Payables, Stock value, Active projects, Jobs today, Open purchase orders and Open POS shifts each open their page. | |
| 13.5 | Choose a branch in the avatar menu. | Sales figures narrow to that branch. | |
| 13.6 | A user without the dashboard permission. | Home shows a welcome message instead of figures. | |

## Statements

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 13.7 | **Accounts → Profit & loss**, this year. | Income, Cost of sales, Gross profit, Operating expenses, Net profit, grouped by top-level account; side tiles with income, gross profit, gross margin %, net profit. | |
| 13.8 | Click an account line. | The general ledger opens for that account and period. | |
| 13.9 | **Balance sheet** as of today. | Assets on the left; Liabilities, Equity and "Current year earnings" on the right; a **Balanced** badge. Total assets = total liabilities and equity. | |
| 13.10 | Compare the P&L net profit with "Current year earnings" (same year, no closing yet). | They are equal. | |
| 13.11 | **Ageing** → receivables, as of today; then set the date a year ahead. | Each customer's open balance under Not due / 1–30 / 31–60 / 61–90 / Over 90; a year ahead everything moves to Over 90 (red). Switch to payables for suppliers. | |

## Compliance

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 13.12 | Switch to the UAE company, set Sales settings VAT 5 %, post an invoice and a supplier bill with VAT → **VAT return** for the month. | Standard-rated supplies per branch with VAT, credit notes deducted, zero-rated supplies, taxable purchases with input VAT, **Net VAT payable** = output − input. The tax registration number shows on the side. | |
| 13.13 | Kuwait company → VAT return. | The note says Kuwait has no VAT yet; figures show 0 VAT. | |
| 13.14 | **Filings & e-invoicing → Download XBRL** for the year. | An `.xml` file with `ifrs-full:Revenue`, `ProfitLoss`, `Assets`, `Liabilities`, `Equity` facts in the company currency; the values match the statements. | |
| 13.15 | Egypt section: pick a posted invoice → **Preview document**. | The ETA v1.0 JSON: issuer (company tax number), receiver, invoice lines with `T1` VAT, totals; `signatures` empty (to be signed with the ETA certificate). **Download JSON** saves it. | |
| 13.16 | Pick a draft invoice. | Error: post the invoice first. | |
| 13.17 | A user with only "View financial reports". | Statements work; VAT return and filings return 403 / are hidden in the menu. | |
