"""Flat-rate daily interest accrual override for Flexi HP loans.

The Frappe Lending module's daily accrual engine calculates interest on the
declining scheduled balance:

    daily_interest = outstanding_balance × rate ÷ 365

For Flexi's flat-rate (straight-line) HP agreements, interest should be
constant every day:

    daily_interest = total_term_interest ÷ days_in_term

This module overrides the accrual amount for flat-rate loans so the GL
matches the customer's HP agreement and statements.

Supports all repayment frequencies: Weekly, Bi-Weekly (Fortnightly), Monthly.
Works with both "Flat Interest Rate" and "Monthly as per repayment start date"
schedule types — the flat override is triggered by the presence of a non-zero
custom_flat_daily_interest value on the Loan doc.
"""

import frappe
from frappe.utils import cint, flt, date_diff, getdate


def apply_flat_daily_rate(doc, method=None):
    """Override the daily accrual amount for flat-rate HP loans.

    Fires on Loan Interest Accrual → before_validate.  Only acts when the
    Loan has a pre-calculated flat daily rate stored in custom_flat_daily_interest.
    """
    if not doc.loan:
        return

    daily = flt(frappe.db.get_value("Loan", doc.loan, "custom_flat_daily_interest"))
    if daily <= 0:
        return

    # Override the interest amount with the constant flat daily rate
    doc.interest_amount = daily


def set_flat_daily_rate_on_loan(doc, method=None):
    """Calculate and store the flat daily interest rate when a Loan is created.

    Fires on Loan → before_validate (for new loans only).
    The flat rate is: total_interest_payable ÷ term_in_days.
    """
    total_interest = flt(doc.total_interest_payable)
    if total_interest <= 0:
        return

    # Calculate term days from repayment periods and frequency
    term_days = _estimate_term_days(doc)
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

    loan_fields = frappe.db.get_value(
        "Loan", doc.loan,
        ["total_interest_payable", "posting_date"],
        as_dict=True,
    )
    if not loan_fields:
        return

    total_interest = flt(loan_fields.total_interest_payable)
    if total_interest <= 0:
        return

    # Get schedule rows from this document
    schedule_rows = doc.get("repayment_schedule") or []
    if len(schedule_rows) < 2:
        return

    first_date = getdate(schedule_rows[0].payment_date)
    last_date = getdate(schedule_rows[-1].payment_date)
    start_date = getdate(loan_fields.posting_date) if loan_fields.posting_date else first_date
    start_date = min(start_date, first_date)
    term_days = date_diff(last_date, start_date)

    if term_days <= 0:
        return

    flat_daily = flt(total_interest / term_days, 4)
    frappe.db.set_value("Loan", doc.loan, "custom_flat_daily_interest", flat_daily, update_modified=False)


def _estimate_term_days(doc):
    """Estimate term in days from repayment periods and frequency."""
    periods = cint(doc.repayment_periods)
    freq = doc.get("repayment_frequency") or "Monthly"

    if freq == "Weekly":
        return periods * 7
    elif freq in ("Bi-Weekly", "Fortnightly"):
        return periods * 14
    elif freq == "Monthly":
        return int(periods * 365 / 12)
    elif freq == "Quarterly":
        return int(periods * 365 / 4)
    elif freq == "Yearly":
        return periods * 365
    else:
        return int(periods * 365 / 12)
