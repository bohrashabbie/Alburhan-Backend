# 11: Projects department

Needs: accounting (script 06), stock in Main warehouse (script 07), a business customer (script 05),
a service product (e.g. `INSTALL-HR`) and the Projects branch.

## Project and BOQ

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 11.1 | **Projects → Projects → New project.** Name, customer, branch Projects, site address, contract value 18 500, retention 5 %. Add BOQ lines: 240 × MS-DL-R01-10W at 4.250 (cost 2.145), 120 × INSTALL-HR at 9 (cost 4.5). **Save**. | Number `KW-PRJ-<year>-00001`, status **Lead**. The list shows contract and BOQ value. | |
| 11.2 | Open it. | Tiles: Contract value, Invoiced 0, Received 0, Outstanding 0, BOQ value, Materials cost 0, Labour cost 0, Margin. A "Move to" row with **Won** and **Lost**, plus **Quotation**. | |
| 11.3 | Change a BOQ quantity and save. | The same line keeps its id (no duplicate), totals update. An issued line can't be removed. | |
| 11.4 | **Quotation**. | Sales → Quotations opens a draft with every BOQ line at BOQ price; the project shows **Quoted**. | |
| 11.5 | **Move to Won**. Then try **Closed**. | Status **Won**; Closed is not offered (only In progress / Lost). | |

## Stock for the project

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 11.6 | **Reserve stock** → Main warehouse. | "1 reservations made." Inventory → Reservations shows 240 downlights held with the project number. Reserving again makes 0. | |
| 11.7 | **Materials to site** (from the project). | A draft issue prefilled with the project, every stock BOQ line and the quantity left. | |
| 11.8 | Issue 100 downlights to "Site foreman" → **Issue to site**. | Number `KW-MI-…`, cost = 100 × average cost. Stock levels: Main −100. The reservation drops to 140. Journal: debit 5300 Installation materials, credit 1140 Inventory. The project becomes **In progress**; BOQ shows Issued 100. | |
| 11.9 | Issue more than free + reserved for us. | Error "Only … free in Main". | |
| 11.10 | Add a product that is not on the BOQ via the search box and issue it. | Works; marked "not on BOQ", no BOQ progress. | |

## Progress invoicing

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 11.11 | **Progress invoice** → Bill now 100 downlights → **Create draft invoice**. | Sales → Invoices opens the draft (project set, BOQ-priced line). Post it. | |
| 11.12 | Back on the project. | Invoiced 425.000, Outstanding 425.000, invoice count 1; BOQ progress shows Billed 100. Margin = invoiced − materials − labour. | |
| 11.13 | Record a receipt against that invoice (script 08). | Received and Outstanding update. | |
| 11.14 | **Completed** → **Closed**. | Closed releases the remaining reservations; nothing more can be issued; the form is read-only. | |
| 11.15 | *Access*: a "Project manager" role at the Projects branch. | Sees only projects of that branch; the list omits the summary; the rail shows Projects. | |
