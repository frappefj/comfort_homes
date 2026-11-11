"""Flat-rate daily interest accrual override for Flexi HP loans.

The Frappe Lending module's daily accrual engine calculates interest on the
declining scheduled balance:

    daily_interest = outstanding_balance × rate ÷ 365

For Flexi's flat-rate (straight-line) HP agreements, interest should be
constant every day:

    daily_interest = total_term_interest ÷ days_in_term

This module overrides the accrual amount for flat-rate loans so the GL
matches the customer's HP agreement and monthly statements.
"""

import frappe
from frappe.utils import flt, date_diff, getdate


def apply_flat_daily_rate(doc, method=None):
    """Override the daily accrual amount for flat-rate HP loans.

    Fires on Loan Interest Accrual → before_validate.  Only acts on loans
    that use the "Flat Interest Rate" schedule and have a pre-calculated
    flat daily rate stored on the Loan doc.
    """
    if not doc.loan:
        return

    flat_rate = frappe.db.get_value(
        "Loan",
        doc.loan,
        ["custom_flat_daily_interest", "repayment_schedule_type"],
        as_dict=True,
    )

    if not flat_rate:
        return

    if flat_rate.repayment_schedule_type != "Flat Interest Rate":
        return

    daily = flt(flat_rate.custom_flat_daily_interest)
    if daily <= 0:
        return

    # Override the interest amount with the constant flat daily rate
    doc.interest_amount = daily


def set_flat_daily_rate_on_loan(doc, method=None):
    """Calculate and store the flat daily interest rate when a Loan is created.

    Fires on Loan → before_validate (for new loans only).
    The flat rate is: total_interest_payable ÷ term_in_days.

    Term days are calculated from the repayment schedule: first payment date
    to last payment date, or from posting_date to last payment date.
    """
    if doc.repayment_schedule_type != "Flat Interest Rate":
        return

    total_interest = flt(doc.total_interest_payable)
    if total_interest <= 0:
        return

    # Calculate term days from the repayment schedule
    schedule = _get_repayment_schedule(doc.name) if not doc.is_new() else None

    if schedule and len(schedule) >= 2:
        first_date = getdate(schedule[0].payment_date)
        last_date = getdate(schedule[-1].payment_date)
        # Use posting_date as start if earlier than first payment
        start_date = min(getdate(doc.posting_date), first_date) if doc.posting_date else first_date
        term_days = date_diff(last_date, start_date)
    else:
        # Fallback: estimate from repayment_periods (months)
        term_days = cint(doc.repayment_periods or 12) * 30

    if term_days <= 0:
        term_days = 365  # safe fallback

    doc.custom_flat_daily_interest = flt(total_interest / term_days, 4)


def set_flat_daily_rate_after_schedule(doc, method=None):
    """Recalculate the flat daily rate after the repayment schedule is generated.

    Fires on Loan Repayment Schedule → on_update.
    At this point the actual schedule dates are known, so the calculation
    is precise.
    """
    if not doc.loan:
        return

    loan = frappe.get_doc("Loan", doc.loan)
    if loan.repayment_schedule_type != "Flat Interest Rate":
        return

    total_interest = flt(loan.total_interest_payable)
    if total_interest <= 0:
        return

    # Get schedule rows from this document
    schedule_rows = doc.get("repayment_schedule") or []
    if len(schedule_rows) < 2:
        return

    first_date = getdate(schedule_rows[0].payment_date)
    last_date = getdate(schedule_rows[-1].payment_date)
    start_date = getdate(loan.posting_date or loan.creation)
    start_date = min(start_date, first_date)
    term_days = date_diff(last_date, start_date)

    if term_days <= 0:
        return

    flat_daily = flt(total_interest / term_days, 4)
    frappe.db.set_value("Loan", doc.loan, "custom_flat_daily_interest", flat_daily, update_modified=False)


def _get_repayment_schedule(loan_name):
    """Get the active repayment schedule rows for a loan."""
    schedule_name = frappe.db.get_value(
        "Loan Repayment Schedule",
        {"loan": loan_name, "docstatus": 1, "status": "Active"},
        "name",
    )
    if not schedule_name:
        return []

    return frappe.get_all(
        "Repayment Schedule",
        filters={"parent": schedule_name},
        fields=["payment_date", "interest_amount", "principal_amount"],
        order_by="idx asc",
    )


# Import for fallback calculation
from frappe.utils import cint
