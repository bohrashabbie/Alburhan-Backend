# Live demo script: 8 acceptance scenarios

Each scenario can be run live, and a finished sample of it already exists (rehearsed on
22 Nov 2026, all 19 checks passed). If time is short, open the sample instead of creating it.

The demo item throughout is **MS-TR-01-20W · Magnetic track spotlight 20W**.

## Before the meeting

- [ ] Update the server so the live site has the demo features (item card, printing, XBRL check):
      `cd ~/Alburhan-Backend && git pull && git -C ../Alburhan-Frontend-System pull && cd deploy && sudo docker compose -f docker-compose.nginx.yml up -d --build`
- [ ] Settings → Companies → Al-Burhan Regional Kuwait: add the address, phone, logo and
      **commercial registration number** (printed on documents; the XBRL check warns without it).
- [ ] Decide what to do with journal entry **KW-CS-2026-00008**: a till shift on 16 Nov was closed
      with a count about 9,972 KWD too high (a typing slip), booked as "cash over". It inflates profit
      on the reports in scenario 7. Accounts → Journal entries → open it → **Reverse**.
- [ ] Have two browser windows: the administrator, and **Yousef** (salesperson, Showrooms 1–3,
      training password) for scenario 8.

## 1 · One item across branches

**Who:** administrator. **Where:** press **Ctrl+K**, type `MS-TR-01-20W`, open the product.

Show, on one screen: photo, code, unit (PCS), price (12.000 from the Kuwait retail list), and for
every location **on hand / reserved / free / on order / in transit**:

| Location | On hand | Reserved | Free | In transit |
|---|---|---|---|---|
| Main Warehouse | 10 | 10 (project) | 0 | |
| Showroom 1 | 12 | 4 (project) | 8 | |
| Showroom 2 | 3 | | 3 | 2 |

**Coming in** lists the open purchase orders with expected dates and the transfer on the way.
**Full history** opens every movement of the item.

## 2 · Showroom sale

**Who:** administrator (or Yousef). **Where:** Sales → Quotations → **New**.

1. Customer *Fahad Al-Mutawa*, branch Showroom 1. Lines: MS-DL-R01-18W × 10 at 6.750 with
   **10 %** discount; MS-GL-GARDEN × 2 at 14.000. **Send**.
2. **Convert to invoice** → warehouse *Showroom 1 Stock* → **Post**.
3. **Deliver** → **Post**.
4. Show the item card of MS-DL-R01-18W: Showroom 1 is 10 lower; Main is unchanged.
5. **Print / PDF** on the quotation, invoice and delivery note (A4; "Save as PDF" in the print dialog).

**Sample:** KW-QT-2026-00003 → KW-INV-2026-00012 → KW-DN-2026-00009 (88.750 KWD).

## 3 · Receive part of a purchase order

**Where:** Purchasing → Purchase orders → **New**.

1. Supplier *MS Lighting (China factory)*, deliver to Main Warehouse, **expected date**,
   MS-TR-01-20W × 40 → **Send to supplier**.
2. **Receive goods** → receive **25** → **Receive now**.
3. The order shows *Partly received*, each line "25 / 40 · 15 to come", and the expected date.
   The item card lists the 15 under **Coming in** with that date.

**Sample:** KW-PO-2026-00003 with goods receipt KW-GRN-2026-00004.

## 4 · Transfer five, receive three

**Where:** Inventory → Transfers → **New**.

1. From Main Warehouse to Showroom 2 Stock, MS-TR-01-20W × 5 → **Send**.
2. **Receive** 3 → status *Partly received*.
3. Item card: Showroom 2 has **3 on hand** and **2 in transit**.

**Sample:** KW-TRF-2026-00003.

## 5 · Project order

**Where:** Projects → **New project**.

1. Customer *Al-Khaleej Contracting Co.*, BOQ: MS-TR-01-20W, MS-DL-R01-18W, INSTALL-HR with
   prices and costs. **Quotation** → open it → **Print / PDF** (shows product photos).
2. **Won** → **Reserve stock** from Main Warehouse. If Main doesn't have enough, the screen says
   what is still to reserve: choose **Showroom 1** and **Reserve stock** again.
3. Down payment: Sales → Receipts → **New**, the customer, bank transfer, 360.000, no invoice →
   **Post**: held as a customer advance.
4. **Materials to site**: send part (10 spotlights, 12 downlights) → **Issue to site**.
5. **Progress invoice** for the same quantities → **Post**.
6. Back on the project: invoiced, materials cost, margin; the item card shows the reservation
   reduced by what went to site.

**Sample:** project KW-PRJ-2026-00004 · quotation KW-QT-2026-00002 · down payment
KW-RCT-2026-00010 · issue KW-MI-2026-00002 · invoice KW-INV-2026-00011.

## 6 · Stocktaking

**Where:** Inventory → Stock counts → **New**.

1. Warehouse *Showroom 1 Stock*, add MS-DL-R01-10W: the system quantity shows; enter the counted
   quantity (one less).
2. **Post** (this is the approval: only roles with "post stock counts" can do it).
3. Stock goes down by one; the item's **Full history** shows the count by its number.

**Sample:** KW-CNT-2026-00001 (18 → 17).

## 7 · Accounting and XBRL

**Where:** Accounts.

1. **Journal entries**: the sale (SI) and the goods receipt (PI) each have their entry; open one
   to show debits and credits.
2. **Profit & loss** and **Balance sheet** for the year.
3. **Filings & e-invoicing** → **Check the file**: the checks run on the generated XBRL
   (structure, IFRS taxonomy reference, entity, periods, units, required figures, the balance
   sheet balances, profit adds up) → **Download XBRL**.
4. How it is validated: these checks catch structure and arithmetic. The full taxonomy validation
   (every figure's data type and the Ministry's filing rules) runs when the file is uploaded to the
   MOCI / CMA portal, and can be run beforehand with a free validator such as Arelle.

## 8 · Permissions and discount control

**Who:** Yousef (salesperson). **Where:** Sales → Quotations → **New**.

1. Give **15 %** on a line → Save: refused, "the most you can give is 10 %… needs a manager's
   approval". **10 %** saves.
2. The branch list offers only Showrooms 1–3; the quotation list shows only his showrooms.
3. In the administrator window, the same 15 % saves: the manager approves by setting it.

The 10 % limit is Sales → Sales settings → *Max discount without approval*. Who may exceed it is
the role permission "Give discounts above the company limit".
