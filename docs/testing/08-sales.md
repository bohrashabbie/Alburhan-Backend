# 08: Showroom sales

Needs: accounting set up (script 06), stock in a warehouse (script 07, e.g. 50 × MS-DL-R01-10W in
Main Warehouse), a customer (script 05) and a price list with a price for that product (script 04).

## Quotation → invoice

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 8.1 | **Sales → Quotations → New quotation.** Pick the customer (type part of the name). | The customer's name and code appear; **Price list** shows the customer's list or the company default. | |
| 8.2 | In the first line, search the product and pick it. | The unit price fills in from the price list. Quantity 1, total shown on the right. | |
| 8.3 | Quantity `10`, discount `10`. | The line total and the footer (Subtotal, VAT, Grand total) update as you type. | |
| 8.4 | **Save draft**, then open it and click **Send**. | Status **Sent**, number `KW-QT-<year>-00001`, Valid until = date + 30 days. | |
| 8.5 | Set a discount above the limit in Sales settings (e.g. 50% when the limit is 15%). | Error: discounts above 15% need approval. A user with "Give discounts above the company limit" can save it. | |
| 8.6 | Open the sent quotation → **Convert to invoice**. | A **draft invoice** opens with the same lines. The quotation shows **Accepted** and its invoice number once posted. | |

## Invoice

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 8.7 | In the draft invoice, choose **Deliver from** = Main Warehouse → **Post**. | Status **Unpaid**, number `KW-INV-…`, Amount due = total, Delivery = **Not delivered**. | |
| 8.8 | **Accounts → Journal entries.** | A posted entry in the SI journal: debit Accounts receivable (total), credit Showroom sales (subtotal) and VAT payable (if any). | |
| 8.9 | Open the posted invoice. | Read-only: four tiles (Total, Paid, Credited, Amount due), delivery status, and the action buttons **Deliver**, **Record payment**, **Credit note**. No Save. | |
| 8.10 | Give the customer a credit limit of 100 and post an invoice that would exceed it. | Error: "Credit limit exceeded …". | |
| 8.11 | **Cancel invoice** on an unpaid, undelivered invoice. | Status **Cancelled**; a reversal entry appears in the journal. The button is hidden once goods are delivered or money is allocated. | |

## Delivery note

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 8.12 | From the invoice → **Deliver**. | The Delivery notes page opens with a new note prefilled: invoice, warehouse, and every stock line with the remaining quantity. Services are not listed. | |
| 8.13 | Change "Deliver now" to `6` of 10, fill **Received by** → **Deliver**. | Number `KW-DN-…`, status **Delivered**. Stock levels: Main −6. A journal entry debits Cost of goods sold and credits Inventory for 6 × average cost. | |
| 8.14 | Open the invoice again. | Delivery = **Partly delivered**; the lines show "6 / 10". | |
| 8.15 | Deliver the remaining 4. | Delivery = **Delivered**; the **Deliver** button disappears. | |
| 8.16 | Try to deliver 5 when only 4 remain. | Error: deliver between 0 and 4. | |
| 8.17 | Deliver from a warehouse with no stock. | Error: "Only 0 … free in …". | |

## Receipt (payment)

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 8.18 | From the invoice → **Record payment**. | A new receipt opens with the customer, the amount due, and that invoice allocated in full. | |
| 8.19 | Method **KNET**. | **Into** switches to the Bank journal (Cash stays on the cash journal). | |
| 8.20 | Change the amount to more than the due (e.g. +40). | "Unallocated: 40.000 KWD" shows; **Post** stays enabled. Post it. | |
| 8.21 | Check the invoice and the journal. | Invoice **Paid**. Entry: debit Bank (amount), credit Accounts receivable (allocated), credit Customer advances (40). | |
| 8.22 | Allocate more than the amount received. | "Over-allocated by …" in red and **Post** disabled. | |
| 8.23 | New receipt for a customer with two open invoices: click the **Due** figure of one. | That invoice is allocated the remaining amount automatically. | |
| 8.24 | Choose a General journal (if offered) or an inactive one. | Only cash and bank journals are offered. | |

## Credit note (return)

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 8.25 | From a delivered invoice → **Credit note**. Credit 1 of the product, **Return to stock** ticked, warehouse Main → **Post**. | Number `KW-CN-…`. Stock levels: Main **Returned** 1. Invoice: Credited = line value, Amount due reduced. | |
| 8.26 | Journal entry of the credit note. | Debit Sales returns (net) and VAT payable (tax); credit Accounts receivable (total); debit Inventory / credit Cost of goods sold for the returned stock value. | |
| 8.27 | Credit more than was invoiced. | Error: credit between 0 and … | |
| 8.28 | Tick Return to stock for goods not yet delivered. | Error: only delivered goods can be returned to stock. | |

## Settings and access

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 8.29 | **Sales settings**: set Default VAT % to 5, save. New quotation lines default to 5% VAT. | | |
| 8.30 | A "Showroom salesperson" role (quotation/invoice view+create, receipt create, no post) assigned at Showroom 1. | They see only Showroom 1 documents; **Post** is hidden; the Branch field defaults to Showroom 1. | |
| 8.31 | *RTL*: switch to Arabic and open a new invoice. | Labels in Arabic, numbers right-aligned, the line table mirrored. | |
