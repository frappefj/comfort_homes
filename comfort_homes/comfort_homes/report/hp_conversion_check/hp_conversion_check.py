"""Read-only: converted MYOB loans against their cut-over figures, and the 1910 Temporary Opening balance."""
import frappe
from frappe.utils import flt

from comfort_homes.hp_lending import hp_settings as s
from comfort_homes.hp_lending.hp_conversion import OPENING_ACCOUNT_NUMBER


def _opening_credit(loan):
	"""7.1: MYOB paid-ahead held as customer credit at cut-over (dev26 design, Dr 1910 / Cr 2017,
	tagged HP-OPEN-CREDIT-<loan>). The receivable still carries those instalments, so the check deducts it."""
	from comfort_homes.hp_lending.hp_credit import credit_account
	account = credit_account(required=False)
	if not account:
		return 0.0
	return flt(frappe.db.sql(
		"""SELECT COALESCE(SUM(jea.credit - jea.debit), 0) FROM `tabJournal Entry Account` jea
		   JOIN `tabJournal Entry` je ON je.name = jea.parent
		   WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND je.user_remark LIKE %s AND jea.account = %s""",
		(loan, f"%[HP-OPEN-CREDIT-{loan}]%", account))[0][0], 2)


def _opening_balance(account, loan):
	"""Lending's opening entry for the imported loan, less the paid-ahead amount brought forward
	(as an Advance Payment before dev26, as customer credit since)."""
	opening = flt(frappe.db.sql(
		"""SELECT COALESCE(SUM(debit - credit), 0) FROM `tabGL Entry`
		   WHERE is_cancelled = 0 AND account = %s AND voucher_type = 'Loan' AND voucher_no = %s""",
		(account, loan))[0][0], 2)
	advance = flt(frappe.db.get_value("Loan Repayment",
		{"against_loan": loan, "docstatus": 1, "reference_number": f"HP-OPEN-ADV-{loan}"}, "amount_paid"), 2)
	return round(opening - advance - _opening_credit(loan), 2)


def _opening_unearned(loan):
	return flt(frappe.db.sql(
		"""SELECT COALESCE(SUM(jea.credit - jea.debit), 0) FROM `tabJournal Entry Account` jea
		   JOIN `tabJournal Entry` je ON je.name = jea.parent
		   WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND je.user_remark LIKE %s AND jea.account = %s""",
		(loan, f"%[HP-OPEN-INT-{loan}]%", s.account("ACC_DEF_INTEREST")))[0][0], 2)


def execute(filters=None):
	filters = dict(filters or {})
	conditions = {"company": s.COMPANY, "custom_hp_converted": 1, "docstatus": ["!=", 2]}
	if filters.get("loan"):
		conditions["name"] = filters["loan"]
	loans = frappe.get_all("Loan", filters=conditions, order_by="name", limit_page_length=0, fields=[
		"name", "applicant", "custom_hp_myob_invoice", "docstatus", "status", "loan_application", "migration_date",
		"custom_hp_opening_outstanding", "custom_hp_opening_unearned", "custom_hp_dpd_at_migration", "days_past_due",
		"is_npa", "custom_hp_charges_active", "custom_hp_review_reason"])
	receivable = s.account("ACC_RECEIVABLE")
	data = []
	for loan in loans:
		row = dict(loan=loan.name, customer=loan.applicant, invoice=loan.custom_hp_myob_invoice,
			state="Submitted" if loan.docstatus == 1 else "Draft", status=loan.status,
			myob_balance=flt(loan.custom_hp_opening_outstanding, 2), myob_unearned=flt(loan.custom_hp_opening_unearned, 2),
			dpd_cutover=loan.custom_hp_dpd_at_migration, dpd_now=loan.days_past_due, npa=loan.is_npa,
			charges=loan.custom_hp_charges_active, review=loan.custom_hp_review_reason)
		problems = []
		if loan.docstatus == 1:
			row["frappe_balance"] = _opening_balance(receivable, loan.name)
			row["opening_credit"] = _opening_credit(loan.name)
			row["frappe_unearned"] = _opening_unearned(loan.name)
			if abs(row["frappe_balance"] - row["myob_balance"]) > 0.01:
				problems.append("balance")
			if abs(row["frappe_unearned"] - row["myob_unearned"]) > 0.01:
				problems.append("unearned")
			if not loan.loan_application:
				problems.append("application not linked")
		row["check"] = ", ".join(problems)
		show = filters.get("show") or "All"
		if (show == "Differences only" and not problems) or (show in ("Draft", "Submitted") and row["state"] != show):
			continue
		data.append(row)
	money = lambda f, l: dict(fieldname=f, label=l, fieldtype="Currency", width=120)
	columns = [dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=150),
		dict(fieldname="customer", label="Customer", fieldtype="Link", options="Customer", width=180),
		dict(fieldname="invoice", label="MYOB Invoice", fieldtype="Data", width=120),
		dict(fieldname="state", label="Load", fieldtype="Data", width=85), dict(fieldname="status", label="Status", fieldtype="Data", width=95),
		money("myob_balance", "MYOB Balance"), money("frappe_balance", "1101 at Cut-over"),
		money("opening_credit", "Paid Ahead (Credit)"),
		money("myob_unearned", "MYOB Unearned"), money("frappe_unearned", "2013 Opening"),
		dict(fieldname="dpd_cutover", label="DPD Cut-over", fieldtype="Int", width=90), dict(fieldname="dpd_now", label="DPD Now", fieldtype="Int", width=80),
		dict(fieldname="npa", label="NPA", fieldtype="Check", width=55), dict(fieldname="charges", label="Charges On", fieldtype="Check", width=80),
		dict(fieldname="check", label="Check", fieldtype="Data", width=160), dict(fieldname="review", label="Review Reason", fieldtype="Data", width=200)]
	opening = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": OPENING_ACCOUNT_NUMBER}, "name")
	temp = flt(frappe.db.sql("SELECT COALESCE(SUM(debit - credit), 0) FROM `tabGL Entry` WHERE is_cancelled = 0 AND account = %s",
		(opening,))[0][0], 2) if opening else 0
	drafts = [r for r in data if r["state"] == "Draft"]
	summary = [dict(value=len(data), label="Converted loans", datatype="Int", indicator="Blue"),
		dict(value=len(drafts), label="Still in Draft", datatype="Int", indicator="Orange" if drafts else "Green"),
		dict(value=round(sum(r["myob_balance"] for r in drafts), 2), label="Draft balance", datatype="Currency", indicator="Orange"),
		dict(value=sum(1 for r in data if r["check"]), label="Differences", datatype="Int", indicator="Red" if any(r["check"] for r in data) else "Green"),
		dict(value=temp, label="1910 Temporary Opening", datatype="Currency", indicator="Green" if not temp else "Orange")]
	return columns, data, None, None, summary
