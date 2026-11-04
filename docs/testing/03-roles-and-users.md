# 03: Roles, users and access

These scripts check that people only see and change what their role allows, in their own
company or branch.

## Roles and the permission grid

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 3.1 | Open **Settings → Roles**. | "Administrator" has the **System** badge. Its permission count equals the total number of permissions. | |
| 3.2 | Open Administrator. | A grey note says it is a system role. Every tick is set and greyed out. **Save** is refused with "System roles cannot be changed". | |
| 3.3 | **Add** a role: code `salesperson`, name `Salesperson`. | The wide panel opens with the permission grid: one table per module, with View / Create / Edit / Delete columns. No text overlaps. | |
| 3.4 | Tick the row box for **Customers**. | View, Create, Edit and Delete for Customers are all ticked. | |
| 3.5 | Untick **Delete** on Customers. | The Customers row box shows a dash (partly selected). | |
| 3.6 | Tick the module box for **Catalog**, then untick it. | All Catalog permissions turn on, then off. | |
| 3.7 | Give Salesperson: Customers View/Create/Edit, Products View, Price lists View. **Save**. | The Permissions column shows the right count (5). | |
| 3.8 | **Add** a role `viewer` with only Organization → Warehouses → View. **Save**. | Saved. | |

## Users and role assignments

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 3.9 | **Users → Add**: first name `Sara`, email `sara.test@example.com`, username `sara`, Home company Kuwait, password `Test-12345` → **Save**. | The user appears in the list. | |
| 3.10 | Open Sara → **Role assignments** → Role = Salesperson, Branch = Showroom 1 → **Add**. | Listed as "Salesperson · Showroom 1". | |
| 3.11 | Add the same role and branch again. | Error: "This user already has this role there". | |
| 3.12 | Create user `Omar` (`omar.test@example.com`, Home company Kuwait) and give him **Viewer** for **All branches**. | Listed as "Viewer · All branches". | |

## Access limits (sign in as each test user)

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 3.13 | Log out. Sign in as **Sara**. | The rail shows Home, Catalog and Customers & suppliers. **Settings is not shown.** | |
| 3.14 | Sara: open **Customers** → **Add** a customer → **Save**. | Saved with an automatic code like `KW-CUS-00001`. | |
| 3.15 | Sara: open that customer. | No **Delete** button (Salesperson has no delete permission). | |
| 3.16 | Sara: open **Catalog → Products**. | The list shows, but there is no **Add** button. | |
| 3.17 | Sara: click the avatar. | Only Kuwait is offered (no UAE or China), and the branch is Showroom 1. | |
| 3.18 | Sara: type `/settings/users` in the address bar. | Either nothing is listed or "You do not have permission". No user data shows. | |
| 3.19 | Log out. Sign in as **Omar**. Open **Settings**. | Only **Warehouses** appears in the side menu, and the list has no **Add** button. | |
| 3.20 | As admin, remove Sara's role assignment. Sign in as Sara again. | "Your account has no company access yet." She sees no data. | |
| 3.21 | As admin, untick **Active** on Omar and **Save**. Try to sign in as Omar. | Sign-in fails. | |
| 3.22 | Clean up: deactivate the test users and delete the test roles. | Done. | |
