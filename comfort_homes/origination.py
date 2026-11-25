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


HIRE_PURCHASE_PURPOSE = "Hire Purchase"
_PURPOSE_ALIASES = {"hp", "h.p.", "h/p", "hire purchase", "hire-purchase", "hirepurchase"}


def resolve_loan_purpose(doc, method=None):
    """A typed purpose such as "HP" is not a Loan Purpose record, which made the public
    submission fail with "Could not find Loan Purpose: HP". Map the known HP spellings to the
    single "Hire Purchase" master (created only if missing); any other unknown value is left
    for Frappe's normal link validation so genuine mistakes are still reported."""
    if doc.docstatus == 1 and not doc.is_new():
        return
    value = (doc.get("loan_purpose") or "").strip()
    if not value or frappe.db.exists("Loan Purpose", value):
        return
    if value.lower() not in _PURPOSE_ALIASES:
        return
    if not frappe.db.exists("Loan Purpose", HIRE_PURCHASE_PURPOSE):
        frappe.get_doc({"doctype": "Loan Purpose", "loan_purpose": HIRE_PURCHASE_PURPOSE}).insert(
            ignore_permissions=True)
    doc.set("loan_purpose", HIRE_PURCHASE_PURPOSE)


def apply_application(doc, method=None):
    """Prepare native validation inputs, then finalise the same approved figures."""
    if doc.docstatus == 1 and not doc.is_new():
        _restore_submitted_summary(doc)
        return
    _identity(doc)
    from comfort_homes.loan_application_workflow import _set_employment_period
    _set_employment_period(doc)
    block_unbookable_fees(doc)
    apply_default_charge_terms(doc)
    try:
        profile = _profile(doc)
        # dev27 (F13c): the customer's existing Flexi instalments are obligations too.
        existing = existing_flexi_monthly(doc)
        _set(doc, "custom_hp_existing_flexi_monthly", money(existing))
        if existing:
            profile["obligations_exact"] += existing
            profile["fields"]["custom_flexi_total_finance_obligations"] = money(profile["obligations_exact"])
            profile["fields"]["custom_flexi_dsr_net_income"] = money(profile["income_exact"] - profile["obligations_exact"])
            for field in ("custom_flexi_total_finance_obligations", "custom_flexi_dsr_net_income"):
                _set(doc, field, profile["fields"][field])
        rows = doc.get("custom_flexi_purchase_items") or []
        contract = contract_values(doc, rows)
        for row, total in zip(rows, contract["line_totals"]):
            row.set("total_amount", total)
        primary = assessment_values(
            profile, contract, doc.get("custom_flexi_selected_dsr"), doc.get("custom_flexi_payment_period"),
        )
        fields = {**contract["fields"], **primary}
        for field, value in fields.items():
            _set(doc, field, value)
        apply_joint_assessment(doc, profile, contract, primary)
    except (ValueError, TypeError, OverflowError) as error:
        frappe.throw(str(error), title="Origination Calculation")


MONTHLY_FACTOR = {"Weekly": 52 / 12, "Bi-Weekly": 26 / 12, "Fortnightly": 26 / 12, "Monthly": 1}


def existing_flexi_monthly(doc):
    """dev27 (F13c): monthly equivalent of the next instalment on each of the customer's open Flexi loans."""
    from comfort_homes.hp_lending import hp_settings as s
    if doc.get("company") != s.COMPANY or not doc.get("applicant") or doc.get("custom_hp_converted"):
        return 0.0
    total = 0.0
    loans = frappe.get_all("Loan", filters={"applicant": doc.applicant, "company": s.COMPANY, "docstatus": 1,
        "status": ["in", ["Disbursed", "Partially Disbursed"]]}, fields=["name", "repayment_frequency", "loan_application"])
    for loan in loans:
        if doc.name and loan.loan_application == doc.name:
            continue
        row = frappe.db.sql(
            """SELECT rs.total_payment FROM `tabRepayment Schedule` rs
               JOIN `tabLoan Repayment Schedule` lrs ON lrs.name = rs.parent
               WHERE lrs.loan = %s AND lrs.docstatus = 1 AND lrs.status = 'Active' AND rs.payment_date >= CURDATE()
               ORDER BY rs.payment_date LIMIT 1""", (loan.name,))
        if row and row[0][0]:
            total += float(row[0][0]) * MONTHLY_FACTOR.get(loan.repayment_frequency, 1)
    return total


def joint_profile(profile, co_applicants):
    """Pure: household income and obligations, primary plus each co-applicant's recorded monthly totals."""
    from comfort_homes.hp_lending.origination_math import number
    income = profile["income_exact"] + sum(number(p.get("custom_flexi_total_monthly_income")) for p in co_applicants)
    obligations = profile["obligations_exact"] + sum(number(p.get("custom_flexi_total_finance_obligations")) for p in co_applicants)
    return {**profile, "income_exact": income, "obligations_exact": obligations}


def apply_joint_assessment(doc, profile, contract, primary):
    """dev27 (F13b): store the joint (household) assessment the Application Print WIP shows, and the
    eligibility the approval decision rests on: joint when there are co-applicants, else the primary."""
    try:
        from comfort_homes.print_helpers import get_co_applicants
        co_applicants = get_co_applicants(doc) if doc.name else []
    except Exception:
        co_applicants = []
    if co_applicants:
        joint = joint_profile(profile, co_applicants)
        values = assessment_values(joint, contract, doc.get("custom_flexi_selected_dsr"), doc.get("custom_flexi_payment_period"))
        _set(doc, "custom_hp_joint_monthly_income", money(joint["income_exact"]))
        _set(doc, "custom_hp_joint_monthly_obligations", money(joint["obligations_exact"]))
        _set(doc, "custom_hp_joint_post_dsr", values["custom_flexi_post_dsr"])
        _set(doc, "custom_hp_decision_eligibility", values["custom_flexi_eligibility_status"])
    else:
        for field in ("custom_hp_joint_monthly_income", "custom_hp_joint_monthly_obligations", "custom_hp_joint_post_dsr"):
            _set(doc, field, 0)
        _set(doc, "custom_hp_decision_eligibility", primary["custom_flexi_eligibility_status"])


def block_unbookable_fees(doc):
    """dev22: surcharge, stamp duty and other fee have no account in the Gross HP booking."""
    from comfort_homes.hp_lending import hp_settings as s
    from comfort_homes.hp_lending.origination_math import number
    if doc.get("company") != s.COMPANY or doc.get("custom_hp_converted"):
        return
    used = [doc.meta.get_label(f) for f in s.UNBOOKABLE_FEE_FIELDS if doc.meta.has_field(f) and number(doc.get(f))]
    if used:
        frappe.throw("{0} cannot be charged on a Flexi HP contract yet: set to zero.".format(", ".join(used)),
            title="Fee Not Available")


def default_charge_terms(product):
    """(monthly rate %, grace days, stop days past due) from the Loan Product, with the HP defaults."""
    from comfort_homes.hp_lending import hp_settings as s
    from comfort_homes.hp_lending.origination_math import number
    row = frappe.db.get_value("Loan Product", product or s.DEFAULT_PRODUCT,
        ["custom_hp_penalty_rate_monthly", "grace_period_in_days", "custom_hp_penalty_stop_dpd"], as_dict=True) or {}
    rate = row.get("custom_hp_penalty_rate_monthly")
    grace = row.get("grace_period_in_days")
    stop = row.get("custom_hp_penalty_stop_dpd")
    return (s.PENALTY_MONTHLY_RATE_DEFAULT if rate is None else number(rate),
        s.GRACE_DAYS_DEFAULT if grace is None else int(grace), int(stop or s.NPA_DAYS_PAST_DUE))


def apply_default_charge_terms(doc):
    """dev22: snapshot the default charge the contract documents print and the scheduler charges.

    Taken from the Loan Product while the application is a draft, so it is part of the signed-contract
    fingerprint; a product change after signing therefore requires the pack to be re-signed."""
    from comfort_homes.hp_lending import hp_settings as s
    if doc.get("company") != s.COMPANY or doc.get("custom_hp_converted"):
        return
    rate, grace, stop = default_charge_terms(doc.get("loan_product"))
    _set(doc, "custom_hp_default_rate_monthly", rate)
    _set(doc, "custom_hp_default_grace_days", grace)
    _set(doc, "custom_hp_default_stop_dpd", stop)


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
