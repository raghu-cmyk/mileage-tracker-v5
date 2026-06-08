# Mileage Tracker — Requirements Specification

**Status:** Draft v0.1
**Scope:** Single-user, authenticated web application for recording vehicle mileage and producing IRS-substantiation-grade records for tax filing.

> **Documentation note:** This file is the requirements deliverable only. Once a repository exists it must be relocated into the repo's documentation system (proposed path: `docs/requirements/mileage-tracker-requirements.md`) and registered under the repo's documentation conventions. No requirements documentation should live outside that system.

-----

## 1. Purpose & Context

The application lets one self-employed user record each vehicle trip throughout the tax year and, at year end, produce a complete, audit-defensible mileage log and a computed deduction figure to hand to their accountant or transcribe onto a Schedule C.

The deduction is computed using the **IRS standard mileage rate method**: `deduction = miles × applicable rate`, where the rate is resolved from reference data keyed on trip category and trip date.

### 1.1 Regulatory basis (drives the data model)

- Substantiation is governed by IRC §274(d) and IRS Publication 463: every trip needs **date, origin, destination, business purpose, and miles**, plus **odometer readings at the start and end of the tax year** per vehicle.
- Records must be **contemporaneous** (entered at or near the time of the trip). Reconstructed logs are an audit red flag.
- Records must be **retained ≥ 3 years** from filing (target: 7 years).
- The standard mileage rate is set annually and **can change mid-year** (this has happened historically), so rates are time-effective data, never a constant.

-----

## 2. Assumptions

These shape the architecture. Each is a decision point that, if wrong, changes the requirements.

|# |Assumption                                                 |Impact if incorrect                                                                                |
|--|-----------------------------------------------------------|---------------------------------------------------------------------------------------------------|
|A1|The user is **self-employed (Schedule C)**.                |If an employee, federal mileage deduction is generally disallowed; the app's purpose changes.      |
|A2|**Standard mileage rate method** only (not actual-expense).|Actual-expense requires receipt/expense capture and a business-use-% engine — a larger system.     |
|A3|**Manual trip entry** (no GPS/automatic tracking).         |Auto-tracking introduces a location pipeline, background services, and device platform constraints.|
|A4|**Web application**, single deployment, one user account.  |Mobile-native or offline-first changes storage, sync, and auth design.                             |
|A5|One or more **vehicles** owned by the single user.         |—                                                                                                  |

-----

## 3. Scope

### 3.1 In scope

- Authenticated single-user access.
- Vehicle records with annual start/end odometer readings.
- Trip records with full §274(d) substantiation fields.
- Data-driven trip categories and time-effective mileage rates.
- Year-end deduction computation and IRS-compliant log export.
- Record retention and an edit audit trail.

### 3.2 Out of scope (explicitly)

- GPS / automatic trip detection.
- Multi-user, sharing, or accountant-collaboration accounts.
- Actual-expense method and receipt management.
- Direct e-filing or IRS API integration.
- Employer reimbursement workflows.

-----

## 4. Domain Model

Entities and their relationships. The model is deliberately small; correctness lives in the constraints, not the entity count.

```
User (1) ──< Vehicle (1..*) ──< Trip (0..*)
                                   │
MileageRate (reference data) ──────┘ resolved by (category, trip_date)
TripCategory (reference data) ─────┘
AuditEvent (0..*) ── attached to Trip / Vehicle edits
```

### 4.1 User

The sole account. Single-user does **not** mean unauthenticated — the account exists to protect the data, which contains locations, purposes, and patterns of movement (PII).

### 4.2 Vehicle

- `id`, `display_name`, `description` (e.g. make/model/year as free text)
- `tax_year` scoped odometer: `odometer_year_start`, `odometer_year_end` per tax year
- A trip references exactly one vehicle.

### 4.3 Trip — the core record

Required fields (all must be present for the record to be valid):

- `trip_date` — the date the trip occurred
- `origin` — starting location
- `destination` — ending location
- `business_purpose` — free text describing why the trip was made
- `miles` — exact miles driven (> 0)
- `category_id` — FK to `TripCategory` (see §4.4)
- `vehicle_id` — FK to `Vehicle`

Metadata (system-managed, for contemporaneousness and audit):

- `created_at` — when the record was entered (distinct from `trip_date`)
- `updated_at`
- Optional per-trip: `odometer_start`, `odometer_end`

### 4.4 TripCategory (reference data)

The trip's tax treatment is an **explicit enumerated value selected by the user**, stored as data — never inferred by matching keywords in `business_purpose`.

- `id`, `code` (e.g. `business`, `medical`, `moving`, `charitable`, `personal`), `display_name`, `is_deductible`
- `personal` trips are recorded (to support a clean business-use percentage and a complete log) but never contribute to a deduction.

> **Design constraint (per project rules):** If automatic classification from `business_purpose` text is ever requested, it must be implemented as a structured AI completion that returns a category enum with confidence — not string/phrase matching. The default and simple path is explicit user selection.

### 4.5 MileageRate (reference data)

The single source of truth for rates. No rate is hardcoded in application logic.

- `id`, `category_code` (matches a `TripCategory.code`)
- `rate_cents_per_mile`
- `effective_start_date`, `effective_end_date`
- Seeded with **real, citable** IRS values (e.g. 2026 business = 72.5¢, medical/moving = 20.5¢, charitable = 14¢; 2025 business = 70¢). This is reference data, not fabricated test data.

**Rate resolution:** for a given trip, the applicable rate is looked up where `category_code` matches and `trip_date` falls within `[effective_start_date, effective_end_date]`. Exactly one rate must match; zero or multiple matches is an error condition (see §6.3), never a silent default.

### 4.6 AuditEvent

Append-only record of create/update/delete on Trip and Vehicle: `entity_type`, `entity_id`, `action`, `field_changes`, `timestamp`. Because reconstructed logs are an audit red flag, a transparent edit history strengthens the record's defensibility rather than hiding changes.

-----

## 5. Functional Requirements

### 5.1 Authentication

- FR-A1: A single registered user authenticates before any data access. No guest/fallback/anonymous path.
- FR-A2: Passwords stored with a modern memory-hard hash (e.g. Argon2id). Login failures return a clear error; they are never swallowed.
- FR-A3: Authenticated session with explicit expiry and logout. All traffic over HTTPS.
- FR-A4: Login attempts are rate-limited.

### 5.2 Vehicle management

- FR-V1: Create, view, edit, and archive vehicles. Vehicles with trips cannot be hard-deleted (archive instead) to preserve the log.
- FR-V2: Record start-of-year and end-of-year odometer readings per vehicle per tax year.

### 5.3 Trip management

- FR-T1: Create, view, edit, and delete trips. Every mutation writes an `AuditEvent`.
- FR-T2: Trip creation **rejects** any record missing a required §274(d) field, with a specific message naming the missing field. Incomplete trips are never persisted.
- FR-T3: `miles` must be a positive number; `trip_date` cannot be in the future.
- FR-T4: The user selects `category` from the enumerated list; the value is stored, not derived.
- FR-T5: List/filter trips by date range, category, and vehicle.

### 5.4 Contemporaneousness signal

- FR-C1: The system records `created_at` independently of `trip_date`.
- FR-C2: When a trip is entered substantially after `trip_date` (threshold configurable as data, e.g. > 7 days), the record is flagged as **late-entered** and surfaced in the UI and on export. This is a visible signal, not a block, and is never silently suppressed.

### 5.5 Deduction calculation & reporting

- FR-R1: For a selected tax year, compute per-trip deductible amount as `miles × resolved_rate` (per §4.5), summed by category and in total.
- FR-R2: Personal-category miles are excluded from the deduction and reported separately; business-use percentage = deductible miles ÷ total logged miles.
- FR-R3: A year summary shows: total miles, miles by category, deductible total, business-use %, and the count of late-entered trips.

### 5.6 Export

- FR-E1: Export a year's full trip log to CSV with all substantiation columns (date, origin, destination, purpose, category, miles, vehicle, applied rate, computed amount, late-entered flag).
- FR-E2: Export a year summary to PDF suitable to hand to an accountant.
- FR-E3: Exports reflect exactly what is stored; no rounding or omission that changes a reported figure.

-----

## 6. Non-Functional Requirements

### 6.1 Data integrity

- NFR-1: All validation failures (missing fields, ambiguous rate resolution, invalid odometer ordering) raise explicit, typed errors surfaced to the user. No silent failure, no fallback default value.
- NFR-2: Monetary/mileage math is exact (integer cents and decimal miles); no floating-point drift in reported deduction figures.

### 6.2 Security & privacy

- NFR-3: Trip data (locations, purposes) is PII; encrypted in transit (HTTPS) and at rest.
- NFR-4: Authorization is enforced server-side on every request — never relying on client-side hiding.

### 6.3 Rate resolution correctness

- NFR-5: Rate lookup returning zero or multiple matches for a `(category, date)` is a hard error that blocks the calculation and reports which trip and date failed. The reference rate table must be complete and non-overlapping across effective dates; this is validated when rates are loaded.

### 6.4 Retention & backup

- NFR-6: Records are retained for a configurable minimum (default 7 years). No automatic purge.
- NFR-7: Deleting a trip requires confirmation and is captured in the audit trail; bulk year deletion requires a prior successful export.
- NFR-8: The datastore is backed up on a defined schedule; restore is verified.

-----

## 7. Open Decisions (need your input before build)

1. **Filer type (A1):** Confirm self-employed/Schedule C. If employee, the deduction premise generally fails federally — does the app still serve a reimbursement-tracking purpose?
2. **Method (A2):** Standard-mileage only, or do you also need the actual-expense method (receipts + business-use depreciation)? This roughly doubles scope.
3. **Categories:** Business only, or also medical / moving / charitable (each has its own rate)?
4. **Platform (A4):** Web app, mobile, or both?
5. **Vehicles:** Single vehicle, or multiple?

-----

## 8. v3 Design & Experience Enhancements (required)

These build on the existing functional scope — no feature is dropped. The v3 build
must additionally deliver a noticeably more polished, accessible front end.

### 8.1 Polished UI (FR-UI1)

- A refined, modern visual design: clear visual hierarchy, consistent spacing scale,
  rounded surfaces/cards, subtle elevation/shadows, and smooth focus/hover states.
- Cohesive design tokens (color, typography, spacing, radius) defined once and reused
  across every page. Forms, tables, buttons, and empty/loading/error states all share
  the same component styling.
- Polished data presentation for trips, summaries, and the year-end report.

### 8.2 Dark mode (FR-UI2)

- A full dark theme alongside the light theme, implemented with CSS custom properties
  (design tokens) so both themes share one component layer.
- A visible theme toggle in the navigation. The choice persists across sessions
  (e.g. localStorage) and respects the OS `prefers-color-scheme` on first visit.
- Both themes meet WCAG AA contrast for text and interactive elements.

### 8.3 Mobile responsive layout (FR-UI3)

- Fully responsive from small phones (~360px) to desktop, using a fluid/grid layout
  with sensible breakpoints — no horizontal scrolling, no clipped controls.
- Navigation collapses appropriately on small screens (e.g. a menu/hamburger), and
  data tables reflow or scroll gracefully on narrow viewports.
- Touch targets are at least 44px; forms are comfortably usable on mobile.

-----

## 9. Receipt Capture (required)

Trips frequently have supporting documents (toll/parking/fuel receipts, service
invoices). v4 adds the ability to attach a receipt image to a trip and surface it
everywhere the trip is shown or exported. This strengthens §274(d) substantiation.

### 9.1 Upload (FR-RC1)

- A user can upload a receipt image for a specific trip (one or more receipts per trip).
- Accept common image types (JPEG, PNG, WebP, HEIC) up to a sensible size limit
  (e.g. 10 MB); reject other types with a clear error.
- The upload is associated with the owning trip and the authenticated user only;
  authorization is enforced server-side (a user can never attach to or view another
  user's trip/receipt).

### 9.2 Storage (FR-RC2)

- Store the receipt image bytes durably (filesystem path or blob) with a DB record
  linking it to the trip: id, trip_id, original filename, content type, byte size,
  upload timestamp, and a content hash for integrity.
- Deleting a receipt is captured in the audit trail; deleting a trip also removes (or
  archives) its receipts per the retention rules.

### 9.3 Display (FR-RC3)

- The trip detail view shows a thumbnail/preview of each attached receipt with a link
  to view the full image, plus filename, size, and upload date.
- A dedicated route serves the stored image bytes with the correct content type, gated
  by ownership.
- Empty state when a trip has no receipts; graceful handling if an image is missing.

### 9.4 Export reference (FR-RC4)

- The CSV export includes a receipt reference column per trip (e.g. count and stable
  identifier(s)/filename(s)) so the row ties back to its documents.
- The PDF year-end summary notes, per trip (or in an appendix), whether a receipt is on
  file and its reference, suitable for an accountant or auditor.
- Exports never omit or fabricate receipt references — they reflect exactly what is stored.

### 9.5 Mobile-friendly capture (FR-RC5)

- On mobile, the upload control allows capturing directly from the device camera or
  choosing an existing file (e.g. an `accept="image/*"` input with `capture`).
- The upload UI follows the Section 8 design system (tokens, dark mode, responsive,
  44px touch targets) and shows clear progress/success/error states.
