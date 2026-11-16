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
	loan = frappe.get_cached_doc("Loan", doc.against_loan)
	if not loan.get("loan_application"):
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


def validate_deposit_payment(doc, method=None):
	"""A submitted deposit Payment Entry is required before the contract moves on.

	Replaces the earlier Sales Invoice gate.  Flexi raises no customer invoice
	for the deposit; the Payment Entry is the proof of payment.
	"""
	previous = doc.get_doc_before_save()
	if not previous:
		return
	from comfort_homes.loan_application_workflow import CONTRACT_STATES, POST_CONTRACT_STATES

	if previous.workflow_state not in CONTRACT_STATES or doc.workflow_state not in POST_CONTRACT_STATES:
		return
	deposit = flt(doc.get("custom_flexi_deposit_amount"), 2)
	if deposit <= 0:
		return

	entry = doc.get("custom_hp_deposit_payment_entry")
	if not entry:
		frappe.throw(_("Record the deposit as a Payment Entry and link it before moving on."), title=_("Deposit Required"))
	pe = frappe.db.get_value(
		"Payment Entry", entry, ["docstatus", "payment_type", "party", "paid_amount"], as_dict=True
	)
	if not pe or pe.docstatus != 1:
		frappe.throw(_("The deposit Payment Entry {0} must be submitted.").format(entry), title=_("Deposit Required"))
	if pe.payment_type != "Receive":
		frappe.throw(_("{0} is not a received payment.").format(entry), title=_("Deposit Required"))
	if flt(pe.paid_amount, 2) != deposit:
		frappe.throw(
			_("Deposit Payment Entry {0} is {1}, but the deposit on the application is {2}.").format(
				entry, pe.paid_amount, deposit
			),
			title=_("Deposit Required"),
		)
