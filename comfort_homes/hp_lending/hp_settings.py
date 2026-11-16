"""Account names, defaults and a setup check for the Gross HP model.

Everything that names a ledger account lives here, so a rename in the Chart of
Accounts is a one-file change.  ``check_setup`` reports anything missing on a
site, which is the first thing to run after the Chart of Accounts is imported.
"""

import frappe

COMPANY = "Comfort Home Furnishing PTE Limited"
CHD_SUPPLIER = "Comfort Home Distribution PTE Limited"
HP_ITEM_CODE = "HP Product"  # the item the LPO script (Auto Create Draft PO) already uses

# Ledger accounts are found by ACCOUNT NUMBER, not by name.  The Chart of
# Accounts Importer builds names as "<number> - <name> - <abbr>", so names differ
# between sites, but the numbers in setup_data/chart_of_accounts_flexi.csv do not.
ACCOUNT_NUMBERS = {
	"ACC_RECEIVABLE": "1101",       # HP Receivables - Current (loan account)
	"ACC_CLEARING": "1180",         # HP Goods Clearing Contra (owed to CHD)
	"ACC_DEPOSITS": "2010",         # Customer HP Deposits
	"ACC_DEF_INTEREST": "2013",     # Deferred HP Interest
	"ACC_DEF_PENALTY": "2015",      # Deferred Income - Penalty Fees
	"ACC_INTEREST_INCOME": "4001",  # HP Interest Income
	"ACC_ADMIN_FEE": "4002",        # Admin Fee Income
	"ACC_DOC_FEE": "4003",          # Documentation Fee Income
	"ACC_PENALTY_INCOME": "4004",   # Default - Penalty Income
	"ACC_VAT_OUTPUT": "2310",       # VAT - GST Output Tax
	"ACC_CHD_PAYABLE": "2111",      # Trade Payables - related parties - CHD
	"ACC_BAD_DEBT": "5008",         # Bad Debt Expense
	"ACC_EARLY_SETTLEMENT": "5027", # Early Settlement Discounts
}
ACCOUNT_LABELS = {
	"ACC_RECEIVABLE": "HP Receivables - Current", "ACC_CLEARING": "HP Goods Clearing Contra",
	"ACC_DEPOSITS": "Customer HP Deposits", "ACC_DEF_INTEREST": "Deferred HP Interest",
	"ACC_DEF_PENALTY": "Deferred Income - Penalty Fees", "ACC_INTEREST_INCOME": "HP Interest Income",
	"ACC_ADMIN_FEE": "Admin Fee Income", "ACC_DOC_FEE": "Documentation Fee Income",
	"ACC_PENALTY_INCOME": "Default - Penalty Income", "ACC_VAT_OUTPUT": "VAT - GST Output Tax",
	"ACC_CHD_PAYABLE": "Trade Payables - related parties - CHD", "ACC_BAD_DEBT": "Bad Debt Expense",
	"ACC_EARLY_SETTLEMENT": "Early Settlement Discounts",
}
REQUIRED_ACCOUNTS = list(ACCOUNT_NUMBERS)


def account(key, company=None):
	"""Account name for a key such as ``ACC_DEF_INTEREST``; throws if the Chart lacks it."""
	name = frappe.db.get_value(
		"Account", {"company": company or COMPANY, "account_number": ACCOUNT_NUMBERS[key], "is_group": 0}, "name"
	)
	if not name:
		frappe.throw(
			f"Account number {ACCOUNT_NUMBERS[key]} ({ACCOUNT_LABELS[key]}) is missing from the Chart of Accounts. "
			"Import setup_data/chart_of_accounts_flexi.csv."
		)
	return name


def __getattr__(name):
	# ``s.ACC_DEF_INTEREST`` keeps working: it is looked up when first used.
	if name in ACCOUNT_NUMBERS:
		return account(name)
	raise AttributeError(name)


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
	for key, number in ACCOUNT_NUMBERS.items():
		row = frappe.db.get_value(
			"Account", {"company": COMPANY, "account_number": number}, ["name", "disabled", "is_group"], as_dict=True
		)
		if not row:
			problems.append(f"Missing account {number} - {ACCOUNT_LABELS[key]}")
		elif row.disabled or row.is_group:
			problems.append(f"Account {row.name} must be enabled and not a group")
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
