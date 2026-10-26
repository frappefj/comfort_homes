"""Jinja template helpers for Comfort Homes print formats.

Phase 1 of the co-applicant integration.  Registered via hooks.py →
jinja → methods, making every function available in Jinja print formats
by its plain name.

Usage in any Loan Application print format:

    {%- set co_app = get_co_applicant(doc.name) -%}
    {%- set has_co_applicant = co_app is not none -%}
    {%- set jname = joint_name(doc.applicant_name, co_app) -%}
    {%- set ca_addr = co_applicant_address(co_app) -%}
"""

import frappe


# ---------------------------------------------------------------------------
# Co-applicant data access
# ---------------------------------------------------------------------------

def get_co_applicant(loan_application_name):
    """Return the full Co-Applicant document linked to a Loan Application.

    Returns None when:
      - The Loan Application has no ``custom_existing_co_applicant`` set
      - The linked Co-Applicant record does not exist
      - The Co-Applicant DocType is not installed on this site

    Safe to call from any Jinja context — never throws.
    """
    if not loan_application_name:
        return None
    if not frappe.db.exists("DocType", "Co-Applicant"):
        return None

    co_app_name = frappe.db.get_value(
        "Loan Application",
        loan_application_name,
        "custom_existing_co_applicant",
    )
    if not co_app_name:
        return None

    try:
        return frappe.get_doc("Co-Applicant", co_app_name)
    except frappe.DoesNotExistError:
        return None


# ---------------------------------------------------------------------------
# Name formatting
# ---------------------------------------------------------------------------

def joint_name(applicant_name, co_applicant):
    """Return 'Name1 AND Name2' when a co-applicant exists, else just Name1.

    Used in contract headings, preambles, and reference boxes where both
    parties must be named as joint hirers.

        {{ joint_name(doc.applicant_name, co_app) }}
    """
    name = applicant_name or ""
    if co_applicant and co_applicant.get("applicant_name"):
        return "{} AND {}".format(name, co_applicant.applicant_name)
    return name


# ---------------------------------------------------------------------------
# Address formatting
# ---------------------------------------------------------------------------

def co_applicant_address(co_applicant):
    """Build a formatted address string from Co-Applicant fields.

    Returns an empty string when no co-applicant is provided or when the
    co-applicant has no address data.

        {% if has_co_applicant %}{{ co_applicant_address(co_app) }}{% endif %}
    """
    if not co_applicant:
        return ""
    parts = [
        co_applicant.get("address_line_1"),
        co_applicant.get("address_line_2"),
        co_applicant.get("custom_suburb"),
        co_applicant.get("city"),
    ]
    province = co_applicant.get("custom_province")
    city = co_applicant.get("city")
    # Avoid duplicating province when it matches the city name
    if province and province != city:
        parts.append(province)
    return ", ".join(p for p in parts if p)


# ---------------------------------------------------------------------------
# Signature helpers
# ---------------------------------------------------------------------------

def co_applicant_signature(co_applicant):
    """Return the co-applicant's stored signature URL, or empty string.

        {% if has_co_applicant %}
            <img src="{{ co_applicant_signature(co_app) }}"/>
        {% endif %}
    """
    if not co_applicant:
        return ""
    return co_applicant.get("custom_applicant_signature") or ""


def co_applicant_signature_date(co_applicant, date_format="dd-mm-yyyy"):
    """Return the co-applicant's signature date, formatted.

        {{ co_applicant_signature_date(co_app) }}
    """
    if not co_applicant:
        return ""
    sig_date = co_applicant.get("custom_flexi_hirer_signature_date")
    if not sig_date:
        return ""
    return frappe.utils.formatdate(sig_date, date_format)
