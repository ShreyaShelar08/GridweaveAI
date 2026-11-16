# GridWeave AI - Backend (Srushti's part, prototype)

FastAPI + PostgreSQL + JWT (access + rotating refresh tokens) + Alembic migrations. Covers: repo/CI, DB schema, auth + RBAC, marketplace & contract APIs,
billing engine + invoice PDF + test-mode payments, ML-as-API, logging/monitoring/deployment.

## Run it (Windows / PowerShell)
```powershell
# 1. Start PostgreSQL (needs Docker Desktop)   -- OR install PostgreSQL / use a free Neon database (see below)
docker compose up -d db

# 2. Python environment
python -m venv venv
venv\Scripts\activate
pip install -r requirements-dev.txt

# 3. Settings
copy .env.example .env          # then put a random SECRET_KEY in .env:
python -c "import secrets; print(secrets.token_hex(32))"

# 4. Create the tables (real database, via migrations)
alembic upgrade head

# 5. First admin (hidden password prompt) and optional demo data
python create_admin.py --email you@example.com --name "Your Name"
python seed.py

# 6. Start the API
uvicorn app.main:app --reload
```
Open **http://localhost:8000/docs**. No Docker? Install PostgreSQL from postgresql.org, create a database `gridweave`
and a user, then set `DATABASE_URL=postgresql+psycopg://USER:PASSWORD@localhost:5432/gridweave` in `.env`.
A hosted database (Neon / Supabase) works too: paste its connection string as `DATABASE_URL`.

Tests: `ruff check .` and `pytest -q` (SQLite). Against Postgres: create an empty database, run `alembic upgrade head`
on it, then `TEST_DATABASE_URL=... pytest -q`.

## Authentication in one minute
1. `POST /auth/register/seller` or `/auth/register/buyer` - creates the user **and** the role profile, password stored as a bcrypt hash.
2. `POST /auth/login` - returns `access_token` (15 min) + `refresh_token` (7 days). 5 wrong passwords lock the account for 15 min.
3. Send `Authorization: Bearer <access_token>` on every request. Role decides what is allowed (seller / buyer / admin).
4. When the access token expires: `POST /auth/refresh` with the refresh token -> a **new pair**; the old refresh token dies (rotation). A replayed old token revokes the whole session.
5. `POST /auth/logout` (this session), `/auth/logout-all` (everywhere), `/auth/change-password` (kills all old sessions).
6. Everything important is written to `audit_logs` (admin: `GET /admin/audit-log`).

## Folder map
| Path | What it does |
|---|---|
| `app/config.py` | Settings from `.env` (secrets never in code) |
| `app/database.py` | DB engine + session per request |
| `app/models.py` | **DB schema**: users, plants, buyers, contracts, contract_offers, readings, invoices, payments |
| `app/schemas.py` | Request/response shapes = API contract for the frontend (Yash) |
| `app/security.py` | bcrypt passwords, JWT, `require_roles()` for RBAC |
| `app/routers/auth.py` | register, login, me |
| `app/routers/plants.py` | seller plants (+marketplace list) and buyer profiles |
| `app/routers/contracts.py` | create / negotiate / accept (=activate) / reject / terminate a VPPA |
| `app/routers/readings.py` | push metered kWh; auto-links to the active contract |
| `app/routers/billing.py` | generate invoice, PDF, pay (test mode) |
| `app/routers/ml.py` + `services/ml_client.py` | forecast / price / match endpoints (placeholders for Shreya's models) |
| `app/routers/admin.py` | user management + stats |
| `app/services/` | billing maths, PDF builder, payment gateway stub, ML adapter |
| `tests/` | end-to-end tests |
| `.github/workflows/ci.yml` | CI: lint + tests on every PR |
| `Dockerfile` | deployment |

## Database relationships
```
users 1--* plants          users 1--1 buyers
plants 1--* contracts *--1 buyers          contracts 1--* contract_offers
plants 1--* readings (readings.contract_id -> contracts)
contracts 1--* invoices 1--* payments
```

## How the VPPA flow works
1. Seller registers -> `POST /plants`.  Buyer registers -> `POST /buyers` (profile).
2. Buyer browses `GET /plants/marketplace` and `GET /ml/matches`.
3. Buyer `POST /contracts` (proposed price + monthly kWh + dates) -> status **proposed**.
4. Parties alternate `POST /contracts/{id}/negotiate` (counter-offers; history kept) -> **negotiating**.
5. The party who did NOT make the last offer calls `/accept` -> **active** (a plant can't have two overlapping active contracts).
6. Readings (`POST /readings/bulk`) from ingestion/simulator get tagged with the active contract.
7. `POST /billing/generate` -> invoice = sum(kwh_delivered in period) x contract price (+tax). Overlapping periods are blocked (no double billing).
8. `GET /billing/invoices/{id}/pdf` -> PDF.  Buyer: `POST /billing/invoices/{id}/pay` then `POST /billing/payments/confirm` (simulated gateway) -> invoice **paid**.

## Roles
| Role | Can |
|---|---|
| seller | register plants, push readings, negotiate/accept, generate invoices, see own data |
| buyer | create profile, propose/negotiate/accept, view & pay invoices, get matches |
| admin | everything read-level + user management + stats (created via `seed.py` only) |

## Integration points for the team
- **Shreya**: replace function bodies in `app/services/ml_client.py` (keep signatures). Ingestion can call `POST /readings/bulk`.
- **Yash**: all endpoints and shapes are documented live at `/docs`; CORS already allows localhost:3000 and :5173.

## Deployment, secrets, monitoring
- Secrets: only via environment variables / `.env` (git-ignored). Generate `SECRET_KEY` with `python -c "import secrets; print(secrets.token_hex(32))"`. On Render/Railway set them in the dashboard.
- Deploy: `docker build -t gridweave . && docker run -p 8000:8000 --env-file .env gridweave` (or connect the repo to Render/Railway; start command `uvicorn app.main:app --host 0.0.0.0 --port $PORT`).
- Logging: console + `logs/app.log` (rotating). Every request logs method, path, status, latency, request id (also sent back as `X-Request-ID`). Unhandled errors are logged with stack trace.
- Monitoring: `GET /health` (checks DB) - point UptimeRobot or the platform health check at it.

## Prototype limits (say these in viva - they show you know the next steps)
SQLite -> PostgreSQL; `create_all` -> Alembic migrations; floats -> `Numeric` for money; test gateway -> Razorpay test keys;
no refresh tokens / rate limiting; ML endpoints are placeholders.
