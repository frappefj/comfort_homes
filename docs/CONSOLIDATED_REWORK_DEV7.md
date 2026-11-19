# Flexi consolidated acceptance rework — 0.0.7.dev7

**Complete source candidate, not a deployment or accounting acceptance certificate.**
Baseline: `frappefj/comfort_homes` main `a1a5e7a1db7a43e0ffcea081f255d5825efbc8f1` (dev6).
Working branch: `rebuild/consolidated-acceptance-dev7`. Target devcom only; Comfort UAT remains read-only and was not accessed during this investigation.

## 1. Investigation and observed root causes

### Native buttons are available; the application handoff is deficient
Using the supplied signed-in devcom browser session, directly opened native Loan ACC-LOAN-2026-00002. Its actual main toolbar contains **Submit**; document is Draft, docstatus0, is_term_loan1, clean form, effective form submit permission1. Directly opened submitted/Sanctioned July Loan ACC-LOAN-2026-00001; native custom buttons include **Loan Disbursement**, grouped under Create. No financial button was clicked. This disproves a blanket statement that native Submit/Create are absent or need new permission grants for that session. It does not establish every user's role permissions or why the user's particular screen differed.

Actual application00002 reached **Active Loan Account** while linked Loan00002 remained **Draft/disbursed0**, its PO849 remained Draft, no PI or booking JE existed. Application workflow status is not a ledger transaction. This is an app integration/control gap, not an accounting configuration failure.

### Actual GL boundary
Whole devcom ledger review found exactly four uncancelled rows: two submitted deposit receipts, each Dr1201/Cr2010 FJD170, dated July1 and November19. Total340Dr=340Cr. No receivable/clearing/fees/VAT/deferred-interest posting has yet been observed. Neither test has a submitted disbursement. No candidate code was run against live records to claim otherwise.

## 2. Changes in this candidate

| Finding | Resolution in source | Acceptance boundary |
|---|---|---|
| ASSESS-01 PDF attaches but proof fields are empty | Render on a separate document instance; save original evidence document; reread DB evidence fields and refuse false success | Regression mutates native-style in_print flag and proves simulated save persistence; no live dev7 generation yet |
| PACK-01 repeated uploads/unclear missing-private-PDF error | New POST-only `record_signed_pack`, one private PDF across six legal rows in one save; manager verifies once; optional per-form action retained | PDF parsing/ownership, all-six attestation, required signatures, fingerprints and manager review remain; no auto-identification of legal content |
| Missing FORM-2 looked like a date/privacy failure | Error names all missing legal rows before opening files | No signature dates are fabricated or changed |
| Same-day signing concern | Already permitted; explicit same-day label and regression coverage | Same-day order still relies on truthful customer-first attestation |
| WF-01 unclear native financial handoff | Read-only Financial Handoff group: complete/view deposit; open draft Loan with Submit guidance; open Loan/disbursement; show draft versus submitted native records | Uses native forms and native permissions, does not auto-submit finance documents |
| WF-02 deposit step not operationally visible | Single owner for Record Deposit Payment; submitted/draft receipt link remains visible; server creation restricted to approved pre-compliance states; reload under receipt-creation lock | No new workflow state or deposit invoice; no auto-payment or customer bank routing |
| WF-03 operational progress despite no financial records | Server gates before generating/progressing LPO require submitted matching full disbursement and submitted tagged gross journal; Active Loan Account requires cash-price CHD PI clearing1180 to2111 with no input VAT; Closed requires native LoanClosed | Existing invalid states are not silently reset on migration; inspect them separately |
| PROC-01 Approve LPO did not submit PO | Both manager/director approval transitions bind a synchronous task that validates exact app/CHD/FJD/cash-price PO and calls native submit once with submit permission | Application/PO locks, reread validation, no ignore_permissions or direct DB status edits; live submit proof pending |
| DATE-01 native replaces old GL date | Do not force past native posting dates. Reclass JE uses disbursement.posting_date (fallback only if absent) so both origination entries share actual GL date | Historical value-date/aged-arrears tests are distinct from clean current-date core tests; no July-to-October period reconciliation claim |
| UI clutter | Customer Documents only at Contract Signing. Evidence actions only intake/contract stages, assessment requires KYCYes; optional individual forms separate | Native Actions is intentionally NOT hidden wholesale: it is required to initiate/review/approve. Clear role/stage actions must remain accessible |
| Telemetry Duplicate Name after successful submit | Native issue documented, not monkey-patched. Check saved state before retry; optional native telemetry opt-out requires explicit separate approval | No milestone deletions, native code edits or suppress-all-errors behaviour |
| Configuration drift | Chart1195 parent5200/Expense; deterministic Supplier Distributor and Item Products defaults with dry-run differences; approved operational reference includes111-account metadata/category/tax values,10branches,342location paths and two July–June FYs | Reference JSON is NOT a fixture and is not automatically imported; no chart replacement, banking/permissions/location writes on migration |

The primary workflow remains15states/20transitions. Only the two Approve LPO task bindings change; it is not rewritten into a new approval scheme. Test workflow email alerts remain0 on fixture import, matching the approved suppression; production enablement is a separate decision. The workflow's account/party eligibility and approval thresholds remain intact.

## 3. Native-first decisions and unfixed external matters

- Lending16.8.1 unconditionally defaults disbursement.posting_date to current date. The custom app does NOT modify native Lending or pretend this setting enables backdated GL. Current-date core tests use both datesNovember19; historical schedule tests can have earlier value dates with explicit test-only period limitations.
- Native telemetry milestone claim uses duplicate-key exceptions within a savepoint while user-visible error messages may remain queued. The successful submitted application was reread before any retry. Native Frappe System Settings **Allow Sending Usage Data for Improving Applications** (`enable_telemetry`) can be proposed off, but Cloud `pulse_force_enabled` may override it. No change applied. Prefer upstream correction that avoids leaking expected duplicate messages. Do not delete milestones to clear the message.
- Effective production roles remain untested/unrefined. The API/browser account holds many roles; the current native permissions include deletion grants. Candidate does not apply Custom DocPerm, copy UAT permissions, assign users or certify separation of duties. Independent permission review remains required.
- Existing native buttons were present in the investigated session. No arbitrary JavaScript reimplementation of Submit/Disburse was added.
- Public full intake, city/suburb cascade, PDF visual/legal-content review, report reconciliation, fortnightly/weekly submitted schedules, penalties/DPD/NPA, write-off/waiver/settlement/closure, scheduler runs and CHD invoice GL remain site acceptance gates.
- User-entered assessment/scans are only structurally checked PDFs plus explicit human content attestation. A filename or existing attachment alone is not evidence the actual form contents/signatures are correct.

## 4. User-directed holds preserved

Banking/mode/repayment routing awaits Finance; no aliases/mappings copied. Repossession manual Accounts task. Digital legal signing disabled. Employers, letterhead and commercial inputs deferred, not GL-test blockers. Flat rates12/19/22/25/27; repaymentsMonthly/Fortnightly/Weekly; initial excluded appraiser/underwriter/onboarding roles unchanged. Gross accounting, FJD, deposit Payment Entry, no CHD input VAT, native penalty0 and native NPA89/custom stop90 preserved.

## 5. Deployment is a separate site-change batch

1. User reviews/merges source on a non-main review branch; do not push directly to main.
2. Verify complete source using RELEASE_MANIFEST.json and rerun full tests. ZIP has repo-root files and comfort_homes package; no nested extra app folder or .git.
3. Take recoverable test-site backup and review current app/Loan/PO/attachment state. Do not delete/cancel test documents to make balances agree.
4. Explicitly approve devcom deployment/migration. Cloud Migrate is a dashboard action, not REST. Fixture import changes workflow task bindings/email flag and app UI; this candidate has no permission/master auto-apply.
5. User deploys approved commit and runs Migrate. Hard-refresh Desk assets. Check installed version and task group bindings, same15/20workflow, new handoff and one-pack UI, setup dry runs. Do not run setup_masters(0) or permission apply without separate approval.
6. Existing application00002 already has an inconsistent Active Loan Account label; deployment does not reset it. A separately approved recovery/test batch must reconcile its native draft Loan and draft PO without manufacturing posting evidence. Test new progression gates with a controlled case; do not claim they prevented historical transitions retroactively.
7. Test assessment persistence, no false success, combined pack/same-day signing/missing-form messages; confirm real-user positive/negative permissions independently.
8. Submit a controlled current-date native Loan/disbursement. Verify actual dates, one tagged reclass, no duplicate postings,1101Dr879.60,1180Cr849.00,2013Cr158.62,4002Cr16.98,4003Cr22.22,2310Cr2.78, deposit receipt+reclass2010net0 for THAT case. July test deposit remains outstanding separately.
9. Test both Approve LPO roles on native PO submission; verify PO has no GL. Invoice CHD849 with noVAT postsDr1180/Cr2111. Repayment73.30 leaves1101 806.30; release13.22 leaves2013 145.40. Rebate with six of twelve elapsed42.71. Jobs are proven only by observed scheduled/authorised server execution, not unit tests or REST guesses.
10. Record failures as findings, stop and review; no native-record/GL patching. Rotate project credentials at handover.

## 6. Test scope and limits

Full Python tests, Node-stub action tests, Python/JS compilation, JSON/Jinja parsing and source/wheel packaging are executed locally. These are not a full Frappe Python3.14 bench, migration, effective permission, PDF-render/layout or posted-ledger run of dev7. No independent second-agent review tool was available. Review before financial/permission deployment remains mandatory. See bundled verification summary for actual counts and hashes.
