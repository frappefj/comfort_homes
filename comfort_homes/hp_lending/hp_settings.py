"""Account names, defaults and a setup check for the Gross HP model.

Everything that names a ledger account lives here, so a rename in the Chart of
Accounts is a one-file change.  ``check_setup`` reports anything missing on a
site, which is the first thing to run after the Chart of Accounts is imported.
"""

import frappe

COMPANY = "Comfort Home Furnishing PTE Limited"
CHD_SUPPLIER = "Comfort Home Distribution PTE Limited"
HP_ITEM_CODE = "HP-GOODS"

ACC_RECEIVABLE = "HP Receivables - Current - FLEXI"
ACC_CLEARING = "1180 - HP Goods Clearing Contra - FLEXI"
ACC_DEPOSITS = "2010 - Customer HP Deposits - FLEXI"
ACC_DEF_INTEREST = "2013 - Deferred HP Interest - FLEXI"
ACC_DEF_PENALTY = "2015 - Deferred Income - Penalty Fees - FLEXI"
ACC_INTEREST_INCOME = "HP Interest Income - FLEXI"
ACC_ADMIN_FEE = "Admin Fee Income - FLEXI"
ACC_DOC_FEE = "Documentation Fee Income - FLEXI"
ACC_VAT_OUTPUT = "VAT - GST Output Tax - FLEXI"
ACC_PENALTY_INCOME = "Default - Penalty Income - FLEXI"
ACC_CHD_PAYABLE = "Trade Payables - related parties - CHD - FLEXI"

REQUIRED_ACCOUNTS = [
	ACC_RECEIVABLE, ACC_CLEARING, ACC_DEPOSITS, ACC_DEF_INTEREST, ACC_DEF_PENALTY, ACC_INTEREST_INCOME,
	ACC_ADMIN_FEE, ACC_DOC_FEE, ACC_VAT_OUTPUT, ACC_PENALTY_INCOME, ACC_CHD_PAYABLE,
]

# The loan is NPA, and late-payment charges stop, at this many days past due.
NPA_DAYS_PAST_DUE = 90
GRACE_DAYS_DEFAULT = 7
PENALTY_MONTHLY_RATE_DEFAULT = 2.5


def entry_exists(entry_type, loan, tag=None):
	"""True when an HP journal of this type already exists for the loan (idempotency)."""
	filters = {"custom_hp_loan": loan, "custom_hp_entry_type": entry_type, "docstatus": 1}
	if tag:
		filters["user_remark"] = ["like", f"%{tag}%"]
	return bool(frappe.db.exists("Journal Entry", filters))


@frappe.whitelist()
def check_setup():
	"""Report what is missing for the HP model.  Changes nothing."""
	frappe.only_for(("System Manager", "Loan Manager"))
	problems = []
	for account in REQUIRED_ACCOUNTS:
		if not frappe.db.exists("Account", account):
			problems.append(f"Missing account: {account}")
		elif frappe.db.get_value("Account", account, "disabled"):
			problems.append(f"Account is disabled: {account}")
	if not frappe.db.exists("Supplier", CHD_SUPPLIER):
		problems.append(f"Missing supplier: {CHD_SUPPLIER}")
	if not frappe.db.exists("Item", HP_ITEM_CODE):
		problems.append(f"Missing item: {HP_ITEM_CODE}")
	elif frappe.db.get_value("Item", HP_ITEM_CODE, "is_stock_item"):
		problems.append(f"Item {HP_ITEM_CODE} must not maintain stock")
	for field in ("Loan Product-custom_hp_penalty_rate_monthly", "Journal Entry-custom_hp_loan"):
		if not frappe.db.exists("Custom Field", field):
			problems.append(f"Missing custom field: {field}")
	company = frappe.db.get_value(
		"Company", COMPANY, ["days_past_due_threshold", "enable_loan_accounting"], as_dict=True
	)
	if not company:
		problems.append(f"Missing company: {COMPANY}")
	else:
		if company.days_past_due_threshold != NPA_DAYS_PAST_DUE:
			problems.append(f"Company NPA threshold is {company.days_past_due_threshold}, expected {NPA_DAYS_PAST_DUE}")
		if not company.enable_loan_accounting:
			problems.append("Loan accounting is not enabled on the Company")
	for product in frappe.get_all("Loan Product", fields=["name", "penalty_interest_rate", "rate_of_interest"]):
		if product.penalty_interest_rate:
			problems.append(f"Loan Product {product.name}: native penalty rate must be 0, is {product.penalty_interest_rate}")
	return {"ok": not problems, "problems": problems}


@frappe.whitelist()
def apply_company_settings():
	"""Set the Company NPA threshold to 90 days.  Idempotent."""
	frappe.only_for("System Manager")
	frappe.db.set_value("Company", COMPANY, "days_past_due_threshold", NPA_DAYS_PAST_DUE)
	return {"days_past_due_threshold": NPA_DAYS_PAST_DUE}
