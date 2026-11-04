# 05: Customers and suppliers

## Customers

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 5.1 | **Customers & suppliers → Customers → Add**. | The wide panel has **General**, **Registration**, **Sales terms** and **Contacts** sections, neatly aligned. | |
| 5.2 | Name `Test Customer 01`, Type Individual, Mobile `99000001`, leave **Code** empty → **Save**. | Saved with code `KW-CUS-00001` (the next free number). | |
| 5.3 | Add `Test Customer 02` the same way. | Code `KW-CUS-00002`. | |
| 5.4 | Switch company to UAE and add a customer. | Code `AE-CUS-00001`: each company has its own numbering. | |
| 5.5 | Back in Kuwait, add a customer with your own code `VIP-1`, then another with `vip-1`. | The first saves as `VIP-1`. The second fails: "This code is already used." | |
| 5.6 | Open Test Customer 01 → **Add contact** twice: Khalid (Site engineer) and Mona. | Two contact cards. The first is marked **Primary contact**. | |
| 5.7 | Click **Primary contact** on Mona. | Mona becomes primary and Khalid is no longer primary. Only one can be primary. | |
| 5.8 | **Save**, then reopen. | Both contacts are kept. The list shows the primary contact's name under the customer. | |
| 5.9 | Remove Khalid (bin icon) → **Save** → reopen. | Only Mona remains. | |
| 5.10 | Set **Price list** = Retail, Credit limit `500`, Payment terms `30` → **Save**. | The list shows a credit limit of "KWD 500.000". | |
| 5.11 | Enter a credit limit of `-1`. | Error under Credit limit. | |
| 5.12 | Search by mobile `99000001`, then by contact name `mona`. | The customer is found, listed once. | |
| 5.13 | Filter **Type = Contractor**. | Only contractor customers. | |
| 5.14 *RTL* | Switch to Arabic and open a customer. | Fields and contacts are right-aligned. Flags and phone numbers display correctly. | |

## Suppliers

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 5.15 | **Suppliers → Add**: name `MS Lighting Jiangmen`, Country China (search "chi", flag shown), Invoice currency CNY, Group company = MS Lighting China, Lead time `45` → **Save**. | Code `KW-SUP-00001`. The list shows a "Group · CN" badge, the China flag, CNY and "45 d". | |
| 5.16 | Add a supplier whose Group company is the current company itself. | That company isn't offered in the list (a company can't supply itself). | |
| 5.17 | Add a supplier with a website without `https://`. | Error under Website: enter a valid URL. | |
| 5.18 | Add two contacts and save. Reopen. | The contacts are kept. | |

## Access (with the Sara user from script 03)

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 5.19 | Sign in as Sara (Salesperson at Showroom 1). Open Customers. | She sees Kuwait customers and can add them, even though her role is for one branch. | |
| 5.20 | Sara: open Suppliers. | The Suppliers link isn't shown (no supplier permission). | |
| 5.21 | Clean up: deactivate the test customers and suppliers. | Done. | |
