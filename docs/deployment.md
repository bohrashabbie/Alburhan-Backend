# Deploying Al-Burhan ERP

One EC2 host runs everything except the database and file storage:

```
Internet ──443──▶ Caddy (HTTPS, both domains)
                   ├─ erp.<domain>  ──▶ web (Next.js)  ──▶ api (Django, gunicorn) ──▶ PostgreSQL (external)
                   │      /admin, /static ──▶ api                                 ├─▶ Redis (cache, on the host)
                   └─ pos.<domain>  ──▶ web (same app; "pos." hosts show only the till)  └─▶ S3 (files), SMTP (emails)
```

Browsers only ever talk to Next.js. The API is not published; Next.js forwards each request with
the user's token. Files live in a private S3 bucket and are shown through short-lived signed links.

Files in this repo: `Dockerfile`, `docker/start.sh` (migrate, then gunicorn), `deploy/docker-compose.yml`,
`deploy/Caddyfile`, `deploy/.env.example`, `deploy/api.env.example`, `deploy/backup.sh`.
The frontend repo has its own `Dockerfile`.

## 1. Before the first deployment

| What | Detail |
|---|---|
| **EC2 instance** | Ubuntu 24.04, at least 2 vCPU / 4 GB (t3.medium). In the **same region as the database**: every screen makes many queries, and the dev laptop's ~320 ms to the database is why it feels slow locally. |
| **Security group** | Inbound 80 and 443 from anywhere; 22 from your office IP only. Nothing else. |
| **Database** | PostgreSQL 15+ (RDS or the current server). A dedicated user `alburhan_app` that owns `alburhan_erp`; SSL on; allow 5432 only from the EC2 instance's security group. |
| **DNS** | Two A records to the instance's Elastic IP: `erp.<domain>` and `pos.<domain>`. The POS host **must start with `pos.`**. |
| **S3** | A **private** bucket (e.g. `alburhan-erp-private`, Block Public Access on) and an IAM user limited to `s3:GetObject/PutObject/DeleteObject/ListBucket` on it. |
| **Email** | Amazon SES (or any SMTP) with the sending domain verified; SMTP credentials for `api.env`. Without it, leave approvals can't be emailed. |
| **Docker** | `curl -fsSL https://get.docker.com \| sh` then `sudo usermod -aG docker ubuntu` (log out and back in). |

## 2. First deployment

```sh
sudo mkdir -p /opt/alburhan && sudo chown ubuntu /opt/alburhan && cd /opt/alburhan
# Deploy from main once feature/erp-foundation is merged (until then use -b feature/erp-foundation).
git clone -b main https://github.com/bohrashabbie/Alburhan-Backend.git
git clone -b main https://github.com/bohrashabbie/Alburhan-Frontend-System.git

cd Alburhan-Backend/deploy
cp .env.example .env            # the two domains and the certificate email
cp api.env.example api.env      # secrets: fill every empty value
chmod 600 api.env

docker compose up -d --build    # builds both images; the api container runs migrations first
docker compose ps               # api and web should become "healthy"
docker compose logs -f caddy    # watch the certificates being issued
```

Then, once:

```sh
# The main administrator (an existing database already has one)
docker compose exec api python manage.py createsuperuser
# A new database only: chart of accounts, journals, periods for each country company
docker compose exec api python manage.py setup_accounting --company KW --year 2027
```

Open `https://erp.<domain>`, sign in, and work through the go-live checklist below.

## 2b. If nginx already runs on the server

When the host's nginx already serves other sites on 80/443, skip Caddy: run the ERP on private
local ports (web 127.0.0.1:3400, api 127.0.0.1:8400) and let that nginx route the two domains.

```sh
cd ~/Alburhan-Backend/deploy
cp .env.example .env && cp api.env.example api.env && chmod 600 api.env   # fill api.env as above
docker compose -f docker-compose.nginx.yml up -d --build

sudo cp nginx/alburhan-erp-proxy.conf /etc/nginx/snippets/
sudo cp nginx/alburhan-erp.conf /etc/nginx/sites-available/alburhan-erp   # check the server_name lines
sudo ln -s /etc/nginx/sites-available/alburhan-erp /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d erp.<domain> -d pos.<domain>
```

Updates then use `docker compose -f docker-compose.nginx.yml up -d --build`. The other sites'
nginx files are not touched.

## 3. Updating

```sh
cd /opt/alburhan/Alburhan-Backend && git pull
cd ../Alburhan-Frontend-System && git pull
cd ../Alburhan-Backend/deploy && docker compose up -d --build
```

Migrations run automatically when the api container starts. A deploy takes the site down for the
few seconds the containers restart; do it outside shop hours (POS shifts closed).

**Rollback**: `git checkout <previous tag>` in both repos and `docker compose up -d --build`. If the
release had a migration, restore the backup taken before it (below) — migrations are not reversed
automatically.

## 4. Backups

`deploy/backup.sh` writes a compressed `pg_dump` to `/var/backups/alburhan` (14 days kept) and, if
`BACKUP_S3_URI` is set, copies it to S3. Schedule it nightly:

```sh
crontab -e
30 1 * * * BACKUP_S3_URI=s3://alburhan-erp-backups /opt/alburhan/Alburhan-Backend/deploy/backup.sh >> /var/log/alburhan-backup.log 2>&1
```

Restore into an empty database: `pg_restore --no-owner -d "$DATABASE_URL" alburhan_erp_<stamp>.dump`.
RDS users can also rely on its automated snapshots. Test a restore once before go-live.

## 5. Logs and health

- `docker compose logs -f api` (requests and errors), `web`, `caddy`.
- `https://erp.<domain>/admin/` is Django's admin for main administrators.
- The api and web containers have health checks; `docker compose ps` shows them. Restart policy
  is `unless-stopped`, so they come back after a reboot.

## 6. Go-live checklist

**Security**
- [ ] New `DJANGO_SECRET_KEY` generated on the server (never reuse the development one).
- [ ] Database password **rotated** (the development one has been shared); `api.env` updated.
- [ ] Main administrator password changed from the development one; extra main admins only for people who need every country.
- [ ] S3: files moved to the **private** bucket; the public development bucket no longer used.
- [ ] `api.env` is `chmod 600` and not in git; port 22 limited to the office IP.

**Data**
- [ ] Decide: start from a **fresh database** (recommended) or keep the current one. The current one holds training data — users `*@training.alburhan.com` (all sharing one known training password), demo sales, POS shifts, a project, payroll for November 2026. If you keep it, deactivate those users and reverse or delete the demo documents.
- [ ] Each country company: chart of accounts and accounting **periods for the coming year**, default accounts (Accounts → Default accounts, including Project sales), journals with their bank accounts.
- [ ] Opening balances posted (bank, stock, customer and supplier balances) with a journal entry dated the cut-over day.
- [ ] Products, price lists, warehouses and POS registers in place; one test sale and a void on each register.

**HR and email**
- [ ] SMTP working: ask for leave as a test user and check the manager receives the email, and that its link opens `https://erp.<domain>/leave-decision/…` (that is `FRONTEND_URL`).
- [ ] HR settings per country: HR email, weekend days, pay days per month, salary accounts.
- [ ] Employees linked to their logins, with managers who have an email address.

**Access**
- [ ] Every person has a country and access by store (Settings → Users → Access). Country administrators can only manage their own country.
- [ ] POS: cashiers sign in on `https://pos.<domain>` and see only their store's till.

**After go-live**
- [ ] Nightly backup running (check `/var/log/alburhan-backup.log` the next morning).
- [ ] First month-end: payroll posted and paid, VAT return (UAE) reviewed.
