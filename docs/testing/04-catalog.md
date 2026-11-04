# 04: Catalog (products and prices)

## Setup lists

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 4.1 | Open **Catalog → Product families**. | 17 families (Recessed Down Light … Flood Light, Drivers & Accessories, Installation Services), each with a group and a product count. | |
| 4.2 | Filter **Group = Outdoor**. | Only Wall, Lawn, Street and Flood Light. | |
| 4.3 | Edit "Recessed Down Light" and set **Inside family** to itself or to one of its own sub-families. | Error: a category cannot be inside itself. | |
| 4.4 | Open **Brands**. | MS Lighting (Own brand, China flag), Iluminia, INOVARA, Metsan, Kosla. | |
| 4.4a | Open a family → **Photo** → **Upload photo** (PNG) or **Take a photo** (camera). | The photo shows in the family list and in the family picker when adding a product. **Remove photo** clears it. | |
| 4.5 | Open **Units**. | PCS, SET, BOX, ROLL, M (fractions allowed), HR (fractions allowed), LS. | |

## Products

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 4.6 | **Products → Add**. | The wide panel opens with **General** and **Lighting specification** sections, in two aligned columns. Unit defaults to PCS. | |
| 4.7 | Fill SKU `ms-dl-r01-10w`, Name `Recessed Downlight 10W`, Family Recessed Down Light, Brand MS Lighting, Power `10`, Luminous flux `950`, CCT `3000K`, CRI `90`, Beam `24°`, IP `ip44`, Dimming DALI → **Save**. | Saved. The list shows SKU `MS-DL-R01-10W` (upper case) and under the name "10W · 950lm · 3000K · CRI 90 · 24° · IP44". | |
| 4.8 | Add another product with SKU `MS-DL-R01-10W`. | Error under SKU: already used. | |
| 4.9 | Add a product with IP `IP99`, CRI `120` or Power `-5`. | A clear error under that field. | |
| 4.10 | Give two products the same barcode. | Error on the second: "Another product already has this barcode." Products without a barcode save fine. | |
| 4.11 | Search `r01`, then filter by Family and Brand. | The list narrows correctly. | |
| 4.12 | Open the product. | Every value you entered is shown, in the same sections and order. | |
| 4.12a | In the product panel → **Photo** → **Take a photo**. | The browser asks for camera permission, a live preview opens, **Capture** saves a square photo. It shows next to the product in the list. | |
| 4.12b | Click **Family** in the product form. | A searchable grid of families with their photos opens; typing filters it; Enter/click picks one. | |

## Price lists

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 4.13 | **Price lists → Add**: name `Retail`, code `retail`, currency KWD, **Default for this company** ticked → **Save**. | Listed with a **Default** badge. "Valid: Always". | |
| 4.14 | Add a second KWD list `Trade` that is also ticked as Default. | Error: the company already has a default price list. | |
| 4.15 | Open Retail → **Prices** → search `r01` in Product, pick it, Min. quantity `1`, Price `4.750` → **Add**. | The row appears with the product SKU and name. | |
| 4.16 | Add the same product with Min. quantity `10`, price `4.250`. | Two rows: a price break for 10 units or more. | |
| 4.17 | Try price `4.7505` (4 decimals) or a negative price. | Error: KWD allows 3 decimal places / cannot be negative. | |
| 4.18 | Try Min. quantity `2.5` for a PCS product. | Error: PCS is counted in whole units. | |
| 4.19 | Change the price in a row → click the ✓ button. | The new price is saved. | |
| 4.20 | Try to change the list's currency to USD while it has prices. | Error: remove the prices before changing the currency. | |
| 4.21 | Try to **Delete** the product used in this price list. | "This is used by other records… Mark it inactive instead." The product stays. | |
| 4.22 | Switch company to UAE and open Price lists. | The Kuwait Retail list is **not** shown. A UAE list in AED allows only 2 decimals. | |
