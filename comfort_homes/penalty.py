"""Custom HP penalty calculator for Flexi hire-purchase agreements.

The native Lending module charges a daily rate on outstanding principal.
Flexi's HP agreement specifies 2.2% of each overdue instalment amount,
applied once per scheduled payment date after a 7-day grace period.

This module disables the native penalty (product penalty_interest_rate = 0)
and implements the contractual mechanism instead.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, getdate, today


PENALTY_RATE = 0.022  # 2.2% per overdue instalment
GRACE_PERIOD_DAYS = 7
PENALTY_APPLIED_FLAG = "custom_penalty_applied"


def _get_disbursed_loans():
    """Return all loans with status Disbursed."""
    return frappe.get_all(
        "Loan",
        filters={"status": "Disbursed", "docstatus": 1},
        fields=["name", "applicant", "loan_product"],
        limit_page_length=0,
    )


def _get_overdue_demands(loan_name, as_of_date):
    """Return unpaid or partially paid demands past the grace period.

    A demand is eligible for penalty when:
    - demand_date + grace_period < as_of_date
    - outstanding amount > 0 (demand not fully paid)
    - penalty has not already been applied for this demand
    """
    grace_cutoff = add_days(as_of_date, -GRACE_PERIOD_DAYS)

    demands = frappe.get_all(
        "Loan Demand",
        filters={
            "loan": loan_name,
            "docstatus": 1,
            "demand_date": ["<=", grace_cutoff],
            "demand_subtype": ["in", ["EMI", "Monthly"]],
        },
        fields=[
            "name",
            "demand_date",
            "demand_amount",
            "paid_amount",
            "outstanding_amount",
        ],
        order_by="demand_date asc",
        limit_page_length=0,
    )

    overdue = []
    for demand in demands:
        if flt(demand.outstanding_amount) <= 0:
            continue

        # Check if penalty was already applied for this demand
        already_applied = frappe.db.exists(
            "Loan Balance Adjustment",
            {
                "loan": loan_name,
                "docstatus": 1,
                "remarks": ["like", f"%penalty-demand-{demand.name}%"],
            },
        )
        if already_applied:
            continue

        overdue.append(demand)

    return overdue


def _apply_penalty(loan_name, demand, penalty_amount, posting_date):
    """Create a Loan Balance Adjustment for the penalty charge.

    The remarks field carries a tag like 'penalty-demand-LM-DMD-00123'
    so the system can detect whether a penalty was already applied to
    a specific demand row, preventing double-charging.
    """
    adjustment = frappe.get_doc(
        {
            "doctype": "Loan Balance Adjustment",
            "loan": loan_name,
            "posting_date": posting_date,
            "adjustment_type": "Loan Adjustment",
            "amount": penalty_amount,
            "remarks": (
                f"Default charge: {PENALTY_RATE * 100:.1f}% of overdue instalment "
                f"${flt(demand.demand_amount):,.2f} "
                f"(due {demand.demand_date}, {GRACE_PERIOD_DAYS}-day grace exceeded). "
                f"[penalty-demand-{demand.name}]"
            ),
        }
    )
    adjustment.insert(ignore_permissions=True)
    adjustment.submit()
    return adjustment.name


def calculate_and_apply_penalties(posting_date=None):
    """Daily scheduled job: scan all disbursed loans for overdue instalments.

    For each instalment past due by more than the grace period where
    penalty has not yet been applied, charge 2.2% of the instalment amount.

    Call from hooks.py scheduler_events or bench execute:
        bench execute comfort_homes.penalty.calculate_and_apply_penalties
    """
    posting_date = getdate(posting_date or today())
    loans = _get_disbursed_loans()
    total_applied = 0
    total_amount = 0

    for loan in loans:
        overdue_demands = _get_overdue_demands(loan.name, posting_date)

        for demand in overdue_demands:
            instalment_amount = flt(demand.demand_amount)
            penalty_amount = round(instalment_amount * PENALTY_RATE, 2)

            if penalty_amount <= 0:
                continue

            try:
                adjustment_name = _apply_penalty(
                    loan.name, demand, penalty_amount, posting_date
                )
                total_applied += 1
                total_amount += penalty_amount
                frappe.logger("penalty").info(
                    f"Penalty {adjustment_name}: ${penalty_amount:.2f} on "
                    f"{loan.name} demand {demand.name} (due {demand.demand_date})"
                )
            except Exception:
                frappe.log_error(
                    title=f"Penalty failed: {loan.name} / {demand.name}",
                    message=frappe.get_traceback(),
                )

    if total_applied:
        frappe.logger("penalty").info(
            f"Penalty run {posting_date}: {total_applied} charges, "
            f"${total_amount:,.2f} total"
        )

    return {"applied": total_applied, "amount": total_amount}
