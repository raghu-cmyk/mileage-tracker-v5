# Mileage Tracker v5

IRS-substantiation-grade mileage log for self-employed filers (Schedule C). Built by Mission Control's `plan → implement → validate → deliver → report` chain.

## Features

- **Authentication** — Argon2id password hashing, session expiry, login rate limiting
- **Vehicles** — CRUD with annual odometer readings; archive instead of hard-delete when trips exist
- **Trips** — Full IRC §274(d) substantiation, explicit category selection, audit trail, late-entered flag
- **Rates** — Time-effective IRS mileage rates as seeded DB reference data (never hardcoded)
- **Deductions** — Integer-cents math, year summary with business-use percentage
- **Export** — IRS-compliant CSV trip log and PDF year summary
- **Design system** — Shared tokens, dark mode with persistent toggle, mobile-responsive layout
- **Receipt capture** — Per-trip image upload, durable storage, ownership-gated display, export references

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python run.py
```

Open http://127.0.0.1:8000 — register the single user account, then log trips.

## Tests

```bash
pytest tests/ -q
```

## Branch

Feature branch: `feat/mvp-v5` (target: `mc_webhook_test`)
