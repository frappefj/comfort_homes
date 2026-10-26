"""Print format HTML synchronisation from app-owned standalone files.

Phase 2 of the co-applicant integration.  Standalone HTML files live in
``comfort_homes/print_formats/`` and are synced into their Print Format
database records on every ``bench migrate``.

Add to hooks.py → after_migrate:
    "comfort_homes.sync_print_formats.sync_print_format_html",

The fixture in hooks.py → fixtures still creates/owns the Print Format
record (name, doc_type, module, etc.).  This function only updates the
``html`` field content, keeping the template source editable and diffable
as normal HTML files under version control.
"""

from pathlib import Path

import frappe
from frappe import _


# ---------------------------------------------------------------------------
# Map each standalone HTML file to its Print Format record name.
# The key is the filename; the value is the Print Format ``name`` field.
# ---------------------------------------------------------------------------

PRINT_FORMAT_MAP = {
    "hire_purchase_agreement.html": "Hire Purchase Agreement",
    "schedule_1.html": "Schedule 1",
    "form_1.html": "Form 1",
    "form_2.html": "Form 2",
    "summary_of_financial_obligations.html": "Summary of Financial Obligations",
    "consent_form.html": "Consent Form",
    "assessment_sheet.html": "Assessment Sheet",
}

# Directory containing the standalone HTML files
TEMPLATE_DIR = Path(__file__).parent / "print_formats"


def sync_print_format_html():
    """Load standalone HTML files into their Print Format records.

    Called from hooks.py → after_migrate.  Idempotent — safe to run
    on every migration.  Does not modify records whose HTML content
    is already identical to the file (avoids unnecessary modified dates).

    The Assessment Sheet is created automatically if it does not yet exist.
    All other Print Formats must already be present (created by fixtures).
    """
    if not TEMPLATE_DIR.exists():
        return

    updated = []
    created = []

    for filename, print_format_name in PRINT_FORMAT_MAP.items():
        path = TEMPLATE_DIR / filename
        if not path.exists():
            continue

        html = path.read_text(encoding="utf-8")

        if frappe.db.exists("Print Format", print_format_name):
            # Only update if content actually changed
            current_html = frappe.db.get_value(
                "Print Format", print_format_name, "html"
            )
            if current_html == html:
                continue

            frappe.db.set_value(
                "Print Format",
                print_format_name,
                "html",
                html,
                update_modified=False,
            )
            updated.append(print_format_name)
        else:
            # Create new Print Format (for Assessment Sheet on first migration)
            frappe.get_doc(
                {
                    "doctype": "Print Format",
                    "name": print_format_name,
                    "doc_type": "Loan Application",
                    "print_format_type": "Jinja",
                    "custom_format": 1,
                    "standard": "No",
                    "module": "Loan Management",
                    "default_print_language": "en",
                    "html": html,
                }
            ).insert(ignore_permissions=True)
            created.append(print_format_name)

    if updated or created:
        frappe.clear_cache(doctype="Loan Application")

    if updated:
        frappe.log_error(
            title="Print formats synced",
            message="Updated: {}".format(", ".join(updated)),
        )
    if created:
        frappe.log_error(
            title="Print formats created",
            message="Created: {}".format(", ".join(created)),
        )
