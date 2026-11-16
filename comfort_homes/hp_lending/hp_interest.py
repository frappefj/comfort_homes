"""Month-end release of deferred interest (straight-line, non-overdue loans only).

The agreed rule: 1/N of the deferred interest is earned each month-end, final
month capped at the exact remainder, and only while the loan is current.
Interest skipped while overdue is released as a catch-up once the loan is
current again.  One journal entry per loan per run, tagged to the loan, so
"released to date" is read from the ledger and can never drift.
"""

import frappe
from frappe.utils import add_days, get_first_day, getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

TYPE = "Interest Release"


def released_to_date(loan):
	rows = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(jea.debit - jea.credit), 0)
		FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND je.custom_hp_entry_type = %s
		  AND jea.account = %s
		""",
		(loan, TYPE, s.ACC_DEF_INTEREST),
	)
	return rows[0][0]


def release_deferred_interest(as_of=None):
	"""Scheduled on the 1st: release for the month that has just ended."""
	month_end = add_days(get_first_day(as_of or today()), -1) if not as_of else getdate(as_of)
	released_loans = []
	loans = frappe.get_all(
		"Loan",
		filters={"company": s.COMPANY, "status": ["in", ["Disbursed", "Partially Disbursed"]], "docstatus": 1},
		fields=["name", "applicant", "loan_application", "cost_center", "days_past_due", "posting_date"],
		limit_page_length=0,
	)
	for loan in loans:
		if not loan.loan_application or (loan.days_past_due or 0) > 0:
			continue  # overdue: interest stays deferred until the loan is current
		tag = f"HP-INT-{loan.name}-{month_end}"
		if s.entry_exists(TYPE, loan.name, tag):
			continue
		app = frappe.db.get_value(
			"Loan Application", loan.loan_application,
			["custom_flexi_term_charges", "custom_flexi_term_months"], as_dict=True,
		)
		if not app or not app.custom_flexi_term_charges:
			continue
		disbursed_on = frappe.db.get_value(
			"Loan Disbursement", {"against_loan": loan.name, "docstatus": 1}, "disbursement_date", order_by="disbursement_date asc"
		) or loan.posting_date
		due = m.interest_release_due(
			app.custom_flexi_term_charges,
			app.custom_flexi_term_months or 12,
			m.months_elapsed(getdate(disbursed_on), month_end),
			released_to_date(loan.name),
		)
		if due <= 0:
			continue
		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": s.COMPANY,
			"posting_date": month_end,
			"custom_hp_loan": loan.name,
			"custom_hp_entry_type": TYPE,
			"user_remark": f"Deferred HP interest earned, {loan.name}, to {month_end} [{tag}]",
			"accounts": [
				{"account": s.ACC_DEF_INTEREST, "debit_in_account_currency": float(due)},
				{"account": s.ACC_INTEREST_INCOME, "credit_in_account_currency": float(due), "cost_center": loan.cost_center},
			],
		})
		je.insert(ignore_permissions=True)
		je.submit()
		released_loans.append(loan.name)
	return released_loans
