# 10: Purchasing

Needs: accounting set up (script 06; run **Set up accounting** again after upgrading so the
"Goods received not invoiced" account 2170 exists and is mapped under Accounts → Default
accounts), a supplier in USD with an exchange rate USD→KWD (Settings → Exchange rates), a local
supplier in KWD, and a service product (e.g. `INSTALL-HR`).

## Purchase order

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 10.1 | **Purchasing → Purchase orders → New purchase order.** Pick the USD supplier. | Currency switches to USD. If the supplier is a group company, a note "intercompany purchase" shows. | |
| 10.2 | Deliver to = Main warehouse, two stock lines (e.g. 100 × MS-DL-R01-10W at 10, 50 × MS-TR-01-20W at 14.50), **Save draft**. | Totals in USD. Number empty, status **Draft**. | |
| 10.3 | Open it → **Send to supplier**. | Number `KW-PO-<year>-00001`, status **Sent**, exchange rate shown (× 0.3075). | |
| 10.4 | Try a warehouse of another company, quantity 0, a negative cost. | Field errors; nothing saved. | |
| 10.5 | **Cancel order** on a sent order (no receipts). | Status **Cancelled**. After goods are received the button disappears and only **Close order** remains. | |

## Goods receipt (stock in at landed cost)

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 10.6 | From the order → **Receive goods**. | Goods receipts opens a prefilled note: order, warehouse, and every line with the quantity still to receive. | |
| 10.7 | Receive 60 of 100 on line 1, 0 on line 2. Freight `50`, Customs `10` (KWD) → **Receive**. | Number `KW-GRN-…`, status **Received**. Stock levels: Main +60. Landed unit cost = (60 × 10 × 0.3075 + 60) / 60. | |
| 10.8 | Accounts → Journal entries (PI journal). | Debit Inventory (goods + landed), credit **Goods received not invoiced** 2170 (goods), credit **Freight and customs** 5200 (landed). | |
| 10.9 | Open the order. | Status **Partly received**, line shows "60 / 100". | |
| 10.10 | Receive 50 when 40 remain. | Error "receive between 0 and 40". | |
| 10.11 | Receive the remaining 40 and the 50 of line 2. | Order **Received**; **Receive goods** disappears; **Close order** remains. | |

## Supplier bill

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 10.12 | From the order → **Bill**. Click **Add received goods**. | Lines appear per goods receipt (prefixed with the GRN number) with the received quantity and the order cost. | |
| 10.13 | Change the unit cost of line 1 to 10.50, enter the supplier's invoice number → **Post**. | Number `KW-BILL-…`, **Unpaid**, due date = date + supplier terms. Entry: debit 2170 (order value), debit 5200 (price difference), credit Accounts payable (bill total × rate). | |
| 10.14 | New bill, stock product without a receipt line. | Error: "receive it first, then bill the receipt." | |
| 10.15 | New bill for the local supplier, one service line (customs clearance, 275 KWD) → Post. | Entry: debit Freight and customs 5200 (or the product's expense account), credit Accounts payable. | |
| 10.16 | **Cancel bill** on an unpaid bill. | Status **Cancelled**, a reversal entry; the goods receipt's "Billed" quantity goes back down. | |
| 10.17 | Catalog → Products → a service → **Accounting** section. | Income account and Expense account dropdowns; a product with an income account (e.g. 4300 Installation income) credits that account when sold. | |

## Supplier payment

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 10.18 | From the bill → **Pay**. | Payment prefilled with the supplier, the amount due and the bill allocated. From = Bank journal for transfers. | |
| 10.19 | Amount 40 more than the due → **Post**. | "Unallocated: 40.000" before posting. Entry: debit Accounts payable (allocated), debit **Advances to suppliers** 1150 (40), credit Bank. Bill **Paid**. | |
| 10.20 | Allocate more than the amount. | "Over-allocated by …" and **Post** disabled. | |
| 10.21 | Open the supplier's bill editor. | Under the supplier: "Amount due … · Advance 40.000". | |

## Access

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 10.22 | A "Buyer" role (purchasing.order.view/create) at Showroom 1 only. | Sees only orders bought for Showroom 1; can't create one for Showroom 2 (403). The rail shows Purchasing. | |
