# 01: Sign-in and layout

| # | Steps | Expected result | Pass/Fail |
|---|---|---|---|
| 1.1 | Open the web app while signed out. | The sign-in page opens: form on the left, Kuwait Towers photo on the right with a curved edge. The photo slowly settles and the caption fades in. | |
| 1.2 | Click **Sign in** with both fields empty. | The browser asks you to fill in the email. Nothing is sent. | |
| 1.3 | Enter the admin email and a wrong password, then **Sign in**. | Red message: "No active account found with the given credentials". The fields keep their values. | |
| 1.4 | Click the eye icon in the password field. | The password becomes readable. Click again to hide it. | |
| 1.5 | Enter the correct password, then **Sign in**. | The Home page opens, with the black menu rail on the left and the white top bar. | |
| 1.6 | Refresh the page (F5). | You stay signed in. | |
| 1.7 | Open the browser developer tools → Application → Cookies. | `ab_access` and `ab_refresh` are marked **HttpOnly**. No token is visible in Local Storage. | |
| 1.8 | Hover each icon on the left rail. | A black label appears beside each icon (Home, Catalog, Customers & suppliers, Settings). | |
| 1.9 | Click the **Settings** icon. | A white menu slides out beside the rail and the icon turns purple (no white outline). Click outside it: it closes. | |
| 1.10 | Choose **Settings → Companies**. | The Companies page opens with the Settings side menu. "Companies" is highlighted. | |
| 1.11 | Click the round **‹** button on the side menu. | The side menu folds away smoothly. Click **›** to bring it back. | |
| 1.12 | Click your initials (top right). | The menu shows your name and company, then **Switch company**, **Branch**, **Language**, **Log out**. | |
| 1.13 | Switch company to UAE. Open **Settings → Branches**. | Only UAE branches are listed. The page description names the UAE company. | |
| 1.14 | Switch back to Kuwait, then choose **Branch → Showroom 1**. | The menu shows "Kuwait · Showroom 1" under your name. | |
| 1.15 *RTL* | Choose **Language → العربية**. | The whole layout mirrors: rail on the right, Arabic labels, side panels slide in from the left. | |
| 1.16 *RTL* | Open any **Add** form. | The panel opens from the left. Its close button sits outside the panel's right edge. Text is right-aligned. | |
| 1.17 | Switch back to English. Choose **Log out**. | You return to the sign-in page. Pressing the browser Back button does not show ERP data. | |
| 1.18 | On the sign-in page, click **عربي** (top right). | The sign-in page switches to Arabic and mirrors. | |
