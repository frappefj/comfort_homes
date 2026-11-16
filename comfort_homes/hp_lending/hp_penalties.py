"""Late-payment charges and their recognition on closure.

Rule (agreed): on each scheduled repayment date, charge a percentage of every
instalment that is overdue beyond the grace period.  Older arrears are charged
again each period, penalties are never charged on penalties, and nothing is
charged once the loan reaches the stop point (90 days past due).

The charge is posted as a native Loan Balance Adjustment (Debit Adjustment) so
it increases what the loan owes; the credit goes to the deferred-penalty
liability.  It is recognised as income only when the loan closes.

Rates and limits are fields on the Loan Product (penalty rate per month, stop
days past due) and Lending's own Grace Period field, not constants in code.
Lending's native daily penalty must stay at 0 on every product.
"""

import frappe
from frappe.utils import add_days, getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

KEY = "HP-PEN"


def _product_settings(product):
	row = frappe.db.get_value(
		"Loan Product",
		product,
		["custom_hp_penalty_rate_monthly", "custom_hp_penalty_stop_dpd", "grace_period_in_days"],
		as_dict=True,
		cache=True,
	) or {}
	rate = row.get("custom_hp_penalty_rate_monthly")
	stop = row.get("custom_hp_penalty_stop_dpd")
	grace = row.get("grace_period_in_days")
	return (
		s.PENALTY_MONTHLY_RATE_DEFAULT if rate is None else rate,
		s.NPA_DAYS_PAST_DUE if not stop else stop,
		s.GRACE_DAYS_DEFAULT if grace is None else grace,
	)


def _is_repayment_date(loan, on_date):
	return bool(
		frappe.db.sql(
			"""
			SELECT 1 FROM `tabRepayment Schedule` rs
			JOIN `tabLoan Repayment Schedule` lrs ON lrs.name = rs.parent
			WHERE lrs.loan = %s AND lrs.docstatus = 1 AND lrs.status = 'Active' AND rs.payment_date = %s
			LIMIT 1
			""",
			(loan, on_date),
		)
	)


def overdue_base(loan, on_date, grace_days):
	"""Unpaid instalments whose due date is past grace.  Penalty demands are excluded."""
	rows = frappe.db.sql(
		"""
		SELECT COALESCE(SUM(outstanding_amount), 0) FROM `tabLoan Demand`
		WHERE loan = %s AND docstatus = 1 AND demand_type = 'EMI'
		  AND outstanding_amount > 0 AND demand_date <= %s
		""",
		(loan, add_days(on_date, -int(grace_days))),
	)
	return rows[0][0]


def assess_penalties(on_date=None):
	"""Scheduled daily.  Safe to run twice: each loan and date is charged once."""
	on_date = getdate(on_date or today())
	charged = []
	loans = frappe.get_all(
		"Loan",
		filters={"company": s.COMPANY, "status": ["in", ["Disbursed", "Partially Disbursed"]], "docstatus": 1},
		fields=["name", "applicant", "loan_product", "repayment_frequency", "days_past_due", "cost_center"],
		limit_page_length=0,
	)
	for loan in loans:
		rate, stop_dpd, grace = _product_settings(loan.loan_product)
		if not rate or (loan.days_past_due or 0) >= stop_dpd:
			continue
		if not _is_repayment_date(loan.name, on_date):
			continue
		key = f"{KEY}-{loan.name}-{on_date}"
		if frappe.db.exists("Loan Balance Adjustment", {"reference_number": key, "docstatus": ["!=", 2]}):
			continue
		base = overdue_base(loan.name, on_date, grace)
		amount = m.penalty_amount(base, rate, loan.repayment_frequency)
		if amount <= 0:
			continue
		adjustment = frappe.get_doc({
			"doctype": "Loan Balance Adjustment",
			"loan": loan.name,
			"applicant_type": "Customer",
			"applicant": loan.applicant,
			"company": s.COMPANY,
			"posting_date": on_date,
			"cost_center": loan.cost_center,
			"adjustment_type": "Debit Adjustment",
			"amount": float(amount),
			"adjustment_account": s.ACC_DEF_PENALTY,
			"reference_number": key,
			"remarks": f"Late payment charge: {rate}% a month on overdue {base:.2f} ({loan.repayment_frequency})",
		})
		adjustment.insert(ignore_permissions=True)
		adjustment.submit()
		charged.append(loan.name)
	return charged


def realise_closed_loan_penalties():
	"""Scheduled daily.  Move deferred penalties to income for loans that have closed."""
	done = []
	rows = frappe.db.sql(
		"""
		SELECT lba.loan,
		       SUM(CASE WHEN lba.adjustment_type = 'Debit Adjustment' AND lba.reference_number LIKE %s THEN lba.amount
		                WHEN lba.adjustment_type = 'Credit Adjustment' AND lba.adjustment_account = %s THEN -lba.amount
		                ELSE 0 END) AS total
		FROM `tabLoan Balance Adjustment` lba
		JOIN `tabLoan` l ON l.name = lba.loan
		WHERE lba.docstatus = 1 AND l.status = 'Closed'
		GROUP BY lba.loan
		HAVING total > 0
		""",
		(f"{KEY}-%", s.ACC_DEF_PENALTY),
		as_dict=True,
	)
	for row in rows:
		if s.entry_exists("Penalty Realisation", row.loan):
			continue
		je = frappe.get_doc({
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": s.COMPANY,
			"posting_date": today(),
			"custom_hp_loan": row.loan,
			"custom_hp_entry_type": "Penalty Realisation",
			"user_remark": f"Late payment charges recognised on closure of {row.loan}",
			"accounts": [
				{"account": s.ACC_DEF_PENALTY, "debit_in_account_currency": float(m.money(row.total))},
				{"account": s.ACC_PENALTY_INCOME, "credit_in_account_currency": float(m.money(row.total))},
			],
		})
		je.insert(ignore_permissions=True)
		je.submit()
		done.append(row.loan)
	return done
