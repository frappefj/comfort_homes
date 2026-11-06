# Comfort Homes — Changelog

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
