# Comfort Homes — Flexi gross hire purchase

**Consolidated candidate: 0.0.7.dev6.** Complete source snapshot, not a patch.
For Frappe 16, ERPNext 16 and Frappe Lending 16. Implementation target:
**devcom.syd.frappe.cloud**. Comfort UAT is strictly read-only.

This is a repository-tested development candidate. A successful devcom install,
native workflow/PDF/permissions tests and ledger acceptance are still required.
It is **not approved for live disbursement**.

## What is included

| Area | Implementation |
|---|---|
| Origination | Existing application calculations, rate choices and approval basis preserved |
| Co-applicant | Reviewed/approved linked profile; no separate Loan, disbursement or PO |
| Loan | Gross post-deposit repayment balance; zero servicing interest; flat contract rate retained |
| Deposit | Payment Entry, never a Sales Invoice |
| Gross booking | Native disbursement plus one tagged deposit/fees/VAT/deferred-interest journal |
| Interest and arrears | Straight-line release with overdue pause/catch-up; periodic non-compounding late charges |
| Settlement | Rule-of-78 rebate through native balance adjustment |
| Documents | Individual CIRA consent/PDF, post-KYC assessment, paper-contract scans and manager review |
| Digital legal contracts | Disabled pending lawyer advice and separate approval |
| Configuration | Portable schemas, fixtures, 111-account proposed chart, master and permission previews |
| Reporting/collections | HP Customer Statement and bank-repayment import/retry controls |
| Installation checks | Ordered fixtures, native permission regression tests, source and package integrity verification |

The complete implementation scope, actual evidence, unresolved decisions and
site acceptance limits are in **docs/BUILD_SPEC.md**. The shared implementation
plan remains the configuration/approval register; this README is not another
configuration manual. The obsolete daily-interest/deposit-invoice model in the
former README is superseded.

## Updating by copying files — no patch commands

1. Extract the **entire consolidated source ZIP** into a new folder.
2. Open that folder. Its top level contains this README, `pyproject.toml`,
   `RELEASE_MANIFEST.json`, `tools`, `docs`, and the `comfort_homes` package folder.
3. Copy **all of those contents**, retaining their nested folders, into your
   repository's existing top level. Do not add another wrapping folder inside
   the repository. Keep your local `.git` directory; no `.git` is included here.
4. Replace existing files when prompted. Include `workflow.json`: in this
   snapshot it is deliberately an empty `[]` compatibility file, so overwriting
   it neutralises the stale duplicate without a manual deletion or patch.
5. Commit/upload the complete snapshot using your usual Git application.
   A review branch is recommended. Send the resulting GitHub link/commit for
   source-manifest verification **before deploying**.

This copy method was checked against main
`4c3769291b1cb152b6deac2eca2c3fd368ffb0d7`. The verifier detects missing/changed
files and unexpected app code/fixtures that may survive an older overlay.
For a different local base, verify the result rather than assuming an overlay
is exact. We do not automatically delete unknown files.

A developer/agent can verify a copied checkout, without site access or changes:

```sh
python tools/verify_release.py
python -m pytest comfort_homes/tests -q
```

Tests use Python 3.12 locally, pytest 9, pypdf, Jinja2 and Node.js 24. Actual
Frappe/ERPNext 16.60.0 require their supported bench Python (currently 3.14),
provided by Frappe Cloud; a local Python build is not a Frappe runtime test.
Runtime PDF and spreadsheet libraries are already provided by Frappe; no native
app Python dependency is re-declared in this custom app.

## Installation/recovery comes next, under separate approval

Frappe Cloud must deploy the exact reviewed source to devcom only. We must first
inspect the failed installation state: Frappe registers installed-app metadata
before fixture import finishes, so an app version in the list is not proof that
installation completed. Do not force-install, uninstall or delete partial records
to get past an error.

Schema bootstrap runs before fixtures. Full app-owned profile/form/print setup
runs after fixture sync on install and at the end of migration. Setup errors
propagate to the deployment/migration log rather than being silently reported as
success. The empty legacy fixture contains no Workflow record; both real
workflows reside in `zz_workflow.json` after their task dependencies.

Company defaults, chart replacement, master creation, effective permissions,
real users, business locations and financial test transactions remain separate
approved batches. Master and permission functions default to dry run. App
installation itself imports app-owned configuration and is therefore a site
change requiring approval. Frappe Cloud Migrate is a dashboard action, not REST.
The primary workflow retains email alerts; outbound notifications must be reviewed
before any test application/workflow transitions. Scheduler registration does not
prove jobs have run.

## License

MIT. Native test-only source extracts retain their upstream provenance and use
the upstream MIT-licensed Frappe source; they are not modifications of native apps.
