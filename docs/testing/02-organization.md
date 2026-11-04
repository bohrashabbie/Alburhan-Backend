# 02: Organization (Settings)

## Companies

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 2.1 | Open **Settings → Companies**. | The table shows Kuwait, UAE and China, each with initials (or its logo), a code under the name, and a country **flag**. Column headings line up with the values. | |
| 2.2 | Click the Kuwait row. | The **Edit company** panel opens: one column of fields, Code greyed out (it can't be changed). | |
| 2.3 | Open the **Country** field and type `sau`. | A searchable list with flags. Saudi Arabia appears. **Enter** picks the first match. **Esc** closes the list but not the panel. | |
| 2.4 | Scroll the country list without searching. | About 250 countries, each with its flag, sorted by name. | |
| 2.5 | Close without saving (✕). | Nothing changed in the table. | |
| 2.6 | Open Kuwait and scroll to **Logo**. Click **Upload logo** and choose a PNG under 2 MB. | The preview shows the logo, and the table shows it in the Kuwait row. | |
| 2.7 | Click **Replace logo** and choose a JPEG. | The new logo replaces the old one. | |
| 2.8 | Try uploading a PDF, a GIF, or an image over 2 MB. | A clear message: "Use a PNG, JPEG or WebP image" or "must be 2 MB or smaller". The logo is unchanged. | |
| 2.9 | Click **Remove logo**. | The initials come back in the table. | |
| 2.10 | Change **Name (Arabic)** and **Save**. Switch the language to Arabic. | The table shows the Arabic name. | |

## Branches

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 2.11 | Open **Branches** (company: Kuwait). | Head office, Projects Department and Showrooms 1–4. | |
| 2.12 | **Add** → code `SR5`, type Showroom, name `Test Showroom` → **Save**. | The panel closes and the branch appears in the list. | |
| 2.13 | **Add** another branch with code `SR5` again. | An error at the top of the form says the company and code must be unique. Nothing is saved. | |
| 2.14 | Search `test`. | Only "Test Showroom" is listed. | |
| 2.15 | Open Test Showroom, untick **Active**, **Save**. | Its status changes to Inactive. | |
| 2.16 | Open Test Showroom → **Delete** → confirm. | It disappears. If it is already used by a warehouse or a user, you see "This is used by other records… Mark it inactive instead." | |

## Warehouses

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 2.17 | Open **Warehouses**. | Main Warehouse, the showroom stock locations and Goods in Transit, with their branch. | |
| 2.18 | **Add** a warehouse, code `TEST`, type Main, Branch left empty → **Save**. | Saved. The Branch column shows "—" (a company-level warehouse). | |
| 2.19 | Edit it and set Branch = Showroom 2 → **Save**. | The Branch column shows Showroom 2. | |

## Exchange rates

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 2.20 | **Add**: from KWD to AED, rate `12.0000`, today's date → **Save**. | The rate is listed with the date. | |
| 2.21 | **Add** the same pair and date again. | An error says the currency pair and date must be unique. Nothing is saved. | |
| 2.22 | **Add** from KWD to KWD. | Error: the two currencies must differ. | |
| 2.23 | **Add** rate `0` or `-1`. | Error: the rate must be greater than zero. | |
