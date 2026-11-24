"""Closure true-up of deferred HP interest (dev22).

The month-end release jobs (hp_interest, hp_conversion) only look at open loans, so a loan that
closes before its last month-end run, or that was behind and then paid off, would keep part of its
term charges in Deferred HP Interest (2013) for ever.

Daily, for every closed Flexi loan that still has a 2013 balance:

* Closed on or after its contract maturity (the last instalment date of any submitted schedule,
  so a "Reduce Tenure" reschedule does not shorten it), or a settlement rebate is already booked:
  the whole remaining balance is earned.  One journal, Dr 2013 / Cr HP Interest Income, dated
  on the loan's closure date and tagged ``HP-CLOSE-<loan>``.
* Closed before maturity with no rebate booked: nothing is posted.  The customer may be owed a
  Rule of 78 rebate, so the loan is listed on the HP Closure Review report for Accounts to book
  the settlement rebate (see the repayments batch: a closed loan needs Dr 2013 / Cr Customer Refund).
  Nothing is released for it until then.

Idempotent: one closure journal per loan.  Never edits or cancels an existing document.
"""

import frappe
from frappe.utils import getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

TYPE = "Closure Release"


def deferred_left(loan):
	from comfort_homes.hp_lending.hp_settlement import _loan_balance
	return m.money(_loan_balance(s.ACC_DEF_INTEREST, loan))


def contract_maturity(loan):
	row = frappe.db.sql(
		"""SELECT MAX(rs.payment_date) FROM `tabRepayment Schedule` rs
		   JOIN `tabLoan Repayment Schedule` lrs ON lrs.name = rs.parent
		   WHERE lrs.loan = %s AND lrs.docstatus = 1""",
		(loan,),
	)
	return getdate(row[0][0]) if row and row[0][0] else None


def rebate_booked(loan):
	return bool(frappe.db.exists("Loan Balance Adjustment",
		{"loan": loan, "reference_number": "HP-REBATE-" + loan, "docstatus": 1}))


def classify(closure_date, maturity, has_rebate, balance):
	"""Pure: what to do with a closed loan's remaining deferred interest."""
	if balance <= 0:
		return "Nothing deferred"
	if has_rebate:
		return "Release"
	if maturity and closure_date and getdate(closure_date) < getdate(maturity):
		return "Rebate owed"
	return "Release"


def _closed_loans():
	return frappe.get_all(
		"Loan",
		filters={"company": s.COMPANY, "status": "Closed", "docstatus": 1, "applicant_type": "Customer",
			"loan_application": ["!=", ""]},
		fields=["name", "applicant", "applicant_name", "loan_application", "cost_center", "closure_date", "modified"],
		limit_page_length=0,
	)


def review_rows():
	"""Every closed Flexi loan with interest or penalties still deferred, and what happens next."""
	from comfort_homes.hp_lending.hp_settlement import _loan_balance
	rows = []
	for loan in _closed_loans():
		interest = deferred_left(loan.name)
		penalty = m.money(_loan_balance(s.ACC_DEF_PENALTY, loan.name))
		if interest <= 0 and penalty <= 0:
			continue
		maturity = contract_maturity(loan.name)
		action = classify(loan.closure_date, maturity, rebate_booked(loan.name), interest)
		rows.append(dict(loan=loan.name, customer=loan.applicant_name or loan.applicant,
			application=loan.loan_application, closure_date=loan.closure_date, maturity=maturity,
			deferred_interest=float(interest), deferred_penalty=float(penalty), action=action))
	return rows


def release_on_closure():
	"""Scheduled daily (after the penalty realisation job)."""
	released = []
	for loan in _closed_loans():
		if s.entry_exists(TYPE, loan.name):
			continue
		balance = deferred_left(loan.name)
		if balance <= 0:
			continue
		frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan.name,))
		action = classify(loan.closure_date, contract_maturity(loan.name), rebate_booked(loan.name), balance)
		if action != "Release":
			continue
		posting_date = loan.closure_date or today()
		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": s.COMPANY,
			"posting_date": posting_date,
			"custom_hp_loan": loan.name,
			"custom_hp_entry_type": TYPE,
			"user_remark": f"Deferred HP interest earned on closure of {loan.name} [HP-CLOSE-{loan.name}]",
			"accounts": [
				{"account": s.ACC_DEF_INTEREST, "debit_in_account_currency": float(balance)},
				{"account": s.ACC_INTEREST_INCOME, "credit_in_account_currency": float(balance),
					"cost_center": loan.cost_center},
			],
		})
		je.insert(ignore_permissions=True)
		je.submit()
		released.append(loan.name)
	return released
