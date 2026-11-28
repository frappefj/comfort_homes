"""Bank PPT Authority on the Loan (7.1.6).

Customers who pay by bank periodical payment (PPT) take a signed authority to their bank. It is filled from
the loan: the customer's bank and account (from the application), the Comfort account at the same bank
(faster and cheaper for the customer), frequency, instalment, start and end dates, and the narration the bank
must use: the contract's HP account number, nothing else. One authority per loan.

Prepare (Loan Officer and up) -> Verify (Disbursement / Accounts Officer, Accounts or Finance Manager; not the
preparer) -> print and sign -> attach the bank-stamped copy (Bank Confirmed). A change to any printed detail
after verification sends it back to Prepared. Until verified the print carries DRAFT. A verified customer
account number is saved on the customer, so receipts from that account match even without the narration.
"""

import hashlib
import json

import frappe
from frappe import _
from frappe.utils import cint, getdate, today

PREPARE_ROLES = ("Loan Officer", "Loan Manager", "Disbursement Officer", "Accounts Officer", "Accounts Manager",
	"Finance Manager", "System Manager")
VERIFY_ROLES = ("Disbursement Officer", "Accounts Officer", "Accounts Manager", "Finance Manager", "System Manager")
CREDIT_ACCOUNTS = {
	"BSP": "BSP Bank - 87349722", "Westpac": "Westpac Bank - 9808559661", "ANZ": "ANZ Bank - 13884586",
	"BRED": "Bred Bank - 014355100010", "HFC": "HFC Bank - 100093479",
}
FREQUENCY = {"Weekly": "Weekly", "Bi-Weekly": "Fortnightly", "Fortnightly": "Fortnightly", "Monthly": "Monthly",
	"Bi-Monthly": "Bi-Monthly", "Daily": "Daily"}
DETAIL_FIELDS = ("custom_hp_ppt_customer_bank", "custom_hp_ppt_customer_account", "custom_hp_ppt_credit_account",
	"custom_hp_ppt_frequency", "custom_hp_ppt_amount", "custom_hp_ppt_start_date", "custom_hp_ppt_end_date",
	"custom_hp_ppt_reference")
REQUIRED = DETAIL_FIELDS[:6] + ("custom_hp_ppt_reference",)


def bank_key(name):
	"""Pure: "BSP", "Westpac Banking Corporation", "bred bank" -> BSP / Westpac / BRED; "" when unknown."""
	text = str(name or "").upper()
	for key, words in (("BSP", ("BSP", "SOUTH PACIFIC")), ("Westpac", ("WESTPAC", "WBC")), ("ANZ", ("ANZ",)),
			("BRED", ("BRED",)), ("HFC", ("HFC",))):
		if any(w in text for w in words):
			return key
	return ""


def frequency(loan_frequency, application_period=None):
	"""Pure: the authority's frequency; twice-monthly contracts (held as fortnightly) print Bi-Monthly."""
	if str(application_period or "").strip().lower() in ("bi-monthly", "bimonthly", "twice monthly"):
		return "Bi-Monthly"
	return FREQUENCY.get(str(loan_frequency or "").strip(), "")


def fingerprint(values):
	"""Pure: hash of the printed details."""
	payload = json.dumps([str(values.get(f) or "") for f in DETAIL_FIELDS])
	return hashlib.sha256(payload.encode()).hexdigest()


def proposal(loan, application, schedule):
	"""Pure: the details filled from the loan. ``schedule`` = {"instalment", "next_due", "maturity"}."""
	application = application or {}
	bank = bank_key(application.get("custom_customer_bank_name"))
	return {
		"custom_hp_ppt_customer_bank": bank,
		"custom_hp_ppt_customer_account": str(application.get("custom_bank_account_number") or "").strip(),
		"custom_hp_ppt_credit_account": CREDIT_ACCOUNTS.get(bank, ""),
		"custom_hp_ppt_frequency": frequency(loan.get("repayment_frequency"), application.get("custom_flexi_payment_period")),
		"custom_hp_ppt_amount": schedule.get("instalment") or 0,
		"custom_hp_ppt_start_date": schedule.get("next_due") or today(),
		"custom_hp_ppt_end_date": schedule.get("maturity"),
		"custom_hp_ppt_reference": str(application.get("custom_flexi_hp_account_number") or "").strip(),
	}


def now_datetime():
	from frappe.utils import now_datetime as frappe_now
	return frappe_now()


def _require(roles):
	if not set(roles) & set(frappe.get_roles()):
		frappe.throw(_("You do not have a role that can do this."), frappe.PermissionError)


def _schedule(loan_name):
	rows = frappe.db.sql(
		"""SELECT rs.payment_date, rs.total_payment, rs.demand_generated FROM `tabRepayment Schedule` rs
		   JOIN `tabLoan Repayment Schedule` lrs ON lrs.name = rs.parent
		   WHERE lrs.loan = %s AND lrs.docstatus = 1 AND lrs.status = 'Active' ORDER BY rs.payment_date""",
		(loan_name,), as_dict=True)
	if not rows:
		return {}
	upcoming = [r for r in rows if getdate(r.payment_date) >= getdate(today())]
	return {"instalment": rows[0].total_payment, "next_due": upcoming[0].payment_date if upcoming else None,
		"maturity": rows[-1].payment_date}


@frappe.whitelist(methods=["POST"])
def prepare(loan, reset=0):
	"""Fill the authority from the loan (blank fields only, or all with ``reset``) and mark it Prepared."""
	_require(PREPARE_ROLES)
	doc = frappe.get_doc("Loan", loan)
	doc.check_permission("write")
	if doc.docstatus != 1:
		frappe.throw(_("The loan must be submitted first."))
	application = frappe.get_doc("Loan Application", doc.loan_application).as_dict() if doc.get("loan_application") else {}
	for field, value in proposal(doc, application, _schedule(doc.name)).items():
		if field == "custom_hp_ppt_reference" or cint(reset) or not doc.get(field):
			doc.set(field, value)
	if not doc.get("custom_hp_ppt_reference"):
		frappe.throw(_("This contract has no HP account number yet, so the bank would have no narration to use."))
	doc.update({"custom_hp_ppt_status": "Prepared", "custom_hp_ppt_prepared_by": frappe.session.user,
		"custom_hp_ppt_prepared_on": now_datetime(), "custom_hp_ppt_verified_by": None, "custom_hp_ppt_verified_on": None,
		"custom_hp_ppt_fingerprint": None})
	doc.flags.hp_ppt_action = True
	doc.save()
	return {"status": doc.custom_hp_ppt_status}


@frappe.whitelist(methods=["POST"])
def verify(loan):
	"""A second person confirms the details against the customer's bank documents."""
	_require(VERIFY_ROLES)
	doc = frappe.get_doc("Loan", loan)
	doc.check_permission("write")
	if doc.get("custom_hp_ppt_status") != "Prepared":
		frappe.throw(_("Prepare the Bank PPT Authority first."))
	if doc.get("custom_hp_ppt_prepared_by") == frappe.session.user and frappe.session.user != "Administrator":
		frappe.throw(_("The person who prepared the authority cannot verify it."), title=_("Second person needed"))
	missing = [doc.meta.get_label(f) for f in REQUIRED if not doc.get(f)]
	if missing:
		frappe.throw(_("Complete before verifying: {0}").format(", ".join(missing)))
	doc.update({"custom_hp_ppt_status": "Verified", "custom_hp_ppt_verified_by": frappe.session.user,
		"custom_hp_ppt_verified_on": now_datetime(), "custom_hp_ppt_fingerprint": fingerprint(doc.as_dict())})
	doc.flags.hp_ppt_action = True
	doc.save()
	remember_payer_account(doc.applicant, doc.custom_hp_ppt_customer_account)
	return {"status": doc.custom_hp_ppt_status}


def remember_payer_account(customer, account):
	account = "".join(ch for ch in str(account or "") if ch.isdigit())
	if not customer or len(account) < 6 or not frappe.db.has_column("Customer", "custom_hp_payer_accounts"):
		return
	current = str(frappe.db.get_value("Customer", customer, "custom_hp_payer_accounts") or "")
	accounts = [a for a in current.replace(";", ",").split(",") if a.strip()]
	if account not in [a.strip() for a in accounts]:
		frappe.db.set_value("Customer", customer, "custom_hp_payer_accounts", ", ".join(accounts + [account]),
			update_modified=False)


def on_loan_update(doc, method=None):
	"""Loan before_update_after_submit: a changed detail undoes verification; a stamped copy confirms it."""
	if doc.flags.get("hp_ppt_action"):
		return
	status = doc.get("custom_hp_ppt_status")
	if status in ("Verified", "Bank Confirmed") and doc.get("custom_hp_ppt_fingerprint") != fingerprint(doc.as_dict()):
		doc.update({"custom_hp_ppt_status": "Prepared", "custom_hp_ppt_verified_by": None, "custom_hp_ppt_verified_on": None,
			"custom_hp_ppt_fingerprint": None})
		frappe.msgprint(_("Bank PPT details changed: the authority must be verified again before it is printed."), alert=True)
		return
	if status == "Verified" and doc.get("custom_hp_ppt_stamped_copy"):
		doc.custom_hp_ppt_status = "Bank Confirmed"


def print_data(doc):
	"""For the print format: the values plus whether this is a verified (non-draft) authority."""
	values = doc.as_dict() if hasattr(doc, "as_dict") else dict(doc)
	verified = values.get("custom_hp_ppt_status") in ("Verified", "Bank Confirmed") and \
		values.get("custom_hp_ppt_fingerprint") == fingerprint(values)
	full_name = lambda user: frappe.db.get_value("User", user, "full_name") if user else ""
	return {"verified": verified, "banks": list(CREDIT_ACCOUNTS), "credit_accounts": list(CREDIT_ACCOUNTS.values()),
		"frequencies": ["Daily", "Weekly", "Fortnightly", "Bi-Monthly", "Monthly"],
		"prepared_by": full_name(values.get("custom_hp_ppt_prepared_by")),
		"verified_by": full_name(values.get("custom_hp_ppt_verified_by")),
		"customer": values.get("custom_customer_full_name") or values.get("applicant_name") or values.get("applicant")}


@frappe.whitelist()
def card_ppt_outstanding():
	"""Number card: verified authorities whose bank-stamped copy has not come back."""
	count = frappe.db.count("Loan", {"docstatus": 1, "custom_hp_ppt_status": "Verified"}) \
		if frappe.db.has_column("Loan", "custom_hp_ppt_status") else 0
	return {"value": count, "fieldtype": "Int",
		"route": ["List", "Loan", {"custom_hp_ppt_status": "Verified"}]}
