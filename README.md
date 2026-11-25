# Comfort Homes — Flexi hire purchase on Frappe Lending

**Release 7.1 (0.0.7.1), "Lean and Clean" baseline.** For Frappe 16, ERPNext 16 and Frappe Lending 16
(tested against Lending 16.8.2). Target site: **devcom.syd.frappe.cloud**. Comfort UAT is read-only.

What changed in each build is in `comfort_homes/CHANGELOG.md`; the independent audit and its decision log are
kept outside the repository.

## How the app is organised

| Area | Where |
|---|---|
| Application workflow, assessment, approvals, re-approval on changed terms | `loan_application_workflow.py`, `hp_lending/hp_workflow.py`, `hp_lending/hp_approval.py`, fixtures |
| Customer documents, CIRA, signed contract pack | `document_controls.py`, `signed_pack.py`, print-format fixtures |
| Former site Server Scripts (customer creation, defaults, workflow task, web-form lookup) | `flexi_events.py` |
| Gross HP booking at disbursement | `hp_lending/hp_lifecycle.py` (pinned) |
| Interest release, late charges, Rule of 78 settlement | `hp_lending/hp_interest.py`, `hp_penalties.py`, `hp_settlement.py` (pinned) |
| Customer credit, receipt allocation, penalty re-test | `hp_lending/hp_credit.py`, `hp_penalty_retest.py` |
| Statements of account | `hp_lending/hp_statement.py`, print format "HP Statement of Account" |
| MYOB conversion and in-place schedule rebuild | `hp_lending/hp_conversion.py`, `hp_lending/hp_rebuild.py` |
| Reports | Portfolio Control, HP Conversion Check, HP Closure Review, HP GL Trace, HP Fee VAT Check, HP Statement Register |

Fixtures in `comfort_homes/fixtures/` are the source of truth for print formats, custom fields, workflows,
client scripts and the web form. There are no site-stored Server Scripts from 7.1 on.

## Deploying

1. Push the release branch and deploy it to devcom from Frappe Cloud, after a backup.
2. Run `bench migrate` (Frappe Cloud does this on deploy). The 7.1 patch removes the old Server Scripts and the
   two retired reports before fixtures sync.
3. Check: Portfolio Control opens; a new Loan Application saves and creates its customer; the public web form
   loads loan products; HP Conversion Check shows only real differences.

## Checks before release

```
python3 -m pytest -q comfort_homes/tests
python3 tools/verify_release.py
```

`verify_release.py` compares every file with `RELEASE_MANIFEST.json`; regenerate the manifest for each release.
