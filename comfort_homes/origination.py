"""Frappe adapters for preserved origination calculations; no ledger postings.

Register apply_application before_validate and before_save. The latter restores
flat summary fields after Lending's native validation calculates its preview.
Co-applicants get profile totals only, never a separate proposed facility.
"""

import frappe

from comfort_homes.hp_lending.origination_math import (
    assessment_values, contract_values, money, profile_values,
)


def _set(doc, field, value):
    if doc.meta.has_field(field):
        doc.set(field, value)


def _profile(doc):
    tables = [doc.get(name) or [] for name in (
        "custom_flexi_income_entries", "custom_flexi_living_expenses", "custom_flexi_finance_obligations",
    )]
    profile = profile_values(*tables)
    for rows, values in zip(tables, (
        profile["income_row_monthly"], profile["expense_row_monthly"], profile["obligation_row_monthly"],
    )):
        for row, value in zip(rows, values):
            row.set("monthly_amount", value)
    for field, value in profile["fields"].items():
        _set(doc, field, value)
    return profile


def _phone(value):
    value = str(value or "").strip()
    digits = "".join(c for c in value if c.isdigit())
    while digits.startswith("679679"):
        digits = digits[3:]
    if digits.startswith("679"):
        digits = digits[3:]
    return "+679-" + digits if len(digits) == 7 else value


def _identity(doc):
    """Retain non-financial work previously bundled in the disabled calculator."""
    if doc.get("applicant_type") == "Customer" and doc.get("applicant"):
        customer = frappe.get_doc("Customer", doc.applicant)
        if not getattr(frappe.flags, "in_web_form", False):
            customer.check_permission("read")
        if not customer.customer_name:
            frappe.throw("Customer Full Name is missing in the selected Customer master.")
        _set(doc, "custom_customer_full_name", customer.customer_name)
        contact = frappe.get_doc("Contact", customer.customer_primary_contact) if customer.get("customer_primary_contact") else None
        address = frappe.get_doc("Address", customer.customer_primary_address) if customer.get("customer_primary_address") else None
        split_name = bool(contact and (contact.get("middle_name") or contact.get("last_name")))
        parts = str((contact.get("full_name") if contact else None) or customer.customer_name).split()
        identity = {
            "custom_first_name": (contact.get("first_name") if split_name else None) or (parts[0] if parts else ""),
            "custom_middle_name": (contact.get("middle_name") if split_name else None) or (" ".join(parts[1:-1]) if len(parts) > 2 else ""),
            "custom_surname": (contact.get("last_name") if split_name else None) or (parts[-1] if len(parts) > 1 else ""),
            "custom_tin_number": customer.get("custom_tin_number"),
            "applicant_email_address": customer.get("email_id"),
            "applicant_phone_number": customer.get("mobile_no"),
        }
        if address:
            identity.update({
                "address_line_1": address.get("address_line1"), "address_line_2": address.get("address_line2"),
                "custom_new_city": address.get("city"), "city": address.get("city"),
                "custom_province": address.get("state"), "state": address.get("state"),
                "country": address.get("country") or "Fiji", "zip_code": address.get("pincode"),
            })
        for field, value in identity.items():
            if not doc.get(field) and value:
                _set(doc, field, value)
    parts = [doc.get(f) for f in ("custom_first_name", "custom_middle_name", "custom_surname")]
    if any(parts):
        _set(doc, "applicant_name", " ".join(str(p).strip() for p in parts if p))
    for field in ("applicant_phone_number", "custom_flexi_employer_contact", "custom_flexi_spouse_mobile", "custom_flexi_spouse_employer_contact"):
        if doc.get(field):
            _set(doc, field, _phone(doc.get(field)))
    for row in doc.get("custom_flexi_application_contacts") or []:
        for field in ("mobile_number", "home_phone", "employer_contact"):
            if row.get(field):
                row.set(field, _phone(row.get(field)))


def _restore_submitted_summary(doc):
    """Submitted contracts retain their saved terms; do not reprice them."""
    months = int(doc.get("custom_flexi_term_months") or 12)
    total = money(doc.get("custom_flexi_total_receivable"))
    for field, value in {
        "repayment_periods": months,
        "repayment_amount": money(total / months) if months else 0,
        "total_payable_amount": total,
        "total_payable_interest": money(doc.get("custom_flexi_term_charges")),
    }.items():
        _set(doc, field, value)


def apply_application(doc, method=None):
    """Prepare native validation inputs, then finalise the same approved figures."""
    if doc.docstatus == 1 and not doc.is_new():
        _restore_submitted_summary(doc)
        return
    _identity(doc)
    from comfort_homes.loan_application_workflow import _set_employment_period
    _set_employment_period(doc)
    try:
        profile = _profile(doc)
        rows = doc.get("custom_flexi_purchase_items") or []
        contract = contract_values(doc, rows)
        for row, total in zip(rows, contract["line_totals"]):
            row.set("total_amount", total)
        fields = {**contract["fields"], **assessment_values(
            profile, contract, doc.get("custom_flexi_selected_dsr"), doc.get("custom_flexi_payment_period"),
        )}
        for field, value in fields.items():
            _set(doc, field, value)
    except (ValueError, TypeError, OverflowError) as error:
        frappe.throw(str(error), title="Origination Calculation")


def apply_profile(doc, method=None):
    """Maintain a linked person's individual profile without loan/PO creation."""
    if doc.docstatus == 1 and not doc.is_new():
        return
    _identity(doc)
    from comfort_homes.loan_application_workflow import _set_employment_period
    _set_employment_period(doc)
    try:
        _profile(doc)
    except (ValueError, TypeError, OverflowError) as error:
        frappe.throw(str(error), title="Profile Calculation")
