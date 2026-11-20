# Upload dev8 complete source safely

## What this is

Complete custom app source for Frappe/ERPNext16 and Lending16, version0.0.7.dev8, based on authenticated pinned dev7 source. This ZIP is not a patch. Everything at archive root belongs at repository root; preserve all subfolders. It contains no .git or credentials. `docs/CONSOLIDATED_REWORK_DEV8.md` contains the complete change register, preserved policies and pending tests.

## Your Git update

1. Extract to a new empty folder, not over an unrelated old app.
2. Run `python tools/verify_release.py .`; require `ok: true` and version0.0.7.dev8.
3. Compare against pinned baseline `6bcb637df3f732c98195db37698339c61c6f2c6d`. Prefer copying to a new review branch and inspecting the diff before merging to main. The agent did not push anything; the user owns merging.
4. If copying all files into your existing checkout, copy root files AND the comfort_homes/docs/tools folders. Preserve your local .git. The empty `fixtures/workflow.json` is intentional overwrite-safe compatibility; do not put an older workflow back.
5. Run tests/source/package verification on that exact copied tree. If verify_release reports an unexpected old app file, STOP and inspect; do not silently delete unknown files or upload a mixed-source overlay.
6. **Check Frappe Cloud automatic deployment before pushing main.** If a main push updates devcom or any bench shared with UAT, wait for explicit deployment approval and confirmed isolation. UAT must not change as a side effect of a Git push. Uploading source is not consent to deploy/migrate or run tests on either site.

## Changed-site impact to preview later

Migration imports new reference-only SI fields, a Company feature flag defaultOFF, a new read-only standard report, updated web-form/Desk scripts, flat-rate labels and native workflow method binding. The old Workflow Task PO generator is disabled and replaced by one app method; approval tasks stay native and synchronous. These are material fixture/UI changes, not only a version bump.

Migration DOES NOT call setup_rollout/setup_masters/permission apply, assign users, change banking, enable fee billing, repair legacy invoices, reset states, generate applications/POs/invoices/repayments or trigger financial jobs. Existing automatically scheduled jobs remain registered unchanged and must stay enabled.

An explicitly approved later setup batch can preview:

```python
comfort_homes.hp_lending.hp_rollout.setup_rollout(
    dry_run=1, include_fees=1, include_company=1
)
```

Call through the native approved server/Desk mechanism, not an invented REST migrate API. Review exact differences/conflicts, then approve apply separately. Required upload masters are explicit native setup, not automatically imported business records. Existing ID/Items/rates are never renamed/deleted/overwritten to get a clean preview.

Keep `custom_hp_independent_fees_enabled` OFF until the billing policy, native SI/PE/credit hooks, NPA/queued effects, permissions and report/GL isolation prototype pass under their separately named approval. Do not use Loan Repayment or native Sales Invoice.loan for the new office-fee debts.

## Minimum live acceptance after deployment approval

- Exact deployed Git SHA, successful Cloud Migrate, served scripts/fixtures, single submit owner and no public load error.
- Actual web-form full matrix: zem/intended officer/Guest if supported; attachments/co-applicant; click/Enter/slowlookup/doubleclick/errors/lostfeedback/retry; one record or visible retained-data failure.
- Stage-correct current assessment, consent renewal, private files, actual all-eight PDF visual review and six-form pack verification.
- Both real LPO approver roles on fresh reachable cases; exact one PO, no creation on refresh, native financial gates and role denials. No reset of old applications.
- New independent fee prototype: correct VAT/date/receivable, receipt/credit, no Loan/demand/schedule/paidfield/DPD/NPA/interest/penalty effects; legacybadinvoices untouched.
- Native2015LBA submit/collect/payoff/closure; actual interest/penalty/NPA boundaries and lifecycle/month-end controls; weekly and complete879.60workedcase.
- Real single-role permissions/noDelete, independent high-stakes review, recoverable backup/hand-over. Rotate project credentials at project end.

**Do not run the old historical-charge script, rewind DPD/NPA, pause schedulers, correct/cancel/amend bad invoices, send communications or create test submissions without the appropriate separate preview and approval.**
