# 06: Accounting core

Use a **test company** if possible. Posted entries can only be reversed, never deleted.

## Setup

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 6.1 | Open **Accounts → Chart of accounts**. | About 52 accounts in levels (Assets → Current assets → Cash on hand…). Headings are bold and have a **Heading** badge. Receivable and payable accounts show **By partner**. | |
| 6.2 | Filter **Type = Expense**. | Only expense accounts (5000 and 6000 groups). | |
| 6.3 | **Add** account `6220`, "Showroom cleaning", type Expense, Under heading = Operating expenses → **Save**. | It appears indented under Operating expenses. | |
| 6.4 | Add an account with code `6220` again. | Error: this code is already used. | |
| 6.5 | Set **Under heading** to an account of another type (e.g. an Asset heading for an Expense). | Error: the parent must be of the same type. | |
| 6.6 | Open **Journals**. | JV General, SI Sales, PI Purchases, CS Cash (account 1110), BK Bank (account 1120). | |
| 6.7 | Open **Periods**. | 12 monthly periods for this year, all **Open**. **Months of [next year]** creates the next year's months. | |
| 6.8 | Open **Default accounts**. | Every field is filled (e.g. receivable = 1130, sales = 4100). Each dropdown only offers accounts of a matching type. | |
| 6.9 | Switch company to one with no chart, open **Chart of accounts**. | An empty page with a **Set up accounting** button. Clicking it creates the full chart. | |

## Journal entries

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 6.10 | **Journal entries → New entry**. | The panel shows journal JV, today's date, currency KWD and two empty lines. **Post** is disabled. | |
| 6.11 | Line 1: 6200 Showroom rent, Debit `450`. Line 2: 1120 Bank, Credit `400`. | The footer shows "Difference: 50.000 KWD" in red. **Post** stays disabled. | |
| 6.12 | Change the credit to `450`. | The footer shows Total 450.000 / 450.000. **Post** is enabled. | |
| 6.13 | Type a debit on a line that has a credit. | The credit clears: a line can only be one side. | |
| 6.14 | **Save draft**. | Listed with status **Draft** and no number. | |
| 6.15 | Reopen it and click **Post**. | Status **Posted**, numbered `KW-JV-<year>-00001`. | |
| 6.16 | Open the posted entry. | Read-only, with a **Reverse** section. There is no Save or Delete. | |
| 6.17 | **Reverse** with today's date. | A new posted entry, "Reversal of KW-JV-…", appears. The original shows **Reversed**. | |
| 6.18 | Try to reverse the original again. | No Reverse button (it's already reversed). | |
| 6.19 | New entry with only one line filled → **Post**. | Error: an entry needs at least two lines. | |
| 6.20 | New entry in **USD** with no USD→KWD exchange rate saved → **Save draft**. | Error under Exchange rate: no rate found. Add a rate in Settings → Exchange rates and try again: the entry posts, and lines show the KWD amounts underneath. | |
| 6.21 | Amount `10.1234` in KWD. | Error: KWD allows 3 decimal places. | |
| 6.22 | Delete a **draft** entry. | It is removed. Posted entries have no Delete. | |

## Periods

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 6.23 | Leave a draft dated this month, open this month's period → **Close period**. | Error: post or delete the draft entries first. | |
| 6.24 | Delete the draft, then close the period. | Status **Closed**. | |
| 6.25 | Try to post an entry dated in the closed month. | Error: period … is closed. | |
| 6.26 | **Reopen period**. | Posting works again. | |

## Reports

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 6.27 | **Trial balance** from 1 Jan to today. | Accounts grouped by Asset, Liability, Equity, Income, Expense. Each shows opening, debit, credit and closing ("Dr"/"Cr"). The total line is **not** marked "not balanced". | |
| 6.28 | Click an account in the trial balance. | **General ledger** opens for that account and dates: the opening balance, every posted line, a running balance and the closing balance. | |
| 6.29 | The closing balance in the ledger. | Equals that account's closing balance in the trial balance. | |

## Access

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 6.30 | Create a role "Accounts clerk" with Journal entries View/Create/Edit (not Post). Assign it to a test user. | As that user: they can create and save drafts. **Post** is not shown. | |
| 6.31 | Give a role "Accountant" the extra permission **Post journal entries**. | That user can post. | |
| 6.32 | A user without **View financial reports**. | The Trial balance and General ledger links are not shown. | |
