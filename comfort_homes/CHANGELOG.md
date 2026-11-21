# Comfort Homes — Changelog

## 0.0.7.dev13 — Purchase-value limit, delivery docket, deposit before contract, co-applicant tie-in

- Finance Manager may Approve or Reject only when Total Purchase Value (cash price) is under 3,000; Director always. Previously the test used the net amount financed. Reject stays tied to the same limit.
- Confirm Delivery now needs the supplier **Delivery docket** (new Attach field on the application). No supplier invoice upload: the Accounts Officer still creates and submits the Purchase Invoice at Receive Delivery Documents.
- The deposit must be recorded and submitted before Approved moves to Contract Signing (it is still checked at compliance sign-off).
- Application Assessment is no longer added as a customer document, so Download All Documents does not include it. It stays attached to the application.
- Read & Sign CIRA Authority Form button shows only in Draft and Initiated.
- Co-applicants: read-only panel on the Loan Application (name, state, CIRA signed, KYC complete, link); banner on the Co-Applicant form linking back to the main application; blocking messages now name the co-applicant; new "Co-app" column (None / Pending / Approved) in Portfolio Control.
- Fix: Prepare Independent Fee Invoice opened a draft that failed with "Item Name / UOM / UOM Conversion Factor is required". The draft line now carries item name, description, UOM and conversion factor.
- Needs install + migrate (workflow, custom field and client script are fixtures). Not site-tested.

## 0.0.7.dev12 — HP GL Trace, spouse rules, Loan Purpose off

- New read-only report **HP GL Trace** (Director, Finance Manager, Accounts Officer): every GL line behind a loan or application, customer and supplier side (deposit receipt, disbursement, gross-booking journal, demands, accruals, repayments, adjustments, write-off, fee invoices and their receipts, CHD purchase invoice and its payment). Filters: Loan, Application, Since (date/time), voucher type, account, include supplier side. Views: Lines, By account, By voucher. Check column flags an unbalanced voucher or a Sales Invoice with no VAT line.
- Fix: submitting as Married failed with "a.refresh_input is not a function". The mandatory rule sat on the Spouse Details section break. It is removed (also repaired on migrate) and the form script now gives any layout control a no-op `refresh_input`.
- Spouse Details still shows only when Married; spouse first name, surname, mobile, employer and employer contact are mandatory then.
- New read-only report **HP Fee VAT Check** (Director, Finance Manager, Accounts Officer): every submitted SMS / Call / Field Visit invoice with the VAT it booked, the VAT-inclusive 12.5% split it should have (2.50 = 2.22 + 0.28; 25.00 = 22.22 + 2.78, same method as the documentation fee), the route (Independent / Legacy) and a one-word status (OK, No VAT, Account, Check). Read-only: it corrects nothing.
- Servicing fees (VAT-inclusive, split revenue/VAT): the independent fee route already posts Dr 1120 / Cr income / Cr 2310 correctly and blocks any fee invoice that is not on that route. devcom's SINV-00001 (7.50) and SINV-00002 (25.00) were raised on the older Loan-linked route with no tax row; they stay untouched and are listed by the check. Turn the route on with Company > Independent fees enabled.
- Loan Connections now show servicing-fee invoices: Sales Invoice has its own "Servicing fees" group, found through the reference Loan field (the native Loan field is not used, so balances and schedule are untouched). Legacy invoices linked through the native Loan field are no longer listed there; HP GL Trace and HP Fee VAT Check still show them.
- Loan Purpose is hidden and not mandatory (HP-only). The dev10 "HP" resolver stays in case a value is ever sent.

## 0.0.7.dev11 — Portfolio Control (Director/Manager snapshot)

- New read-only report **Portfolio Control**: one line per application/loan, traffic light, worst first. Filters: Location, Stage, Light, Bucket, show closed/rejected. Short one-word columns: Light, Stage, Bucket (Current/Grace/D30/D60/D90/NPA), PI (None/Open/Part/Paid), Deliv (No/Pending/Yes).
- Light: Closed green; active loan current green, in 7-day grace orange, 8+ days red; any earlier stage red when nothing has happened for more than 72 h, otherwise orange. Delivery is taken from the workflow state "Delivered - Awaiting Documents" (Confirm Delivery).
- New workspace **Portfolio Control** with five number cards. Report, workspace and cards are limited to Director, Finance Manager and Loan Manager (checked again on the server).
- Set-based queries (no per-loan lookups). No ledger or status writes. Not site-tested.

## 0.0.7.dev10 — four-digit amounts, Loan Purpose "HP"

- Grid amounts of 1,000 or more no longer turn into NaN. Cause: two legacy grid handlers (income/expense/obligation and purchase-item grids) did `Number(cell.value)` on the cell after Frappe reformatted it to "1,000.00". The migrate-time repair now rewrites both to `flexiSafeNumber`, and the fixture script is corrected.
- "Could not find Loan Purpose: HP": `HP`/`Hire Purchase` typed in Loan Purpose now resolves to a single "Hire Purchase" master (created only if missing) before link validation. Other unknown values are still rejected normally.

## 0.0.7.dev9 — public submit: every click gets a visible answer

- Submit and Next buttons are never disabled and never get Bootstrap's `disabled` class (it ignores clicks silently). A click while checking, saving or after saving now shows a message instead of nothing.
- Duplicate-customer block is a visible click guard, not a dead button.
- Duplicate lookup and pre-save checks time out after 20 s with a message; the form stays editable and retry is allowed.
- A stuck native `window.saving` flag now gives a message and a safe retry. A failure before any request starts is treated as safe to retry rather than "uncertain".
- Required-field errors show once (no second dialog).
- Coordinator marker is now `dev9`; the managed-script upgrade cuts from either `dev8` or `dev9`, so an upgraded site never has two coordinators.
- Local Chromium regressions (synthetic native-save model) added; not Frappe site acceptance. Nothing deployed.

## 0.0.7.dev8 — source-only reliability and independent-fee candidate

- Single native-save submit coordinator, no recursive clicks, explicit failure/uncertain retry states; grid ownership and zero-price row preservation.
- Native master-ID geography filters/server guards; public callable API guard.
- Assessment actions available at KYC review stages; valid saved CIRA hides redundant signing; combined-pack UI simplified.
- Explicit conflict-safe native rollout preview for six document masters, catalogue/template and guarded non-inventory defaults; no auto-apply.
- Independent service invoices/allocated receipts, reference-only links, same invoice/value date, exact inclusive VAT, event uniqueness/authority and native-trigger rejection. Feature OFF pending policy/native isolation tests.
- Locked native LPO creation replaces permission-bypassing legacy generator; existing approval method/gates retained; no legacy state reset.
- Read-only manager overview and separate fee/legacy exception statement summaries; actual-booking chronology and flat-rate labels.
- Full prior rework register and all unperformed native/lifecycle/role/PDF tests retained in docs. No remote/site actions.

## 0.0.7.dev7 — consolidated acceptance rework

- Isolate PDF rendering from saved assessment document and verify persisted evidence.
- One combined six-form paper-pack recording action; preserve private PDFs, hashes, human attestations, same-day signing and manager review.
- Name missing legal-document scans instead of reporting an unrelated privacy error.
- Clear native financial handoff links and deposit draft/submitted status; remove duplicate deposit button implementations.
- Gate LPO/Active/Closed transitions on actual native financial completion, including update-after-submit.
- Both LPO approval roles submit the exact linked PO through a native synchronous task with permissions and retry safety.
- Reclass uses actual native posting date; no native backdated GL override.
- Stage-specific document menus, safe test email default, approved1195 chart amendment, deterministic master groups and reference-only operational configuration.
- Native telemetry warning remains documented, not monkey-patched. No deployment/production financial acceptance.

## v0.0.6 (November 2026) — Gross HP rebuild, setup and settlement

- Gross Hire Purchase model on native Lending: loan at 0% for the balance after deposit; one booking
  journal at disbursement; monthly straight-line interest release; scheduled late-payment charge
  (Loan Product fields, 2.5% a month, 7-day grace, stops at 90 days); penalties recognised on closure.
- Ledger accounts are found by account number (`hp_settings.account`), so the Chart of Accounts
  Importer's naming does not matter. New `setup_data/chart_of_accounts_flexi.csv` (107 accounts).
- `hp_setup.setup_masters`: roles, Company loan accounting and NPA 90, CHD supplier, HP Product item,
  Loan Product HP-STD, demand offset order. Dry run by default.
- `hp_settlement`: Rule of 78 early-settlement quote and rebate posting; write-off clearance of deferred
  interest and penalties (hooked on Loan Write Off submit).
- `hp_permissions`: minimal-access role matrix, plan first, apply on approval. No delete rights.
- Deposit is a Payment Entry (button "Record Deposit Payment"); the Sales Invoice route is removed.
- HP Customer Statement reads the deposit Payment Entry and shows rebates and waivers.
- Removed duplicate stray report folder and the old deposit invoice client script.

## v2.3.0 (November 2026) — Configuration Fixes & Print Format Resolution

### Fixes
- **HP Customer Statement report:** Fixed `adjustment_amount` → `amount`
  (Lending v16.8.0 renamed the field on Loan Balance Adjustment)
- **Print Formats renamed** to match Loan Document Template references:
  - Test Agreement → Hire Purchase Agreement
  - Schedule 1 Test → Schedule 1
  - Form 1 Test → Form 1
  - Form2 Test → Form 2
  - Consent Test → Consent Form
  - Summary Test → Summary of Financial Obligations
  - CIRA Authority Form imported from pre-prod
- **Loan Document Template:** Added `print_format` custom field (Link → Print Format)
  - This field was present on pre-prod but missing from dev/app fixtures
  - Required by FLEXI DOCUMENT PREVIEW script to generate customer documents

### Fixtures Updated
- `custom_field.json` — 184 fields (added Loan Document Template.print_format)
- `property_setter.json` — 9 setters (Province/City/Suburb title_field + search_fields)
- `server_script.json` — 30 scripts (updated Flat Repayment Schedule Sync for all frequencies)
- `print_format.json` — 8 formats (production names, not test names)
- `loan_document_template.json` — 6 templates (with print_format values set)

## v2.2.0 (November 2026) — Multi-Frequency Flat-Rate Support

### Breaking Change: Loan Product Schedule Type
- Changed from `Flat Interest Rate` → `Monthly as per repayment start date`
- This matches pre-prod and removes the Lending module's restriction that
  blocked Weekly and Bi-Weekly (Fortnightly) repayment frequencies
- The comfort_homes app's `Flexi Flat Repayment Schedule Sync` script
  overrides the reducing-balance schedule with flat-rate amounts

### Updated: `Flexi Flat Repayment Schedule Sync` Server Script
- **Previously:** Only handled `repayment_frequency == "Monthly"`
- **Now:** Handles Weekly (7-day), Bi-Weekly (14-day), and Monthly
- Calculates equal principal + equal interest per period for ALL frequencies
- Uses the correct instalment amount from the Loan Application:
  - Weekly → `custom_flexi_weekly_instalment_amount`
  - Bi-Weekly → `custom_flexi_fortnightly_instalment_amount`
  - Monthly → `custom_flexi_monthly_instalment_amount`

### Updated: `interest_override.py`
- Simplified: triggers on any Loan with `custom_flat_daily_interest > 0`
  (no longer checks schedule type — works for both Flat Interest Rate and
  Monthly as per repayment start date)
- Supports all repayment frequencies for term calculation

### Why This Change
The Lending module hard-validates at `loan.py` line 245:
```python
if self.repayment_schedule_type == "Flat Interest Rate":
    if self.repayment_frequency not in ("Monthly", "Yearly"):
        frappe.throw("Flat Interest Rate loans can only have monthly and yearly repayment frequency")
```
Flexi customers choose Weekly, Fortnightly, or Monthly repayment. Using
`Monthly as per repayment start date` bypasses this restriction while the
comfort_homes override script ensures the schedule remains flat-rate.

This matches the pre-prod site's configuration where all 16 Loan Products
use `Monthly as per repayment start date`.

## v2.1.0 (November 2026) — Flat-Rate Daily Accrual Override

### New Module: `interest_override.py`
- **Flat daily accrual** for "Flat Interest Rate" schedule loans
- Overrides the Lending module's declining-balance daily accrual with a constant
  daily rate: `total_term_interest ÷ days_in_term`
- Example: $158.62 ÷ 365 = **$0.4345/day** (constant, matches HP agreement)
- Three hooks:
  - `Loan → before_validate`: Calculates and stores flat daily rate on new loans
  - `Loan Repayment Schedule → on_update`: Recalculates rate when schedule is finalised
  - `Loan Interest Accrual → before_validate`: Overrides the accrual amount
- Non-flat-rate loans are unaffected (hook checks `repayment_schedule_type`)

### New Custom Field
- `Loan.custom_flat_daily_interest` (Currency, hidden, read-only)
  - Stores the pre-calculated daily rate
  - Set automatically at loan creation / schedule generation
  - Used by the accrual override

### Why This Change
The Lending module's "Flat Interest Rate" schedule generates equal monthly
instalments but accrues interest daily on the declining balance. This creates
a mismatch between:
- **Customer statement**: $13.22 interest per month (flat)
- **GL income statement**: $13.30 in month 1, declining to $1.20 in month 12

For Flexi's straightforward HP business, the GL should match the customer
agreement. This override ensures constant daily interest recognition.

### Impact
| Metric | Before (declining) | After (flat) |
|--------|-------------------|-------------|
| Daily GL interest | Variable ($0.43→$0.36→...) | **$0.43 every day** |
| Monthly income | Front-loaded | **$13.22 constant** |
| Total interest | $158.62 | **$158.62 (unchanged)** |
| Customer payments | Unchanged | Unchanged |

---


## v2.0.0 (November 2026) — Path B (Native-Aligned)

### Architecture
- Adopted **Path B**: works WITH the native Frappe Lending module
- Interest: native daily accrual (no deferral, no 2013 account)
- Fees: native Loan Charges + Item Tax Template (auto VAT)
- Disbursement account: 1004 BSP Bank (net of charges)
- Native penalty disabled (product `penalty_interest_rate = 0`)

### New Modules
- **`penalty.py`** — Custom HP penalty calculator
  - 2.2% of each overdue instalment amount
  - Applied once per scheduled payment date after 7-day grace
  - Runs as daily scheduled job (`daily_long`)
  - VAT-exempt
  - Uses Loan Balance Adjustment with traceable remarks

- **`comfort_homes/report/hp_customer_statement/`** — Script Report
  - Gross customer view: items + fees − deposit − payments = balance
  - Aggregates Lending module's principal/interest/charges into unified statement
  - Shows purchase items, admin fee, doc fee, interest, deposits, repayments, penalties

### Configuration Changes (for new sites)
- Company: `book_advance_payments_in_separate_party_account = 1`
- Company: `days_past_due_threshold = 90` (was 9)
- Company: `enable_perpetual_inventory = 0` (Flexi holds no stock)
- Company: IFRS 9 three-stage ECL classification configured
- Products: `penalty_interest_rate = 0` (custom penalty replaces native)
- Products: `repayment_schedule_type = "Flat Interest Rate"` (native)
- Products: `disbursement_account = 1004 BSP Bank` (was 1150)
- Products: Loan Charges configured (Admin Fee 2.5% + Doc Fee $25)
- Item Tax Template: "HP Doc Fee VAT 12.5%" on Documentation Fee
- HP Product: `is_stock_item = 0`
- Loan Deposit item: `income_account = 2010 Advance Payment`

### Inherited from v1 (Anuradha145/comfort-homes version-16)
- `api.py` — Bank statement import, deposit SI, reconciliation
- `co_applicant.py` — Co-Applicant linking
- `hooks.py` — Doc events, fixtures, Jinja helpers
- `loan_application_workflow.py` — Financial calculations, workflow guards
- `loan_mapping.py` — Application → Loan → Disbursement → PO value flow
- `print_helpers.py` — Jinja helpers for print formats
- `signed_pack.py` — Document signing workflow
- `webform_setup.py` — Public web form setup
- All fixtures (custom fields, print formats, workflows, etc.)

### v2.0.1 Fixes (November 2026)

**signed_pack.py** — Frappe v16 compatibility:
- Print Format fixtures: boolean strings '0'/'1' → integers 0/1 (fixes "Raw Commands required" error)
- `sync_reviewed_print_formats`: try/except on insert/save
- `_ensure_assessment_document_template`: filter to valid columns only (fixes "Unknown column 'print_format'" error)
- `_agreement_rows`: dynamic field list based on schema
- DOCUMENT_PRINT_FORMATS loop: check `has_field` before setting print_format

**Loan Product Configuration Note:**
- Documentation Fee Loan Charge amount = **$22.22** (net, VAT-exclusive)
- Item Tax Template "HP Doc Fee VAT 12.5%" adds $2.78 VAT → SI total = $25.00 VIP
- The `custom_flexi_documentation_fee` field on Loan Application remains $25 (VIP, customer-facing)
- The Loan Product charge stores the net amount for correct accounting split

**Validation Test Findings:**
- Loan Charges must be placed on `loan_disbursement_charges` (Disbursement), not just `loan_charges` (Loan)
- The comfort_homes app's `apply_loan_values_to_disbursement` hook should auto-copy charges
- Workflow roles (Director, Finance Manager, etc.) must be created before workflow can progress
