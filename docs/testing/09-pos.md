# 09: Point of sale

Needs: accounting set up (script 06), products with a price in the company's **default price
list** (script 04), stock in the showroom warehouse (script 07) and a "Walk-in customer" (script 05).
The POS reuses Sales underneath: every till receipt is a posted invoice + delivery note + receipt,
so scripts 06–08 show the same numbers from the other side.

## Register

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 9.1 | **POS → Registers → Add.** Branch Showroom 1, code `SR1-T1`, name "Showroom 1 till", Sells from = SR1 warehouse, price list empty (company default), walk-in customer picked, Cash journal = CS, Card journal = BK, Cash over/short account = 6900 Other expenses, a receipt header and footer. | Saved. The table shows the branch, warehouse, "Default" for the price list and "—" in the Shift column. | |
| 9.2 | Try a General journal as the cash journal. | Error under Cash journal: "Choose a cash or bank journal." Only cash journals are offered in the dropdown. | |
| 9.3 | Pick a walk-in customer from another company (switch company, then back). | Error: "Choose one from the same company." | |

## Shift

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 9.4 | **POS → Sell.** | The bar shows the register, "No shift open", and an **Open shift** button. | |
| 9.5 | **Open shift**, opening float `50`. | Number `KW-SHF-<year>-00001`, "Opened hh:mm · your name", Sales count 0, Expected cash in drawer 50.000. The product grid appears. | |
| 9.6 | **POS → Shifts → Open shift** for the same register. | Error: "This register already has an open shift." The register is greyed in the list. | |

## Selling

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 9.7 | Tap a product tile. Tap it again. | A purple **2** badge on the tile; the cart shows the line with quantity 2 and the line total. Subtotal and Total update. | |
| 9.8 | Use the **−** button down to 0. | The line disappears and the badge goes. | |
| 9.9 | Type a SKU (e.g. `MS-DL-R01-10W`) in **Scan or search** and press Enter. | The product is added and the box clears (a barcode scanner does the same). | |
| 9.10 | Tap a category chip. | Only that family's products show; **All** brings everything back. | |
| 9.11 | Add a product with 0 stock (set it to 0 in script 07). | The tile shows **Out of stock** and can't be tapped; ≤ 3 shows an amber count. | |
| 9.12 | Enter `25` in **Disc. %** on a line when the limit in Sales settings is 15. | On Charge → Complete: error "discounts above 15% need a manager". A user with "Give discounts above the company limit" can. | |
| 9.13 | **Change** the customer, pick a named customer, then **×** to clear. | The cart header shows the name and code, then goes back to the walk-in customer. | |

## Payment

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 9.14 | Cart of 16.500 → **Charge**. | The Charge modal: Total in a black band, Cash selected, Amount tendered prefilled with 16.500, quick buttons **Exact**, **KWD 17.000**, **KWD 20.000**. | |
| 9.15 | Tap **KWD 20.000** → **Complete sale**. | "Sale complete" with the receipt slip and **Change KWD 3.500**. **Print** opens the browser print dialog with only the 80 mm slip. **New sale** clears the cart. | |
| 9.16 | Sales → Invoices and Accounts → Journal entries. | Invoice `KW-INV-…` **Paid**, delivered. Receipt `KW-RCT-…` for 16.500 into the Cash journal (not 20). Stock: SR1 −1 of each. Entries: SI (receivable / sales), JV (COGS / inventory), CS (cash / receivable). | |
| 9.17 | New cart → **KNET**, amount more than the total. | Red: "KNET / card can't be more than the total." **Complete sale** is disabled. | |
| 9.18 | **Add payment**: KNET 10 with reference + Cash for the rest. | Paid = total, Change 0.000; two receipts, the KNET one in the Bank journal with the reference. | |
| 9.19 | Cash less than the total. | Red **Remaining** amount and the button is disabled. | |
| 9.20 | Sell 25 of a product that has 20. | Error on the lines: "Only 20 … free in SR1". Nothing is saved (no invoice, no stock move). | |

## Void and records

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 9.21 | **POS → Sales** → open the sale → **Void**, reason "Wrong item" → **Void sale**. | Status **Voided**, credit note `KW-CN-…`, a negative payment line for each method. Stock: SR1 **Returned** +qty. The invoice shows Credited = total and Paid 0. A refund entry debits receivable and credits cash/bank. | |
| 9.22 | Void it again. | Error: "This sale is already voided." | |
| 9.23 | The shift bar on the till. | Net sales exclude the voided sale; Expected cash drops by the cash refunded. | |

## Close

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 9.24 | **Close shift** on the till. Cash counted = expected − 0.500. | "Short by KWD 0.500" in red before closing. After: status **Closed**, the shift page shows counted, expected, difference, a journal entry debiting 6900 and crediting cash 1110. | |
| 9.25 | Close with the exact amount (new shift). | "Drawer balances."; no journal entry. | |
| 9.26 | Try to sell or void on the closed shift (old tab). | Error: "Open a shift first." / "The shift is closed. Use a credit note under Sales…". | |
| 9.27 | A "Cashier" role (pos.sale.view/create, pos.shift.view/open/close) at Showroom 1. | Sees only Showroom 1 registers and shifts; can't open the Showroom 2 register (403); the rail shows POS. | |
| 9.28 | *RTL*: Arabic and reload the till. | Cart on the start (left) side, Arabic product names, Arabic receipt labels. | |
