"""Retained HP Agreement endpoints, guarded even when called directly.

The current Desk asset is print-only; these handlers are not proof that digital
signing is the operational process. Do not re-enable a signing UI without approval.
"""

import frappe
from frappe import _
from frappe.utils import getdate, now_datetime


def _check_agreement_access(docname, rowname=None, write=False, company_signing=False):
    """Authorise before any signature, file or agreement mutation."""
    from comfort_homes.api import check_application_access
    check_application_access(docname, write=int(write), agreement_row=rowname,
                             company_signing=int(company_signing))
    application = frappe.get_doc("Loan Application", docname)
    if rowname:
        row = frappe.get_doc("Loan Customer Agreement", rowname)
        code = row.get("document_code") or frappe.db.get_value(
            "Loan Document Template", row.document_template, "document_code")
        if code != "HP-AGREEMENT":
            frappe.throw("This endpoint only handles the HP Agreement")
    if write and application.get("custom_existing_co_applicant"):
        _check_co_applicant_access(application)
    if write:
        from comfort_homes.hp_lending.document_policy import DIGITAL_CONTRACT_SIGNING_ENABLED
        if not DIGITAL_CONTRACT_SIGNING_ENABLED:
            frappe.throw("Digital HP contract signing is not enabled; use the wet-signature scan process")
    return application


def _check_co_applicant_access(application):
    name = application.get("custom_existing_co_applicant")
    if not name:
        return None
    profile = frappe.get_doc("Co-Applicant", name)
    profile.check_permission("write")
    if profile.company != application.company:
        frappe.throw("Co-Applicant and application must belong to the same company")
    return profile


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# The print format used for the HP Agreement.  All three signing flows
# render the same format; the format's own Jinja logic handles the
# co-applicant lookup and dual-signature layout.
PRINT_FORMAT = "Hire Purchase Agreement"


# ---------------------------------------------------------------------------
# Shared helpers — used by customer-sign, director-sign, and preview
# ---------------------------------------------------------------------------

def _set_print_aliases(doc, agreement=None):
    """Provide runtime values expected by the HP Agreement print format.

    The print format references ``doc.hp_account_no``, ``doc.hirer_signature``
    and similar short names.  These are not database fields; they are set on
    the document object at render time so both the signing flow and the
    direct-print-URL path produce the same output.
    """
    doc.hp_account_no = doc.get("custom_flexi_hp_account_number") or doc.name
    doc.hp_reference_no = doc.get("custom_flexi_quotation_number") or ""

    doc.hirer_signature = (
        doc.get("custom_applicant_signature")
        or (agreement.customer_signature if agreement else "")
        or ""
    )
    doc.hirer_signature_date = (
        doc.get("custom_flexi_hirer_signature_date")
        or (agreement.customer_signed_on if agreement else None)
    )

    doc.company_representative_name = (
        doc.get("custom_flexi_company_representative_name") or ""
    )
    doc.company_representative_signature = (
        doc.get("custom_flexi_company_representative_signature") or ""
    )
    doc.company_representative_date = (
        doc.get("custom_flexi_company_representative_date") or None
    )

    # Company registered office — used in the contract preamble
    doc.company_registered_office = _company_registered_office()

    return doc


def _company_registered_office():
    """Return a formatted company address string for the contract preamble."""
    addresses = frappe.get_all(
        "Address",
        filters={"is_your_company_address": 1},
        fields=["address_line1", "address_line2", "city", "state", "country"],
        limit_page_length=1,
    )
    if not addresses:
        return ""
    parts = []
    for key in ("address_line1", "address_line2", "city", "state", "country"):
        value = addresses[0].get(key)
        if value:
            parts.append(value)
    return ", ".join(parts)


def _save_co_applicant_signature(doc):
    """Save the co-applicant's signature from form_dict if present.

    MUST be called BEFORE HTML rendering so the print format's Jinja
    lookup picks up the co-applicant's saved signature image and date.

    The co-hirer signature fields are sent by the client dialog alongside
    the main hirer signature.  They are optional — when no co-applicant
    exists on the Loan Application, this function does nothing.
    """
    co_app_name = doc.get("custom_existing_co_applicant")
    if not co_app_name:
        return
    _check_co_applicant_access(doc)

    co_sig = frappe.form_dict.get("co_hirer_signature")
    co_sig_date = frappe.form_dict.get("co_hirer_signature_date")

    if not co_sig and not co_sig_date:
        return

    updates = {}
    if co_sig:
        updates["custom_applicant_signature"] = co_sig
    updates["custom_flexi_hirer_signature_date"] = (
        co_sig_date or getdate(now_datetime())
    )

    frappe.db.set_value(
        "Co-Applicant", co_app_name, updates, update_modified=False
    )


def _attach_html(doctype, docname, file_name, html):
    """Attach rendered HTML as a private file to any document.

    Returns the file URL, or None if creation fails.
    """
    file_doc = frappe.get_doc({
        "doctype": "File",
        "file_name": file_name,
        "content": html,
        "is_private": 1,
        "attached_to_doctype": doctype,
        "attached_to_name": docname,
    })
    file_doc.insert(ignore_permissions=True)
    return file_doc.file_url


def _attach_to_co_applicant(doc, html):
    """Attach a copy of the signed agreement to the Co-Applicant record.

    The joint agreement belongs to both parties.  Attaching it to the
    Co-Applicant record means staff can find the signed contract from
    either the Loan Application or the Co-Applicant form.

    Returns the Co-Applicant file URL, or None if no co-applicant.
    """
    co_app_name = doc.get("custom_existing_co_applicant")
    if not co_app_name:
        return None
    _check_co_applicant_access(doc)
    file_name = "{}-HP-Agreement-Signed.html".format(co_app_name)
    return _attach_html("Co-Applicant", co_app_name, file_name, html)


def _render_agreement(doc):
    """Render the HP Agreement print format and return the HTML string."""
    html = frappe.get_print(
        "Loan Application", doc.name, print_format=PRINT_FORMAT, doc=doc
    )
    if not html:
        frappe.throw(_("HP Agreement could not be rendered."))
    return html


# ---------------------------------------------------------------------------
# API: Customer Sign
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def hp_agreement_customer_sign(**kwargs):
    """Customer (and co-applicant) signs the HP Agreement.

    Expected form_dict parameters:
        docname              — Loan Application name (required)
        rowname              — Loan Customer Agreement child row name (required)
        signature            — Customer signature data URL (required unless signature_file)
        signature_file       — Uploaded signature file URL (alternative to signature)
        co_hirer_signature   — Co-applicant signature data URL (optional)
        co_hirer_signature_date — Co-applicant signature date (optional)
    """
    docname = frappe.form_dict.get("docname")
    rowname = frappe.form_dict.get("rowname")
    _check_agreement_access(docname, rowname, write=True)
    signature = frappe.form_dict.get("signature")
    signature_file = frappe.form_dict.get("signature_file")

    # -- Validate --
    if not docname:
        frappe.throw(_("Loan Application is required"))
    if not rowname:
        frappe.throw(_("Customer Agreement row is required"))
    if not signature and not signature_file:
        frappe.throw(_("Customer signature is required"))

    signed_on = now_datetime()
    signed_date = getdate(signed_on)

    # -- 1. Save main hirer signature on Loan Application --
    frappe.db.set_value(
        "Loan Application",
        docname,
        {
            "custom_applicant_signature": signature or "",
            "custom_flexi_hirer_signature_date": signed_date,
        },
    )

    # -- 2. Save co-applicant signature BEFORE rendering --
    #    The print format's Jinja fetches the Co-Applicant doc; the
    #    signature must be persisted before that fetch happens.
    doc_for_co = frappe.get_doc("Loan Application", docname)
    _save_co_applicant_signature(doc_for_co)

    # -- 3. Save signing data on the Customer Agreement row --
    frappe.db.set_value(
        "Loan Customer Agreement",
        rowname,
        {
            "customer_viewed": 1,
            "customer_acknowledged": 1,
            "customer_acknowledged_on": signed_on,
            "customer_signature": signature or "",
            "customer_signature_file": signature_file or "",
            "customer_signed_on": signed_on,
            "status": "Customer Signed",
        },
    )

    # -- 4. Reload and set print aliases --
    doc = _set_print_aliases(frappe.get_doc("Loan Application", docname))

    # -- 5. Render agreement with all signatures now saved --
    html = _render_agreement(doc)

    # -- 6. Freeze rendered HTML to agreement row --
    frappe.db.set_value(
        "Loan Customer Agreement",
        rowname,
        {"generated_html": html, "generated_on": signed_on},
    )

    frappe.db.commit()

    frappe.response["message"] = {
        "status": "Customer Signed",
        "html": html,
    }


# ---------------------------------------------------------------------------
# API: Director Sign
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def hp_agreement_director_sign(**kwargs):
    """Director counter-signs the HP Agreement and produces the final copy.

    Expected form_dict parameters:
        docname              — Loan Application name (required)
        rowname              — Loan Customer Agreement child row name (required)
        director_name        — Director / Manager name (required)
        director_signature   — Director signature data URL (required unless director_signature_file)
        director_signature_file — Uploaded director signature (alternative)
    """
    docname = frappe.form_dict.get("docname")
    rowname = frappe.form_dict.get("rowname")
    _check_agreement_access(docname, rowname, write=True, company_signing=True)
    director_name = frappe.form_dict.get("director_name") or ""
    director_signature = frappe.form_dict.get("director_signature") or ""
    director_signature_file = frappe.form_dict.get("director_signature_file") or ""

    # -- Validate --
    if not docname:
        frappe.throw(_("Loan Application is required"))
    if not rowname:
        frappe.throw(_("Agreement row is required"))
    if not director_name:
        frappe.throw(_("Director / Manager name is required"))

    signature_value = director_signature or director_signature_file or ""
    if not signature_value:
        frappe.throw(_("Director signature is required"))

    signed_on = now_datetime()
    signed_date = getdate(signed_on)

    # -- 1. Verify customer signed first --
    agreement = frappe.get_doc("Loan Customer Agreement", rowname)
    if not agreement.customer_signature:
        frappe.throw(_("Customer signature is missing. Customer must sign first."))

    # -- 2. Save director details to Loan Application --
    frappe.db.set_value(
        "Loan Application",
        docname,
        {
            "custom_flexi_company_representative_name": director_name,
            "custom_flexi_company_representative_signature": signature_value,
            "custom_flexi_company_representative_date": signed_date,
        },
    )

    # -- 3. Save director details to agreement row --
    frappe.db.set_value(
        "Loan Customer Agreement",
        rowname,
        {
            "director_name": director_name,
            "director_signature": signature_value,
            "director_signed_on": signed_on,
            "status": "Director Signed",
        },
    )

    # -- 4. Reload and set print aliases --
    doc = _set_print_aliases(
        frappe.get_doc("Loan Application", docname), agreement=agreement
    )

    # -- 5. Render final agreement (both signatures present) --
    html = _render_agreement(doc)

    # -- 6. Attach signed copy to Loan Application --
    file_name = "{}-HP-Agreement-Signed.html".format(doc.name)
    file_url = _attach_html("Loan Application", doc.name, file_name, html)
    if not file_url:
        frappe.throw(_("Final signed agreement attachment could not be created."))

    # -- 7. Attach copy to Co-Applicant (if present) --
    co_app_file_url = _attach_to_co_applicant(doc, html)

    # -- 8. Complete the Customer Agreement row --
    frappe.db.set_value(
        "Loan Customer Agreement",
        rowname,
        {
            "generated_html": html,
            "final_signed_file": file_url,
            "director_signed_on": signed_on,
            "completed_on": signed_on,
            "status": "Completed",
        },
    )

    # -- 9. Update Loan Application documents table --
    loan = frappe.get_doc("Loan Application", docname)
    document_row = None
    for d in loan.documents:
        if d.document_code == "HP-AGREEMENT":
            document_row = d
            break

    if not document_row:
        document_row = loan.append("documents", {})

    document_row.update({
        "document_type": "Hire Purchase Agreement",
        "file": file_url,
        "document_template": agreement.document_template,
        "document_code": "HP-AGREEMENT",
        "version": agreement.version or 1,
        "status": "Completed",
        "generated_on": agreement.generated_on or signed_on,
        "customer_viewed_on": agreement.customer_viewed_on,
        "customer_signed_on": agreement.customer_signed_on,
        "director_signed_on": signed_on,
        "final_signed_file": file_url,
    })

    loan.save(ignore_permissions=True)
    frappe.db.commit()

    # -- 10. Return response --
    response = {
        "status": "Completed",
        "html": html,
        "file_url": file_url,
    }
    if co_app_file_url:
        co_app_name = doc.get("custom_existing_co_applicant")
        response["co_applicant"] = co_app_name
        response["co_applicant_file_url"] = co_app_file_url

    frappe.response["message"] = response


# ---------------------------------------------------------------------------
# API: Preview
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["GET", "POST"])
def hp_agreement_preview(**kwargs):
    """Read-only preview of the HP Agreement.  No data is saved.

    Expected form_dict parameters:
        docname — Loan Application name (required)
    """
    docname = frappe.form_dict.get("docname")
    if not docname:
        frappe.throw(_("Loan Application is required"))

    application = _check_agreement_access(docname, write=False)
    doc = _set_print_aliases(application)
    html = _render_agreement(doc)

    frappe.response["message"] = {"html": html}
