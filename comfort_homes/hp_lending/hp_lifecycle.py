"""Gross HP booking at disbursement, and the deposit gate.

Lending books the native disbursement as  Dr HP Receivable / Cr Clearing  for
the loan amount (the balance after the deposit).  This module adds ONE journal
entry on top, so the ledger reads as the agreed Gross HP booking.  Nothing the
native disbursement posted is cancelled or edited.
"""

import frappe
from frappe import _
from frappe.utils import flt

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

TYPE_RECLASS = "Gross Booking Reclass"


def _amounts(application, loan_amount):
	return dict(
		loan_amount=loan_amount,
		deposit=application.get("custom_flexi_deposit_amount"),
		goods_value=application.get("custom_flexi_cash_price"),
		term_charges=application.get("custom_flexi_term_charges"),
		admin_fee=application.get("custom_flexi_admin_fee"),
		doc_fee_net=application.get("custom_flexi_doc_fee_net"),
		doc_fee_vat=application.get("custom_flexi_doc_fee_vat"),
	)


def on_disbursement_submit(doc, method=None):
	"""Post the Gross HP reclass once, when the Loan Disbursement is submitted."""
	loan = frappe.get_doc("Loan", doc.against_loan)
	if loan.company != s.COMPANY or not loan.get("loan_application"):
		return
	if s.entry_exists(TYPE_RECLASS, loan.name):
		return

	application = frappe.get_doc("Loan Application", loan.loan_application)
	if flt(doc.disbursed_amount, 2) != flt(application.get("custom_flexi_total_receivable"), 2):
		frappe.throw(
			_("Disbursed amount {0} must equal the contract balance after deposit, {1}.").format(
				doc.disbursed_amount, application.get("custom_flexi_total_receivable")
			),
			title=_("HP Disbursement"),
		)

	try:
		lines = m.reclass_entry(**_amounts(application, doc.disbursed_amount))
	except ValueError as e:
		frappe.throw(str(e), title=_("HP Disbursement"))

	party = {"party_type": "Customer", "party": loan.applicant}
	rows = []
	if lines["debit"]["deposit"]:
		rows.append({"account": s.ACC_DEPOSITS, "debit_in_account_currency": float(lines["debit"]["deposit"]), **party})
	clearing = lines["debit"]["clearing"]
	if clearing:
		side = "debit_in_account_currency" if clearing > 0 else "credit_in_account_currency"
		rows.append({"account": s.ACC_CLEARING, side: float(abs(clearing))})
	for key, account in (
		("deferred_interest", s.ACC_DEF_INTEREST),
		("admin_fee", s.ACC_ADMIN_FEE),
		("doc_fee", s.ACC_DOC_FEE),
		("vat", s.ACC_VAT_OUTPUT),
	):
		if lines["credit"][key]:
			rows.append({"account": account, "credit_in_account_currency": float(lines["credit"][key]),
				"cost_center": loan.get("cost_center")})

	je = frappe.get_doc({
		"doctype": "Journal Entry",
		"voucher_type": "Journal Entry",
		"company": loan.company,
		"posting_date": doc.disbursement_date,
		"custom_hp_loan": loan.name,
		"custom_hp_entry_type": TYPE_RECLASS,
		"user_remark": _("Gross HP booking for {0} ({1}) [HP-RECLASS-{2}]").format(loan.name, application.name, doc.name),
		"accounts": rows,
	})
	je.insert(ignore_permissions=True)
	je.submit()


def check_deposit(application, require_submitted=True):
	"""Bind one receipt to one application, customer, company and FJD ledger."""
	from comfort_homes.hp_lending.hp_controls import deposit_issues
	if flt(application.get("custom_flexi_deposit_amount"), 2) <= 0:
		return
	name = application.get("custom_hp_deposit_payment_entry")
	if not name:
		frappe.throw(_("Record and link the deposit Payment Entry before moving on."))
	# Lock the receipt so competing application/disbursement checks serialize.
	frappe.db.sql("SELECT name FROM `tabPayment Entry` WHERE name = %s FOR UPDATE", (name,))
	payment = frappe.get_doc("Payment Entry", name)
	problems = deposit_issues(application, payment, s.account("ACC_DEPOSITS", application.company), require_submitted)
	bank = frappe.db.get_value("Account", payment.paid_to,
		["company", "account_type", "disabled", "is_group"], as_dict=True)
	if not bank or bank.company != application.company or bank.account_type not in ("Bank", "Cash") or bank.disabled or bank.is_group:
		problems.append("Deposit destination must be an enabled company Bank or Cash leaf account")
	other = frappe.db.exists("Loan Application", {
		"custom_hp_deposit_payment_entry": name, "name": ["!=", application.name], "docstatus": ["!=", 2]})
	if other:
		problems.append("Deposit Payment Entry is already linked to another application")
	if problems:
		frappe.throw("; ".join(problems), title=_("Deposit Required"))
	return payment


def validate_deposit_payment(doc, method=None):
	"""Gate the existing contract-to-compliance transition without an invoice."""
	previous = doc.get_doc_before_save()
	if not previous:
		return
	from comfort_homes.loan_application_workflow import CONTRACT_STATES, POST_CONTRACT_STATES
	if previous.workflow_state in CONTRACT_STATES and doc.workflow_state in POST_CONTRACT_STATES:
		check_deposit(doc)


def validate_disbursement(doc, method=None):
	"""Recheck financial authority immediately before native submission."""
	loan = frappe.get_doc("Loan", doc.against_loan)
	if loan.company != s.COMPANY or not loan.get("loan_application"):
		return
	if s.RELEASE_BLOCKERS:
		frappe.throw("; ".join(s.RELEASE_BLOCKERS))
	frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan.name,))
	application = frappe.get_doc("Loan Application", loan.loan_application)
	if application.company != loan.company or application.applicant != loan.applicant or application.docstatus != 1 or application.status != "Approved":
		frappe.throw("HP disbursement requires the matching submitted, approved customer application")
	if doc.disbursement_account != s.account("ACC_CLEARING", loan.company):
		frappe.throw("Gross HP disbursement must credit the goods clearing account")
	if doc.get("bank_account") or doc.get("withhold_security_deposit") or flt(doc.get("broken_period_interest")) or any(flt(row.get("amount")) for row in doc.get("loan_disbursement_charges") or []):
		frappe.throw("HP goods disbursement must not add a bank payout, security withholding or native charges")
	if loan.rate_of_interest != 0:
		frappe.throw("Gross HP servicing interest must be zero; do not disburse this loan")
	if flt(doc.disbursed_amount, 2) != flt(application.custom_flexi_total_receivable, 2):
		frappe.throw("HP disbursement must equal the whole approved post-deposit balance")
	if frappe.db.exists("Loan Disbursement", {"against_loan": loan.name, "docstatus": 1, "name": ["!=", doc.name]}):
		frappe.throw("This HP loan already has a submitted disbursement")
	from comfort_homes.document_controls import validate_signed_pack
	validate_signed_pack(application)
	check_deposit(application)


def enforce_clearing_account(doc, method=None):
    """Draft validation: a customer collection preference is not a goods payout."""
    loan = frappe.get_doc("Loan", doc.against_loan)
    if loan.company == s.COMPANY and loan.get("loan_application"):
        doc.disbursement_account = s.account("ACC_CLEARING", loan.company)
        doc.mode_of_payment = None
