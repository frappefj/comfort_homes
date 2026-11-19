# Flexi gross-HP correction candidate

**Current version:** 0.0.7.dev7. **Status:** SOURCE CANDIDATE — NOT DEPLOYED OR LIVE-DISBURSEMENT ACCEPTED.

**Latest:** docs/CONSOLIDATED_REWORK_DEV7.md supersedes historical status statements below. Baseline main a1a5e7a1db7a43e0ffcea081f255d5825efbc8f1. No site deployment in this action.
Prepared against `rebuild/gross-hp` at `7884c54a5980c3354ffa0f153120452a6f2cb4fa`.
Native reference versions: Frappe 16.56.0, ERPNext 16.55.0, Lending 16.8.1.

All implementation changes target **devcom.syd.frappe.cloud**, only after a
separate site-batch approval. **comfort.syd.frappe.cloud is strictly read-only**.
No native application source was edited. No site migration, configuration apply,
financial posting or user-role assignment was performed for this candidate.
The shared implementation plan is the business/configuration approval register;
this file describes the repository implementation and its limits.

## 1. Approved native NPA boundary

The user approved native Loan Product NPA threshold **89** to flag stored DPD
**90**, because Lending 16.8.1 uses `days_past_due > threshold`. The custom penalty
stop remains **90**; Company days-past-due setting remains **90**. The constants
are deliberately separate so fixing native classification does not change late
charges. No custom NPA classifier was added.

The specific NPA decision release gate is now resolved in code. This is not
installation or disbursement approval. The native counter's as-of/date offset and
actual flags must still be demonstrated on devcom. Neither site's product or
Company settings were changed by this repository update.

## 2. Locked business model

Flexi and CHD are separate legal entities. Flexi retains the customer deposit,
issues the CHD LPO at goods cash price, receives CHD's non-stock invoice without
claiming input VAT, and settles CHD monthly. Gross HP interest is inside the
post-deposit Loan balance; servicing interest is zero. No IFRS 9 or EIR.

Only the documentation fee is VAT-inclusive: 25.00 = 22.22 income + 2.78 VAT.
Admin is 2.5% of value after deposit and VAT-exempt. The existing origination
rounding order is preserved, following the user's explicit decision.

## 3. Two explicit test cases, not a hidden one-cent correction

| Value | Original fixed-input reference | Preserved origination output |
|---|---:|---:|
| Goods / deposit | 849.00 / 170.00 | 849.00 / 170.00 |
| Net financed | 720.98 | 720.98 |
| Term charges | 158.61 | 158.62 |
| Post-deposit repayment balance | 879.59 | 879.60 |
| Gross contract | 1,049.59 | 1,049.60 |
| Monthly instalments | 73.30 x 11, then 73.29 | 73.30 x 12 |
| Fortnightly | 33.83 x 25, then 33.84 | 33.83 x 25, then 33.85 |
| Weekly | 16.92 x 51, then 16.67 | 16.92 x 51, then 16.68 |
| Receivable after 73.30 payment | 806.29 | 806.30 |
| Deferred after 13.22 release | 145.39 | 145.40 |
| Rule-of-78: six of twelve elapsed | 42.70 | 42.71 |

The original helper tests remain unchanged. A complete origination case and
browser/Python parity tests cover the chosen output. Neither column is evidence
of a posted site transaction. The added reclass balances the origination case
with deposit Dr 170.00, clearing Dr 30.60, and credits 158.62 / 16.98 / 22.22 / 2.78;
native clearing Cr 879.60 consequently leaves Cr 849.00. No rounding JE is added.

## 4. Origination preservation

- Keep the primary workflow's 15 states / 20 transitions and 130 public form
  fields. Working origination is the reference; UAT servicing/accounts are not.
- Application `loan_amount` remains **net financed** for the existing 3,000
  approval threshold. `loan_mapping` maps gross post-deposit repayments to Loan.
- Preserve exact monthly-equivalent decision inputs, Salary Deductions handling,
  and UAT's assessment-frequency divisor (52/12 or four weeks). Do not use rounded
  display instalments to decide eligibility.
- Finalise the flat summary after native validation on **save and submit**.
  Existing-customer prefill requires read permission; public callers cannot supply
  an existing Customer link. Customer creation is prepared before native validation.
- Browser previews share one pure engine; binary rounding matches preserved Python
  `round`, including half-period counts. This is needed because Frappe's native
  epsilon-based browser rounding is not equivalent at all boundaries. No extra
  event-listener bridge is registered by the shared helper.
- Co-applicants receive their own profile totals, not a second facility. Necessary
  Desk identity/geography/CIRA/employment behaviour is packaged. The co-applicant
  post-review workflow remains a separately held decision; its old loan/PO task
  has deliberately not been copied.

## 5. Native integration safeguards

Lending's native Loan defaulting treats zero interest as missing. The small
`GrossHPLoanMixin` calls the native defaulting method, then restores zero only for
marked, non-imported Flexi HP Loans before later validation/scheduling. It refuses
to silently rewrite an already-submitted non-zero-rate loan. The product keeps
its contractual default rate; each Loan stores the selected application rate.

Native disbursement defaulting unconditionally replaces its account when a Mode
of Payment is set. The app's draft validation restores goods clearing and clears
the collection preference from that disbursement. Before submission it checks
matching approved application/customer, whole amount, zero servicing rate,
clearing account, submitted bound deposit, and absence of another disbursement.
Native security withholding, additional native disbursement fees and a customer
bank payout are rejected for this gross goods-booking path.

Deposits must be submitted Receive Payment Entries for the same customer,
company, FJD and account 2010, equal in paid/received amounts, not invoice-allocated
and not shared by applications. Receipt/loan locks serialize competing checks.
There is no deposit Sales Invoice.

Native schedules remain authoritative; select **No Rounding** with effective
currency precision two. A submitted Loan must not suppress mapping tenure onto
its new disbursement. No repayment schedule rewrite is enabled.

## 6. API, collection and document safeguards

- Mutating Python endpoints, including retained directly callable agreement-signing
  handlers, are POST-only with document/role checks. Completed agreement rows are
  protected from overwrite; linked co-applicant writes are separately authorised.
  HP-only handlers validate the agreement code and use the actual template link,
  not a site-specific LDT number. No digital-signing UI is enabled. Retained
  safe-exec document APIs call a common native-dispatch access gate, including
  agreement ownership, draft-only mutation and company-signing role checks.
- Bank import persists a deterministic bank/file/sheet/row key and uses native
  Loan Repayment references. Reconciliation/bank/loan locks, exact submitted
  reference matching and per-row savepoints protect retries and failures. This
  identifies **exact file re-imports**, not all re-exported statement duplicates.
- Imported money uses the selected company bank; a customer's preferred collection
  mode is not evidence of the actual bank used. No UAT auto-advance shortcut is copied.
- Prior PDF pack attachments are not deleted. Pack files are content-versioned,
  reused only after verifying identical content, and require write access.
- Existing-application assessment backfill is System Manager, POST-only, dry-run
  by default and is not called automatically during migration. Read-only users
  cannot create persistent pack attachments. Non-signing assessment is excluded
  from signing readiness. Existing print-only UI is not reactivated as e-signing.
- Eight template records now cover six legal forms, withdrawn REQ and the internal
  assessment; canonical format names are mapped by code. Existing Form 1/2
  Director-signature differences remain held for the execution-process decision.

## 7. Interest, late charges and settlement

Monthly straight-line release, arrears pause/catch-up and periodic non-compounding
late charges remain app extensions. Jobs are scoped to linked, non-imported HP
customer loans. Grace is strictly beyond seven days; stored DPD 90 stops penalties.
Locks serialize tag checks. Historical missed-date charging is NOT automatically
backfilled from today's outstanding amounts.

A booked rebate is not deducted again in a later quote, and the monthly interest
job does not release rebated interest while settlement payment is pending. The
Rule-of-78 date rule itself was not changed. Deferred balances come from tagged
journals/adjustments. Native write-off remains native; a settlement write-off does
not trigger a second full-loan deferred clearance. Waiver/closure income remains
the current net-of-waiver design pending Finance's decision.

## 8. Proposed configuration, not applied site changes

- Full Company/Product field diffs are included in master previews. Existing HP-STD,
  supplier/item company mappings are reconciled rather than silently skipped.
  BSP payment bank is explicitly number 1201, not the first Bank account found.
- A native **Flexi HP Demand Offset Order** avoids modifying a shared standard
  order. Company changes and group/mapping validity still need saved-site proof.
- `check_setup` checks required accounts/types, fields, masters, defaults and exact
  desired setup; its result explicitly does not certify native acceptance.
- Assessment permlevel 1 is retained in the proposed role matrix. Preview shows
  native permissions if no Custom DocPerm applies. There is no delete/share grant;
  export is limited to designated reporting/finance roles. No permission apply or
  user assignment was run. Effective roles still require independent review/tests.

### Proposed chart: 111 accounts (requires explicit account-batch approval)

The original importer file had 107 rows and lacked four mandatory native account
types. The candidate adds:

| Number | Name | Type | Parent |
|---|---|---|---|
| 1196 | Stock - Native Compatibility | Stock | Current Assets |
| 5003 | Cost of Goods Sold - Native Compatibility | Cost of Goods Sold | Expenses |
| 5004 | Depreciation Expense | Depreciation | Expenses |
| 5005 | Stock Adjustment - Native Compatibility | Stock Adjustment | Expenses |

It restores 2008 Payable, 2009 Bank and 1610 Receivable. HP Product remains
non-stock; compatibility accounts do not create stock transactions. Isolated
ERPNext 16.55.0 importer validation/tree helpers accept the 111-row, five-root
file. Native Account creation/posting was not tested. No chart was imported.
The importer replaces accounts and related mappings/templates; replacing devcom's
96 existing accounts requires backup and explicit deletion/replacement approval.
Do not overlay the existing 2310 TDS account with VAT by account number alone.

## 9. Required remaining proof and decisions

Passing repository tests is not finalising the app. The NPA setting decision is
resolved; the co-applicant/document-process decisions are implemented in the dev4 candidate below, but not site-accepted. Confirm settlement
fees/month counting, waiver presentation, rate choices, repossession and any
sanctioned-limit basis affected by gross Loan amounts. Twice-monthly and legacy
portfolio migration are not silently implemented.

On separately approved devcom batches: clean install/migrate with full fixture
checks; Company/COA and master dry runs; independent permission review and real
user tests; all eight prototype checks plus arrears/cure/settlement/write-off/
waiver/CHD invoice scenarios; public application, co-applicant and every legal
print/signed pack; scheduler and month-end reconciliations. Verify the LPO approval
handoff actually submits the intended native PO, and CHD invoices have no VAT;
these native document/approval paths are not certified by the current unit tests.
Outbound test notifications must be reviewed before any workflow batch can send
mail. Migrate is a Frappe Cloud dashboard action, never a REST action.

The test suite covers pure logic, mocked document/database operations, selected
pinned native snippets and Node-executed previews. No real ledger, actual native
submit/cancel cycle, PDF render, role login, scheduler run or migration was observed.
Never deploy this candidate merely because the repository tests are green.


## 10. Confirmed individual consent, linked profiles and paper execution (dev4)

The user confirmed this process and requested finishing this repository action
and stopping. No installation, migration, configuration, notification or financial
transaction is authorised by this code delivery. The existing implementation plan
remains the single configuration/approval register; no extra settings manual is needed.

### Native first, narrow extensions

- **Co-applicant:** native, non-submittable `Co-Applicant Profile Review` workflow:
  Draft → Initiated → Under Review → Pending Approval → Approved, with rejection/
  resubmission and Loan Manager reopening. It has no loan-amount conditions, financial
  transition tasks, Loan, disbursement or PO creation. Both legacy co-applicant PO
  scripts are disabled. The primary workflow record is unchanged. Profile changes
  to fingerprinted identity, employment, income/expense/obligation inputs or consent
  require renewed review before facility approval. KYC completion is required for
  profile assessment/approval. No permissions have been applied.
- **Individual CIRA:** retain the existing client acknowledgement/signature inputs.
  On save, capture server time and consent/name/wording fingerprint. Require this
  consent before KYC initiation (and public form submission), for each linked
  individual, not one joint signature. Historical unknown capture times are not
  fabricated: obtain fresh consent. PNG data is restricted to inline captures;
  remote signature URLs are rejected. This is capture evidence, not independent
  identity verification.
- **CIRA PDF:** explicit Desk action **Generate Individual CIRA PDF** for a saved
  application/profile, using the native renderer and a private content-versioned
  File. It shows recorded capture time, not application date. Original files remain.
  The legacy auto-attach script is disabled. PDF creation is explicit, not automatic
  on every save; native PDF-engine failure must be reported, not disguised as PDF.
- **After KYC:** Desk **Generate Assessment PDF** renders and attaches **Application
  Print WIP**. If native PDF rendering is unavailable, staff may print the native
  format to PDF, then **Record Printed Assessment PDF**, explicitly attesting it is
  current and post-KYC. Private attachment ownership, parsed PDF, SHA-256 content
  and input fingerprint are checked. Changed source inputs or file content block
  subsequent approval/compliance until the current assessment is recorded again.
- **Paper contracts:** keep existing print/pack actions. During **Contract Signing**,
  **Record Wet-signed Scan** uploads private PDF evidence and declared customer/
  co-hirer and manager dates. Staff attest that every required individual signature
  is present and customers signed first. Preserve each form's existing manager-
  signature requirements; do not add blanket boxes to statutory forms.
- **Manager review:** Finance Manager/Director **Verify Uploaded Contract Pack**
  checks all six legal documents. This records review, not an electronic signature.
  Dates are date-only: same-day order depends on the human attestation, not invented
  timestamps. Superseding completed evidence requires manager role, explicit flag
  and reason; earlier files are retained. Application and direct-child save gates
  prevent ordinary API/form writes from inventing verification metadata.
- **Compliance/disbursement:** require current individual consent, approved linked
  profiles, current assessment, and verified six-document evidence. Recheck at
  disbursement, including file content and contract/template fingerprints.
- **Digital legal contracts:** disabled in retained Python and safe-exec mutation
  endpoints, not merely hidden in the UI. Await lawyer advice and separate approval.
  Digital individual CIRA capture remains enabled. No Fiji admissibility opinion is made.

The five new POST-only evidence APIs are in `comfort_homes.document_controls`:
`generate_cira_pdf`, `generate_assessment_pdf`, `record_assessment_pdf`,
`record_signed_scan`, `verify_contract_pack`. They reuse native Workflow,
Print Format, File, Version and FileUploader rather than introducing another
custom DocType. Document controls run after origination finalisation on save/submit.

### Local verification and limits

The complete suite passes **173 tests + 28 subtests**, no skips. New coverage includes
consent timestamps/reuse/history, individual names, profile/KYC review, protected
proof metadata, private attachment ownership, real pypdf parsing of synthetic
valid/corrupt/empty/encrypted/201-page files, scan dates/order, stale evidence,
separate upload/manager verification, retained digital-signing denial, lifecycle
source wiring and Node-executed Desk controls/private-uploader configuration.
PDF rendering itself is mocked; parsing is real. Native reference reads include
Frappe v16.56.0 Workflow validation and FileUploader properties.

One inherited profile rejection condition still referenced loan amount; the new
regression exposed it and the copied profile conditions were removed, while the
primary workflow remains unchanged. The final-control hook is deliberately after
flat calculation to avoid fingerprinting native overwritten totals.

Python compilation, JavaScript syntax and whitespace checks pass. A fresh-base
patch verification and credential-value scan accompany the exported evidence.
No full Frappe lifecycle, native saved workflow, migration, generated PDF layout,
public-form/browser session, effective role or ledger has been tested here. No
independent second-agent ledger/permission review occurred. These remain mandatory
before their separately approved site batches. No remote push has occurred.

### Final comparison for this action

Origination remains net financed **720.98**, term charges **158.62**, repayment
balance **879.60**, gross contract **1,049.60**, with a separate unchanged historic
879.59 reference test. Native NPA **89**, penalty stop **90**, 111-row proposed chart
and account-number invariants remain covered by passing tests. No ledger claim
is made. LPO approval/submission handoff, site accounts/masters and full acceptance
are not completed by this document-process action.


## 11. Fresh-install permission correction (dev5)

The user's devcom install log exposed a dev4 fixture defect: Co-Applicant was
non-submittable but its embedded DocPerm rows still granted Submit to Loan
Manager, Employee and Loan LOS User, and Amend to Loan Manager and Employee.
Frappe v16.56.0 rejects these grants during fixture import, before the later
explicit permission-setup action can run. The previous 173-test delivery did
not exercise this native schema-permission validation.

The correction keeps the approved non-submittable linked-profile design and
replaces only this fixture's permissions with the existing hp_permissions
Co-Applicant MATRIX and ASSESSMENT grants (nine roles at each of levels 0 and 1).
No Submit/Cancel/Amend/Delete/Share/Email/Import rights remain; Employee is removed.
Read/write/create, assessment-level access and reporting exports match the
existing approved matrix. This does not apply Custom DocPerm or assign real users.
Other fields, workflow transitions, calculations and the chart are unchanged.

The uploaded main snapshot bc16a0ae6263645e224de2322d936caf4e1a10da also contained
obsolete workflow.json alongside zz_workflow.json. The untouched checkout ran
172 passed, 28 subtests passed and one failure: test_dependencies_precede_workflow.
The primary workflow records in the two files were exactly equal. Remove the
obsolete duplicate so each workflow imports once, from zz_workflow.json after
its task dependencies. The primary workflow content remains unchanged.

Verification: 183 tests and 28 subtests pass, no skips. Ten new cases compare
both permission levels to the approved matrix, scan all non-submittable schema
fixtures, execute the pinned native validate_permissions function against the
corrected in-memory profile, reproduce each original invalid Submit/Amend grant,
check the reference digest and enforce unique workflow import. Native source
provenance and SHA-256 are bundled beside the test-only extracted function.
Python compilation and git diff --check pass. Financial/origination and chart
invariant tests are rerun as the final acceptance comparison for this batch.

LIMITS: isolated native validation uses an Administrator context, not a full
Frappe installation, migration, database or effective-role test. No independent
second-agent permission review was available. Initial HTTP 403 was caused by including the credential-file API label in the
Authorization value; corrected parsing authenticates with HTTP 200. Read-only
checks show Frappe/ERPNext 16.60.0, Lending 16.8.1, app code dev4, 96 accounts,
zero GL entries and no Co-Applicant DocType (404). App version reporting is not
proof of completed installation. Frappe 16.60.0 validate_permissions is identical
to the pinned 16.56.0 reference; source hash is recorded. Partial-install state
requires fuller inspection before any retry. No devcom or UAT
writes, install retry, migration, configuration or transaction occurred. A real
successful install remains mandatory; this corrects the observed blocker, not
proof that no subsequent install issue exists. Installation/deployment requires
a separately approved batch and Frappe Cloud logs. Do not force or delete partial
records merely to bypass errors.


## 12. Consolidated complete-source installation review (dev6)

The user requests a complete ZIP suitable for copying all repository files, not
incremental patches. This snapshot is based on main
`4c3769291b1cb152b6deac2eca2c3fd368ffb0d7` (the user's merged permission fix).
It includes the complete custom app, not only the changed files. No main push,
site write, deployment, retry, migration or financial transaction is performed.
The user's direct main updates do not authorise an agent push to main.

### Whole-app inventory and boundaries

| Ships in this app | Must be configured/proved separately on devcom |
|---|---|
| Profile/address/child-table schema bootstrap; three schema fixtures including Co-Applicant and bank reconciliation | Actual native schema import/migration and successful app completion log |
| 208 Custom Fields, 11 roles, 15 Workflow States, 18 actions, two task groups, two real workflows | Effective role assignments and independent maker/checker review |
| 30 Server Scripts, 11 Client Scripts, public application form, browser assets | Server-script enablement/bench policy, public intake and permission sessions |
| Eight Print Formats and eight templates (six legal, assessment, withdrawn REQ) | Native PDF engine/layout, real individual CIRA and signed-pack testing |
| 111-account proposed chart; master/permission dry-run helpers | Explicit backup/chart-replacement approval, Company defaults, bank/modes/locations/employers and real users |
| Gross booking, zero-rate Loan extension, deposit checks, interest/penalty/settlement/write-off code | All eight native acceptance checks plus additional scenarios and penny-level GL reconciliation |
| HP Customer Statement and bank import/retry code | Report-to-GL comparison, write-off/waiver/closure scenarios and saved imports |
| Registered daily/monthly jobs | Scheduler enabled status and observed job runs/reconciliations |

These counts describe packaged configuration, not records already created on a
site. Co-Applicant remains a non-submittable reviewed profile, not a second loan.
Preserve original origination calculations/rate list and primary workflow.

### Defects found and changes in this consolidated snapshot

1. **Copy-over upload retained obsolete workflow.json.** The new main baseline
   runs 181 passed + 28 subtests and two failed duplicate/order assertions. The
   correct permission fix is already present. Instead of requiring deletion,
   ship workflow.json as an empty `[]` compatibility fixture. Overwriting that
   file neutralises the duplicate. Both real workflow records remain byte-identical
   in zz_workflow.json, following roles/states/actions/scripts/task groups.
2. **Post-install timing.** Native Frappe 16.60.0 install_app executes after_install
   before sync_fixtures, and after_sync afterward. Full dependent setup now runs
   only in setup_after_sync, bound to after_sync and after_migrate. It runs profile
   field/profile configuration before form/print setup. Bootstrap remains before
   install/migrate. Native installer/migrator reference extracts and hashes are
   included; native code itself is not modified.
3. **Suppressed print/template failures.** Remove broad exception swallowing from
   print sync and assessment-template insertion. Errors now reach the install/
   migrate log. Remove the normalizer's explicit commit; native installer commit
   boundaries still exist, so this is NOT an atomic-install guarantee. No automatic
   business masters, financial entries or Custom DocPerm application is added.
4. **Website asset path.** Use the canonical /assets/comfort_homes/... URL for the
   website branch-filter script, matching the actual packaged asset.
5. **Fixture export completeness.** Include all nine imported geography Property
   Setters in the existing export filter, rather than losing them at the next export.
6. **Signature attribution.** Application Print WIP and standalone assessment HTML
   no longer substitute the individual CIRA signature when a witness signature is
   absent. Leave the witness area blank; keep the actual CIRA form unchanged.
7. **Old README contradicted gross HP.** Replace its native-daily-interest/deposit-
   Sales-Invoice model with the approved gross model and copy-based instructions.
8. **Copy verification.** tools/verify_release.py and RELEASE_MANIFEST.json detect
   missing/changed source and unexpected app code/fixtures after copying. They are
   local read-only integrity checks, not installation commands or digital signatures.
   No deletions are needed when overwriting the specifically reviewed main base.

### Review/test evidence

- Full source suite: **203 tests + 28 subtests pass**, no skips. Twenty new cases
  cover complete schema/link/table closure, field/fixture identity, public form
  fields, workflow dependencies/roles, export preservation, hook order/callables,
  post-sync sequencing, failure propagation, template syntax and safe copy verification.
- Read-only devcom native metadata supplies an explicitly labelled schema-only
  reference (Frappe/ERPNext 16.60.0, Lending 16.8.1). No missing app-owned Link/Table
  targets or public web-form fields are found. Native app internal dependencies
  remain the native apps' responsibility. This is not a native schema save.
- Native permission validator accepts all non-table schema fixtures in isolation.
  It uses Administrator context, not effective-role acceptance. No independent
  second-agent permission/ledger review is available in this session.
- All **30** Server Script sources parse as Python; **23** shipped standalone/report/
  fixture browser scripts pass Node syntax checks; **15** fixture/standalone HTML
  templates pass Jinja parsing. This does NOT render PDFs or certify safe-exec execution.
- Python compilation and whitespace checks pass. The declared Flit build is exercised
  for both source distribution and wheel; all packaged app runtime source/assets,
  reports, fixtures, chart and print sources are checked in the wheel. Local tooling
  initially installed Flit 4; using the project's declared Flit >=3.4,<4 resolves
  the local build-tool mismatch without changing app dependency requirements.
- Local runtime is Python 3.12.15 / Node 24.22.1; this is NOT Frappe's Python 3.14
  bench runtime. Do not infer native installation from a successful wheel build.
- The complete ZIP is copied over a clean checkout of the reviewed main base and
  source-manifest checked, then the full suite is rerun. All source ZIP files are
  verified against the final local commit. Supplied devcom key/secret values are
  scanned against deliverables; this is not a full Git-history security audit.
- Final worked comparison: original financial modules/chart unchanged; approved
  origination net financed 720.98, charges 158.62, Loan 879.60, gross 1049.60;
  historical fixed 879.59 case remains distinct. Account chart stays 111 rows.

### Not silently completed

A complete source ZIP is not a declaration that the whole implementation is
finished. The primary workflow's LPO approval-to-native-PO-submission handoff is
still an open code/acceptance item; this consolidation does not invent a submission
action. Full financial/report/write-off/waiver and PDF/browser acceptance remain
outstanding. Finance/legal decisions on settlement fees/month counting, penalty
waiver presentation and repossession remain open; digital contract signing stays
disabled. No legacy portfolio migration is added.

The last observed devcom state was app code dev4 reported with Co-Applicant 404,
96 accounts and zero GL entries. App listing alone cannot establish install
completion because native registration commits precede fixtures. Before any retry,
read current state and Cloud logs; use a separately approved install/recovery batch,
not force-install or record deletion. Review outbound mail and bench isolation from
UAT. After successful installation, Company/COA/master/permission and test-data
batches remain separately approved. The next deliverable is this full ZIP for the
user's copy/upload and source verification, not another patch to learn.


## 13. Consolidated acceptance rework (dev7)

See **CONSOLIDATED_REWORK_DEV7.md** for the complete investigation, changes, native boundaries and approval-gated deployment/test sequence. Native Submit and Create controls were observed in the supplied browser session, without clicking. The confirmed app gap is the application-to-financial-record handoff and progression guards.

Assessment rendering now uses a separate document and checks saved evidence. Combined signed-pack recording is one atomic six-row action with separate manager review; same-day signing is retained. Missing scans are named. Native LPO approval task submits the exact linked PO with native permissions. Gates cover both ordinary and update-after-submit application transitions. Reclass uses actual native GL date; no native past-date override. Telemetry remains an external native finding with an optional, separately approved setting-based opt-out.

Verified acceptance basis is unchanged:720.98 financed,158.62 charges,879.60 repayment balance,1049.60 gross,73.30x12,806.30 after one payment,145.40 deferred after13.22 release,42.71 month-six rebate. These are expected amounts and local regressions, not newly posted dev7 site entries. Existing devcom GL remains the two170 deposit receipts only at the investigation boundary.

111-account source chart incorporates the user-approved1195 Expense/5200 change. Approved operational reference metadata is bundled outside fixtures; no automatic import/role assignment or financial posting occurs. Main workflow email flag stays0 for the test rollout; states15/transitions20 preserved except intentional LPO approval task bindings. Current role matrix is not applied or independently accepted.
