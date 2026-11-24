"""Early settlement (Rule of 78 rebate) and write-off clean-up.

Early settlement
----------------
The customer is owed a rebate of the interest not yet earned, by the Rule of 78.
It is applied as a native **Credit Adjustment** against the loan (Dr Deferred
Interest / Cr HP Receivable), so Lending's own payoff figure and closure logic
see the reduced balance.  The rest of the deferred interest, earned to date but
not yet released, is moved to Interest Income by one journal entry.

Write-off
---------
After Lending's native Loan Write Off is submitted, deferred interest and deferred
penalties still sitting for that loan are cleared against the write-off account so
the expense is the net loss, not the gross receivable.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s


def _contract(loan_name):
	loan = frappe.get_doc("Loan", loan_name)
	loan.check_permission("read")
	if loan.company != s.COMPANY:
		frappe.throw("Settlement helper is restricted to Flexi loans")
	app = frappe.db.get_value(
		"Loan Application", loan.loan_application,
		["custom_flexi_term_charges", "custom_flexi_term_months"], as_dict=True,
	) if loan.get("loan_application") else None
	if not app or not app.custom_flexi_term_charges or not app.custom_flexi_term_months:
		frappe.throw(_("{0} is not a Hire Purchase loan created from a Loan Application.").format(loan_name))
	return loan, m.d(app.custom_flexi_term_charges), int(app.custom_flexi_term_months)


def _schedule_rows(loan_name):
	schedule = frappe.db.get_value(
		"Loan Repayment Schedule", {"loan": loan_name, "docstatus": 1, "status": "Active"}, "name"
	)
	if not schedule:
		frappe.throw(_("No active repayment schedule for {0}.").format(loan_name))
	return frappe.get_all(
		"Repayment Schedule", filters={"parent": schedule}, fields=["payment_date"], order_by="payment_date asc"
	)


def settlement_figures(loan_name, as_of=None):
	"""Pure read.  Rule of 78 rebate (CCA Schedule 3): the contract term in months and the whole months
	completed since the contract date, whatever the repayment frequency or any reschedule (dev26)."""
	as_of = getdate(as_of or today())
	loan, term_charges, periods = _contract(loan_name)
	paid = min(periods, m.complete_months(getdate(loan.posting_date), as_of))
	rebate = m.rule_of_78_unearned(term_charges, periods, paid)
	deferred_left = max(m.money(_loan_balance(s.ACC_DEF_INTEREST, loan_name)), m.d(0))
	rebate = min(rebate, deferred_left)  # never more than is still deferred
	booked = frappe.db.get_value("Loan Balance Adjustment", {"loan": loan_name, "reference_number": "HP-REBATE-" + loan_name, "docstatus": 1}, "amount")
	if booked:
		rebate = m.d(0)  # current native balance already includes this credit
	return {
		"loan": loan_name, "as_of": str(as_of), "periods": periods, "periods_paid": paid,
		"term_charges": float(term_charges), "deferred_interest_left": float(deferred_left),
		"rebate": float(rebate), "rebate_already_booked": flt(booked), "earned_not_released": 0 if booked else float(deferred_left - rebate),
	}


@frappe.whitelist()
def settlement_quote(loan, as_of=None):
	"""Rebate and amount payable on a given date, for the counter or the phone."""
	frappe.only_for(("Accounts Manager", "Accounts Officer", "Finance Manager", "Loan Manager", "System Manager"))
	figures = settlement_figures(loan, as_of)
	loan_row = frappe.db.get_value(
		"Loan", loan,
		["total_payment", "total_principal_paid", "debit_adjustment_amount", "credit_adjustment_amount"], as_dict=True,
	)
	figures["balance_before_rebate"] = (
		flt(loan_row.total_payment) - flt(loan_row.total_principal_paid)
		+ flt(loan_row.debit_adjustment_amount) - flt(loan_row.credit_adjustment_amount)
	)
	figures["payable_after_rebate"] = round(figures["balance_before_rebate"] - figures["rebate"], 2)
	return figures


@frappe.whitelist(methods=["POST"])
def post_settlement_rebate(loan, as_of=None):
	"""Book the rebate.  Confirmed by the Finance Manager (dev26); the customer then pays the reduced balance."""
	frappe.only_for(("Finance Manager", "System Manager"))
	frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan,))
	return book_rebate(loan, settlement_figures(loan, as_of))


def book_rebate(loan, figures):
	"""Credit Adjustment for the rebate (Dr 2013 / Cr 1101) and release of interest earned to that date."""
	key = f"HP-REBATE-{loan}"
	if frappe.db.exists("Loan Balance Adjustment", {"reference_number": key, "docstatus": 1}):
		frappe.throw(_("A settlement rebate has already been booked for {0}.").format(loan))
	doc = frappe.get_doc("Loan", loan)
	if figures["rebate"] > 0:
		adj = frappe.get_doc({
			"doctype": "Loan Balance Adjustment",
			"loan": loan, "applicant_type": "Customer", "applicant": doc.applicant,
			"company": doc.company, "posting_date": figures["as_of"],
			"adjustment_type": "Credit Adjustment", "amount": figures["rebate"],
			"adjustment_account": s.ACC_DEF_INTEREST,
			"reference_number": key,
			"remarks": f"Rule of 78 settlement rebate: {figures['periods_paid']} of {figures['periods']} months completed",
		})
		adj.insert()
		adj.submit()
	earned = figures["earned_not_released"]
	if earned > 0 and not s.entry_exists("Interest Release", loan, f"HP-SETTLE-{loan}"):
		je = frappe.get_doc({
			"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": doc.company,
			"posting_date": figures["as_of"], "custom_hp_loan": loan, "custom_hp_entry_type": "Interest Release",
			"user_remark": f"Interest earned to settlement date [HP-SETTLE-{loan}]",
			"accounts": [
				{"account": s.ACC_DEF_INTEREST, "debit_in_account_currency": earned},
				{"account": s.ACC_INTEREST_INCOME, "credit_in_account_currency": earned, "cost_center": doc.cost_center},
			],
		})
		je.insert()
		je.submit()
	return figures


def clear_deferred_on_write_off(doc, method=None):
	"""Loan Write Off on_submit: clear the loan's deferred interest and deferred penalties to the write-off account."""
	loan = doc.loan
	if doc.get("is_settlement_write_off"):
		return  # full-settlement adjustments are not a second full-loan clearance
	if doc.company != s.COMPANY or not frappe.db.get_value("Loan", loan, "loan_application"):
		return
	if s.entry_exists("Write Off Clearance", loan):
		return
	rows = []
	for key in ("ACC_DEF_INTEREST", "ACC_DEF_PENALTY"):
		account = s.account(key)
		balance = _loan_balance(account, loan)
		if balance > 0:
			rows.append({"account": account, "debit_in_account_currency": balance})
	if not rows:
		return
	total = sum(r["debit_in_account_currency"] for r in rows)
	rows.append({"account": doc.write_off_account or s.ACC_BAD_DEBT, "credit_in_account_currency": total,
		"cost_center": doc.cost_center})
	je = frappe.get_doc({
		"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": doc.company,
		"posting_date": doc.posting_date, "custom_hp_loan": loan, "custom_hp_entry_type": "Write Off Clearance",
		"user_remark": f"Deferred interest and penalties cleared on write-off of {loan}", "accounts": rows,
	})
	je.insert(ignore_permissions=True)
	je.submit()


def _loan_balance(account, loan):
	"""Credit balance of a deferred account attributable to one loan, from HP-tagged journals and adjustments."""
	jv = frappe.db.sql(
		"""SELECT COALESCE(SUM(jea.credit - jea.debit), 0) FROM `tabJournal Entry Account` jea
		   JOIN `tabJournal Entry` je ON je.name = jea.parent
		   WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND jea.account = %s""",
		(loan, account),
	)[0][0]
	lba = frappe.db.sql(
		"""SELECT COALESCE(SUM(CASE WHEN adjustment_type = 'Debit Adjustment' THEN amount ELSE -amount END), 0)
		   FROM `tabLoan Balance Adjustment` WHERE docstatus = 1 AND loan = %s AND adjustment_account = %s""",
		(loan, account),
	)[0][0]
	return flt(jv) + flt(lba)
