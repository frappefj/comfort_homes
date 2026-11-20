# Flexi complete-source candidate — 0.0.7.dev8

**Source candidate for review, not a clean-site, financial or production acceptance certificate.**
Implementation target: devcom.syd.frappe.cloud only. Comfort UAT remains strictly read-only.
Pinned upstream baseline: `frappefj/comfort_homes` commit `6bcb637df3f732c98195db37698339c61c6f2c6d`, version dev7. All 118 baseline manifest hashes and its GitHub manifest blob were verified before work. A new local Git repository was established from that authenticated complete source; its local baseline commit is NOT the upstream Git commit or an ancestry claim.

No site request, submission, configuration/permission change, transaction correction, communication, financial job, scheduler change, push, deployment or migration occurred while producing this candidate. There are no credentials, private customer files, bank imports, raw site snapshots, venv, Git metadata or build caches in the ZIP.

## 1. Included corrections

### Intake

- One app-owned form-submit coordinator, covering physical Submit and Enter, replaces both recursive click handlers. Native Web Form save/accept and confirmation remain authoritative. No redirect or fake Submitted panel.
- Synchronous capture prevents browser form navigation before asynchronous checks. Locking spans actual native request completion, not the synchronous `save()` return value. No global replacement of frappe.call.
- Explicit duplicate/validation/lookup rejection preserves inputs. Lost/uncertain save response prevents blind retry and tells staff to inspect saved state. A successful save with broken confirmation is not offered for resubmission.
- Input changes during duplicate lookup invalidate that approval. Double/concurrent submit is held. Upload-in-progress flags are checked; real file upload/network and zem/Guest full-form testing remain mandatory.
- Native grid ownership is respected: frm.doc wins where present, otherwise grid.df.data. Meaningful zero-price goods and attachment/description values remain; seeded blank rows are removed without regenerating child identities.
- The final required/CIRA/age/spouse checks remain. The pure financial engine, rounding, affordability/approval basis and product/rate choices are not changed.
- Native Autocomplete receives `{value: record ID, label: display}` from a fixed, guest-safe Fiji-master-only endpoint. City/Suburb are parent filtered. Server validation prevents cross-parent links. The obsolete hard-coded label hierarchy no longer drives the controls. Real supported-browser and co-applicant intake acceptance remains open.
- The public branch asset checks that Desk `frappe.ui.form.on` is callable, not merely that the object exists.
- Script upgrade is idempotent and applies to the fixture plus retained database script on post-sync; the temporary database deferred-click fix is superseded by the full source implementation. No new companion handler is simply layered on top of the old re-click logic.

### Documents and action clarity

- Generate/Record Assessment is available after KYC in Initiated, Under Review, Pending Approval, Approved and Contract Signing as applicable. Missing/invalid evidence still blocks server gates.
- Existing dev7 separate-render and persistence checks are preserved. New read-only evidence status compares current consent/source fingerprints and removes the redundant CIRA signing action only for clean, saved, valid consent. A changed/dirty record is not falsely labelled current.
- The optional Individual Forms menu is removed from normal combined-pack UI. Combined six-form upload, separate manager review, supersession reason/history and native workflow Actions remain. Existing per-form server API remains for a future separately approved exception UI; this release does not weaken server controls.
- The misleading Application flat-rate field/assessment metadata label is changed to Flat Contractual Rate (% p.a.). No statutory wording or signature requirement is changed; all actual PDFs still need visual/legal review.

### Independent service debts

- New fee path uses ordinary Sales Invoice / native invoice-allocated Payment Entry, on 1120, with custom reference-only Loan/Application fields. No Loan update, Loan Demand, Loan Repayment, LBA, charge-offset insertion or communication is created by this code.
- `Company.custom_hp_independent_fees_enabled` defaults OFF. Keep it OFF until billing policy, exact native-hook isolation prototype and permission/GL acceptance are approved. Deploying code is NOT fee-enablement approval.
- Invoice Date (`posting_date`) equals Value Date. Actual service event date/evidence remains separate; it cannot be later than Invoice Date.
- Initial scope: one reviewed event, one line, quantity1, SMS2.50/Call2.50/Visit25.00 VIP, 12.5% inclusive output VAT. Exact-cent invoice rounding disabled. SMS/call4011, visit4008, VAT2310 and receivable1120. Existing non-default native tax template is explicitly selected/populated on this path; no global Sales Tax Rule, tax default or Loan Product is altered.
- Narrow backend validation covers manual/API drafts/submits: native loan/disbursement/repayment/partner/voucher trigger fields prohibited, exact Customer/Company/Application binding, date/rate/tax/total checks, event/evidence required, customer lock plus unique event field. Draft/submitted/cancelled event references cannot be silently reused.
- Native Sales Invoice submission additionally requires Finance Manager/Director and chargeability attestation. The precise attempt-versus-success/caps/legal authority policy is not invented. No unattended billing enabled.
- The Loan action prepares an unsaved native invoice only. Complete actual event details then obtain review; standard Accounts native receipt creation handles payment. Fee receipt guards reject EMI/deposit/mixed allocations, deductions and unallocated excess.
- Independent credit notes are limited to matching independent source invoices and a new event reference; they must not correct legacy invoices. Native credit/refund behaviour still needs runtime acceptance. Automatic refunds and waiver policy are not implemented.
- Native Lending hooks still execute. Blank native loan-link source conditions are not proof of zero runtime side effects, particularly NPA/suspense/return hooks and downstream jobs. The feature stays OFF pending these tests.
- **ACC-SINV-2026-00001/00002 and their old Charges demands are preserved unchanged.** New hooks do not iterate, relink, cancel, credit or repair historical invoices on installation. Do not use those known bad invoices as clean prototypes.
- No independent-fee Loan-closure block is added. Obtain explicit policy before coupling a separate debt to Loan closure.

### Purchasing and reporting

- Existing workflow topology15states/20transitions remains. Approve LPO after creating a DRAFT PO is intentional. Both existing approver tasks remain; synchronous native task ordering is preserved.
- Generate LPO now uses one registered app workflow method with application lock, stable exact application link, full-disbursement gate, exact CHD/FJD/cash-price validation and native create permission. Native insert, no ignore_permissions or remarks-substring deduplication. A matching legacy remarks-only PO causes a review stop, not a second PO or silent relink. Old Workflow Task generator is disabled to leave one owner; the previously disabled after-submit/co-applicant generators stay disabled.
- Approval still submits the exact intended PO with native permissions; no financial or workflow state is patched. A submitted-PO/pending-application mismatch is displayed as an exception, never auto-repaired. Native PO status determines Draft versus Submitted links.
- New standard **Flexi Manager Overview** starts with permission-visible Loans and unlinked applications, not POs. It separates own stored DPD from borrower-wide NPA, Loan balance, independent fee debt, preserved legacy fee exceptions and recorded customer position. PO status/CHD outstanding are read separately. No summed DPD or overlapping category chart.
- Child records use native permission-filtered reads. Inaccessible balances are blank, not zero. This is a current-state overview, not a historical portfolio/payoff engine; stale DPD, supplier statement cycle and unproved sent/delivery evidence are explicitly labelled.
- HP Customer Statement retains the contractual ledger, dates origination rows by actual submitted gross-booking journal rather than application creation, and shows independent/legacy invoice debt in separate summary totals. Invoice outstanding is read once, never added again as Loan Demands.
- Detailed fee receipt-allocation timeline and historical as-of reconstruction remain open; invoice drill-down and the native accounting ledger are the allocation evidence. No historical GL is invented from value dates.

### Rollout portability

- Explicit `comfort_homes.hp_lending.hp_rollout.setup_rollout` is POST-only, System Manager, **dry-run by default**. It is NOT called by install/migrate. `include_fees=1` adds catalogue/template checks; `include_company=1` adds guarded non-inventory settings. It creates missing native masters only, stops on conflicts, serializes apply and leaves the request transaction to Frappe.
- Required exact Loan Document Types: Identity Document, Latest Payslip, Bank Statement, Proof of Address, Supplier Quotation, Other Supporting Document. Existing ID is not renamed/deleted.
- Fee catalogue/template and Company perpetual/provisional-nonstock settings are previewable; inventory flags are never automatically disabled if a stock ledger exists. No chart replacement or dummy stock receipt/account is added.
- `check_setup` now includes upload masters, non-inventory requirements and both LPO task bindings. When independent fees are enabled, catalogue conflicts/missing configuration are reported. `native_acceptance_tested` remains false.
- Approved operational references stay outside fixtures. Existing111accounts,1195Expense/5200,actualFlexi suffix,HQ/branches/locations/FYs,Finance banking HOLD,native penalty0,NPA89/custom stop90 and email suppression are retained. No roles/users/masters/transactions silently applied during migration.

## 2. Evidence from this build

See `VERIFICATION_DEV8.json` for final observed counts, syntax/package results and limitations. The unchanged baseline passed241tests+28subtests. Final candidate:290tests+28subtests, no skips;17local Chromium scenarios. The expanded suite and local browser results must not be substituted for actual Frappebench, fresh install, native hooks, PDF rendering, effective-role or GL acceptance.

Real local Chromium testing uses `tests/webform_submit_harness.html`, a synthetic native-save/XHR model, not the deployed form. It covers physical click, Enter, double/concurrent submit, invalid/duplicate/lookup failure, no-request path, explicit server rejection/retry, uncertain lost-response blocking, input change during lookup and both native-grid ownership modes. It does NOT upload real supporting documents or reproduce zem's exact browser session.

Four financial modules (gross booking, interest, penalties, settlement/write-off) are hash-identical to dev7. Their outstanding native tests remain outstanding. No scheduler order/backfill/classification change is hidden in this candidate.

## 3. Every earlier register item is retained

| Register ID | dev8 source disposition / acceptance still required |
|---|---|
| WF-SUBMIT-01 | Unified coordinator and failure/retry tests; recurring live incident remains open until actual devcom matrix passes. |
| WF-GRID-01 | Real grid data ownership, meaningful-row preservation, ID-based locations/server hierarchy guards; full co-applicant/addresses/runtime calculations pending. |
| WF-PUBLIC-01 | Callable Desk API guard corrected/tested locally. |
| CFG-DOC-TYPE-01 | Create-missing helper/reference/checks; approved site execution still separate. |
| DOC-ASSESS-UI-01 | Stage visibility/stale hint fixed; dev7 persistence kept; actual visual review pending. |
| UI-CIRA-PACK-01 | Current-consent hide and normal combined-pack-only actions; genuine renewal/review retained. |
| FEE-ARCH-01 | Isolated native SI/PE source path included, OFF by default; native hook/queued-effect isolation is a release gate. |
| FEE-CONFIG-01 | Explicit scoped template defaults, exact dates/rates/totals, portable catalogue preview; native GL/credit/receipt pending. |
| FEE-EXCEPTIONS-01 | Legacy invoices/demands neither corrected nor double-counted; separate visible exception balance. |
| FEE-ACTIONS-01 | Single-Loan prepare-draft action included. Worklist bulk/event-record/communications workflow held for chargeability/caps/authority policy; no messages sent. |
| MGR-VIEW-01 | Thin current-state report included; native role/totals/performance and missing-record acceptance pending. Supplier statement and delivery evidence not invented. |
| REPORT-STMT-01 | Actual-booking dates, flat label, separate fee summaries; detailed allocation timeline/historical as-of and lifecycle report reconciliation remain open. |
| CFG-STOCK-01 | Native flags preview/check and stock-ledger conflict guard; no site configuration applied. |
| PROC-DELIVERY-01 | Draft/submit clarity; delivery/Close operational policy and CHD exact30-day statement cycle remain pending, no fake PR/date edits. |
| WF-FIN-01 | dev7 gates/approval kept; generation boundary hardened; both real-role reachable workflows and after-submit rollback pending. |
| PENALTY-ORDER-01 | Financial code unchanged. Stored-DPD90/job ordering and non-replay limitation documented; no historical script. |
| LBA-COLLECT-01 | Native2015liability acceptance/actual collection/payoff/closure remain unperformed, not patched around. |
| INTEREST-01 | Financial code unchanged. Actual release/pause/cure/catch-up/final-cent/month-end and date/NPA scope tests remain. |
| LIFECYCLE-01 | Weekly and complete benchmarkmonthly, Rule78,writeoff,waiver,closure,month-end/supplierpayment still require exact approved batches. |
| TEL-01 | Native duplicate milestone warning retained as upstream issue; no monkeypatch/milestone deletion or financial retry. |
| PERM-01 | No effective permission changes/user assignments. Single-role positive/negative/noDelete/independent review still mandatory. |
| DOC-VISUAL-01 | All prints/real pack/co-applicant/legal visual evidence remains a gate; hash/parse is not legal verification. |
| CFG-ROLLOUT-01 | Complete source/manifest/build/checks and explicit setup stages supplied. Fresh install/repeat migrate/backup/release/role/native acceptance still not proved. |

## 4. Review/upload and later rollout

1. Extract into a **new empty folder**. Repository-root files (README,pyproject,etc.) and `comfort_homes/` are at ZIP root. No extra nested app folder and no `.git`.
2. Verify locally: `python tools/verify_release.py .`. It must report versiondev8,oktrue. Run `python -m pytest comfort_homes/tests` and inspect the diff against pinned main before merging. Prefer a review branch/PR; the user merges. The agent did not push main or any branch.
3. If main is connected to Cloud automatic deployment, **do not push/merge to it until deployment is separately approved and devcom/UAT bench isolation confirmed**. A source review approval must not accidentally deploy to UAT.
4. Complete-copy overlay is only suitable against the inspected dev7 file set. The verifier catches unexpected app source/fixtures; stop rather than silently delete unknown files. Do not include local Git metadata, venv,caches,privatefiles or credentials in the push.
5. Before devcom deployment: recoverable backup, exact commit/build review, current-state read-only snapshot, notification review and explicit deployment/migration approval. Cloud Migrate is a dashboard action. Inspect the web-form fixture/script, schema, workflow methods/taskbindings and served assets. No finance job/setup/permissions automatically applied.
6. Then separately preview `setup_rollout(dry_run=1,include_fees=1,include_company=1)`. Approve the exact conflict-free batch before apply. Read/create permissions must be native; no blanket grant. Keep independent billing OFF until policy and isolation tests are approved.
7. Repeat preview and `check_setup`. Preserve all existing test transactions/bad invoices, keep schedulers on, do not rewind DPD/NPA,run historical script,trigger jobs or submit diagnostics without a named approved batch.
8. Run full real intake matrix zem/intended roles/Guest,attachments/co-applicant/slowfailure/retry,privateevidence/currentassessment,realPDFs; then both LPO approvers and scoped financial tests. Stop on findings. No existing state rewind to manufacture a test.
9. Independentfees prototype: new isolated2.50/25.00SI,allocatedPE/credit/retry/NPA/queued effects; compare Loan monetary/paid fields,demands,schedules,DPD/NPA,status,interest,penalties. Account for naturally running scheduler changes; never pause schedulers or conflate time effects with fee effects. No EMI diversion.
10. Reconcile1101/2013/1180/2015/2010and independent1120. Complete roles/independentreview/hand-over backup/open decisions. Rotate project API keys,secrets and tokens at project end.

## 5. Approved acceptance basis / preserved holds

Goods849.00,deposit170.00,admin16.98,documentation25.00(22.22+2.78VAT),financed720.98,term158.62,Loan879.60,gross1049.60. Monthly73.30x12;fortnightly33.83x25+33.85;weekly16.92x51+16.68. Firstmonthlypayment leaves806.30;13.22release leaves145.40deferred;6of12elapsed Rule78rebate42.71. Historical879.59vectors remain explicitly separate.

Keep111-account chart,2010Receivable liability,1195Expense/5200,0%Loans/contractflat12/19/22/25/27,7daystrictgrace,custom2.5%monthly/stop90,nativepenalty0,NPA89,no1104transfer,net-of-waiverclosureincome,monthlyfull-disbursementmonthinterest/catch-upatnextmonthlyrun,noinitialsettlementfee. FinancebankingHOLD;manualrepossessiononly;digitallegalcontractsigningdisabled;individualCIRAretained. Exactrepossessionaccounting,CHDstatementcycle,deliveryhandling,feechargeability/caps/waiversand independentfeeclosurepolicy are not invented.
