# 12: Installation

Needs: a customer, a service product with income account 4300 Installation income (Catalog →
Products → Accounting), and optionally a project (script 11).

## Technicians

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 12.1 | **Installation → Technicians → Add**: Rajesh, branch Showroom 1, hourly cost 3, skills "electrical". Add Omar at 3.5. | Both listed with cost and 0 open jobs. | |

## Work orders

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 12.2 | **Installation → Work orders → New job**: title, customer, date tomorrow 09:00, 3 hours, tick Rajesh and Omar → **Save**. | Number `KW-WO-<year>-00001`, status **Scheduled**; the technicians' Open jobs show 1. | |
| 12.3 | **Schedule**. | The job sits on tomorrow's column with time, title, customer and crew; today is highlighted. Technician filter narrows the board. ‹ › move by a week. | |
| 12.4 | Open the job → **Start**. | Status **In progress** (amber card on the schedule). | |
| 12.5 | **Complete**: 3.5 hours, signed by "Ahmad". | Status **Completed**; Labour cost = 3.5 × (3 + 3.5) = 22.750. The job can no longer be edited or cancelled. | |
| 12.6 | **Bill** → service Installation, 3.5 × 10 → **Create draft invoice**. | Sales → Invoices opens the draft (work order linked). Post it: the entry credits **4300 Installation income**, not Showroom sales. The job shows "Billed on KW-INV-…". | |
| 12.7 | New job → **Cancel job** with a reason. | Status **Cancelled**; it disappears from the schedule. Cancelling without a reason is refused. | |
| 12.8 | New job with no technicians → **Complete**. | Error: assign at least one technician. | |
| 12.9 | From a project → **New job**. | The job is prefilled with the project's customer and site; once completed, its labour cost appears in the project's Labour cost and margin. | |
| 12.10 | *Access*: an "Installer" role at Showroom 2 only. | Sees no Showroom 1 jobs in the list or the schedule. | |
