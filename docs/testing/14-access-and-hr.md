# 14: Access by country and store, and HR

Needs: accounting set up for the country (script 06), a main administrator, and a few users.
For real emails set `EMAIL_BACKEND`, `EMAIL_HOST`… and `FRONTEND_URL` in `.env`; without them
emails print in the server console (or set `EMAIL_BACKEND=django.core.mail.backends.filebased.EmailBackend`
to write them to `sent_emails/`).

## Access: country, then stores

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 14.1 | As main administrator, **Settings → Users**. | An Access column shows `KW · Showroom 1`, `KW · Whole country`… and "Main administrator" for main admins. | |
| 14.2 | **Add** a user: Country Kuwait, Home store only lists Kuwait's stores. Change Country to UAE. | Home store now lists UAE's stores only. | |
| 14.3 | Open the user → Access: Role, Country, tick **Showroom 2** and **Showroom 3** → **Add access**. | Two lines under the country, one per store. | |
| 14.4 | Country **All countries** (main administrator only) → Add access. | One whole-country line per country. | |
| 14.5 | Tick **Main administrator** on a user and save. | Their list badge reads Main administrator; they see every country. | |
| 14.6 | Sign in as a Kuwait administrator (users + roles permissions for Kuwait only). Open a user who also has a UAE role and change the password. | Refused: "This person also has access in UAE. Ask an administrator there." | |
| 14.7 | Same Kuwait administrator: edit a main administrator; or tick Main administrator on someone. | Refused. The switch is not even shown to them. | |
| 14.8 | A salesperson at Showroom 1 opens a document that needs a warehouse. | All Kuwait warehouses are listed to pick from; editing a warehouse other than Showroom 1's is refused. | |

## HR setup

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 14.9 | **HR → HR settings**. | Kuwait defaults: weekend Fri + Sat, pay days 26, salaries expense 6100, payable 2150. Set the HR email and **Save changes**. | |
| 14.10 | **Leave types**. | Annual (from the annual balance), Sick (15 a year), Emergency (5), Unpaid — created automatically. | |
| 14.11 | **Departments → Add** Showrooms. **Employees → Add** a manager (with email), then an employee with that manager, a login, hire date and salary. | Numbers `KW-EMP-00001`…; the manager appears in the Manager list straight away; the drawer shows the leave balance. | |
| 14.12 | Make A the manager of B and B the manager of A. | Refused: "That would make this person their own manager." | |

## Leave

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 14.13 | Sign in as the employee → profile menu → **My leave** → **Ask for leave**: Annual, Sun–Thu. | The form counts 5 working days while typing. After sending: status Waiting for approval; balance shows 5 waiting. | |
| 14.14 | Check the emails. | Manager: "Leave request KW-LV-… from …" with the employee's email, dates, reason and an **Approve or reject** button. HR: a copy. Employee: a confirmation. | |
| 14.15 | Open the manager's link in a private window (not signed in). | The request with the balance; **Approve** / **Reject**. Nothing is decided by just opening the link. | |
| 14.16 | **Reject** without a reason; then with one. | First refused; then Rejected, and the employee and HR are emailed with the reason. | |
| 14.17 | Open the same link again. | Shows the decision; can't decide twice. Tamper with the link → "This link is not valid." | |
| 14.18 | Ask for more sick days than are left; ask for dates that overlap a request. | "Only … days of sick leave left this year." / "These dates overlap KW-LV-…". | |
| 14.19 | Manager signs in → **My leave**. | "Waiting for your approval" lists the team's pending requests; Approve works in the app (Decided via: in the app). | |
| 14.20 | **HR → Leave requests** → open one. | Who decided, when and how, and every email sent (a failed one in red with the reason). | |
| 14.21 | Employee cancels an approved leave. | Cancelled; manager and HR are emailed. | |

## Payroll

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 14.22 | Approve 2 days of **unpaid** leave in a month. **HR → Payroll → New payroll** for that month. | One payslip per employee; that employee shows 2 unpaid days and a deduction of (monthly pay ÷ 26) × 2. Someone hired mid-month gets a share of the month. | |
| 14.23 | Type 15 in Additions for someone; **Recalculate**. | Net and totals change; the 15 is kept after recalculating. | |
| 14.24 | **Post**. | Status Posted; journal entry debits 6100 and credits 2150 with the net. Payslips can't be edited, recalculated or deleted. The leave shows "Accounted in payroll". | |
| 14.25 | **Pay salaries** from the bank journal. | Status Paid; the payment entry debits 2150 and credits the bank. | |
| 14.26 | New payroll for the same month again. | Refused: there is already a payroll for that month. | |
| 14.27 | *Access*: an HR officer for Kuwait only. | Sees Kuwait employees, leave and payroll only. | |
