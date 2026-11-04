# 07: Inventory

Needs: accounting set up for the company (script 06), at least two warehouses (e.g. Main Warehouse
and Showroom 1 Stock) and a couple of stock products (script 04).

## Opening stock and adjustments

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 7.1 | **Inventory → Adjustments → New adjustment.** Warehouse = Main, Reason = Opening stock. Add a line: product MS-DL-R01-10W, Available, quantity `100`, unit cost `2.500` → **Post**. | Status **Posted**, number `KW-ADJ-<year>-00001`. | |
| 7.2 | Open **Stock levels**. | The product shows Available 100, Free 100, average cost 2.500, value 250.000. The stock value card shows 250.000 KWD. | |
| 7.3 | Open **Accounts → Journal entries**. | A posted entry "Stock adjustment KW-ADJ-…": debit Inventory 250.000, credit Inventory adjustments 250.000. | |
| 7.4 | Post another opening adjustment: same product, 100 at `3.500`. | Stock levels show 200 at average cost 3.000 (weighted). | |
| 7.5 | New adjustment, Reason = Correction, quantity `-250`. **Post**. | Error: "Not enough … 200 on hand". Nothing changes. | |
| 7.6 | New adjustment, Reason = Status change: line 1 Available `-5`, line 2 Damaged `+5` → **Post**. | Stock levels: Available 195, Damaged 5 (amber). No journal entry is created (value unchanged). | |
| 7.7 | Status change with only one line (`+5` Damaged). | Error: a status change must add and remove the same quantity. | |
| 7.8 | A quantity of `2.5` for a product counted in pieces. | Error: counted in whole PCS. A product in metres accepts 2.5. | |
| 7.9 | Open a posted adjustment. | Read-only view with the journal entry number. No Save or Delete. | |

## Transfers (Transfer Out → In Transit → Transfer In)

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 7.10 | **Transfers → New transfer.** From Main, To Showroom 1 Stock, product MS-DL-R01-10W quantity `30` → **Send**. | Status **In transit**, number `KW-TRF-…`. | |
| 7.11 | Open **Stock levels**. | Main: Available 165. Showroom 1 Stock: In transit 30, Available 0. The stock value is unchanged. | |
| 7.12 | Open the transfer. Receive now = `20` → **Receive**. | Status **Partly received**. Showroom 1: Available 20, In transit 10. | |
| 7.13 | Receive the remaining `10`. | Status **Received**, received date filled. In transit 0. | |
| 7.14 | Try to receive `11` when only 10 remain. | Error: receive between 0 and 10. | |
| 7.15 | New transfer with From = To the same warehouse. | The destination list doesn't offer the source warehouse. | |
| 7.16 | **Movements** page, filter Type = Transfer out. | The two −30 / +30 lines of the transfer, with its number. | |

## Reservations

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 7.17 | **Reservations → Add**: Main, product MS-DL-R01-10W, quantity `150`, reference `PRJ-TEST`. | Saved as **Active**. Stock levels: Reserved 150, Free 15. | |
| 7.18 | Add another reservation of `20` for the same product in Main. | Error: "Only 15 … free in Main Warehouse". | |
| 7.19 | New transfer from Main of `20` → **Send**. | Error: only 15 free (the rest is reserved). | |
| 7.20 | Open the reservation → **Release**. | Status **Released**. Free is back to 165. | |

## Stock counts

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 7.21 | **Stock counts → New stock count**, warehouse Main → **Fill from stock**. | Every product with available stock is listed with its expected quantity, counted = expected. | |
| 7.22 | Change the counted quantity of MS-DL-R01-10W to `160` (expected 165). | The Difference column shows −5 in red. | |
| 7.23 | **Post**. | Number `KW-CNT-…`. Stock levels show 160. A journal entry credits Inventory by 5 × average cost. | |
| 7.24 | Post a count with no differences. | Status Posted, no journal entry. | |

## Planning

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 7.25 | **Reorder rules → Add**: Main, MS-DL-R01-10W, min `500`, reorder `200`. | Saved. | |
| 7.26 | Open **Reorder alerts**. | The product is listed: free (red), min 500, suggested 340 (= max(200, 500 − 160)). | |
| 7.27 | Change the rule's min to `10`. | The product leaves the alerts list. | |
| 7.28 | Open **Slow-moving stock**, 30 days. | Products with no outgoing movement in 30 days, with value tied up. The transferred product is **not** listed (it went out). | |

## Access

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 7.29 | Give a role "Store keeper" View stock, Adjustments View/Create/Post, Transfers View/Receive; assign it to a test user at **Showroom 1** only. | | |
| 7.30 | Sign in as that user → Stock levels. | Only Showroom 1 Stock is listed. The warehouse filter offers only Showroom 1 Stock. | |
| 7.31 | That user creates an adjustment for Main Warehouse. | Main isn't offered in the Warehouse list; the API refuses it if forced. | |
| 7.32 | That user opens a transfer to Showroom 1 and clicks Receive. | Allowed. A transfer *from* Showroom 1 can't be sent (no Send permission). | |
