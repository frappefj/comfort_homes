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
		"Account", {"company": company or COMPANY, "account_number": ACCOUNT_NUMBERS[key], "is_group": 0, "disabled": 0}, "name"
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
# Approved: native strict-greater-than threshold 89 flags stored DPD 90.
# Site installation and financial acceptance remain separate approvals.
RELEASE_BLOCKERS = ()
NATIVE_NPA_THRESHOLD = 89

NPA_DAYS_PAST_DUE = 90
GRACE_DAYS_DEFAULT = 7
PENALTY_MONTHLY_RATE_DEFAULT = 2.5


def entry_exists(entry_type, loan, tag=None):
	"""True when an HP journal of this type already exists for the loan (idempotency)."""
	filters = {"custom_hp_loan": loan, "custom_hp_entry_type": entry_type, "docstatus": 1}
	if tag:
		filters["user_remark"] = ["like", f"%{tag}%"]
	return bool(frappe.db.exists("Journal Entry", filters))


NATIVE_ACCOUNT_TYPES = {
	"1101": "Receivable", "2010": "Receivable", "2310": "Tax", "2111": "Payable",
	"1105": "Receivable", "1106": "Receivable", "1107": "Receivable", "1108": "Receivable",
	"2008": "Payable", "1201": "Bank", "2009": "Bank", "1111": "Cash",
	"4001": "Income Account", "4002": "Income Account", "4003": "Income Account", "4004": "Income Account",
}
REQUIRED_EXTRA_NUMBERS = ("1105", "1106", "1107", "1108", "2008", "1201", "2009", "1111", "1120", "2112", "1195", "5029", "5030", "4016", "4006")
REQUIRED_FIELDS = (
	"Loan Product-custom_hp_penalty_rate_monthly", "Loan Product-custom_hp_penalty_stop_dpd",
	"Journal Entry-custom_hp_loan", "Journal Entry-custom_hp_entry_type", "Loan-custom_hp_contract_rate",
	"Loan Application-custom_hp_deposit_payment_entry", "Loan Application-custom_flexi_doc_fee_net",
	"Loan Application-custom_flexi_doc_fee_vat", "Loan Application-custom_flexi_gross_contract_value",
	"Loan Document Template-print_format",
)


@frappe.whitelist()
def check_setup():
	"""Configuration diagnostics only: an ok result never certifies native postings."""
	frappe.only_for(("System Manager", "Loan Manager"))
	problems = list(RELEASE_BLOCKERS)
	account_problems = []
	for number in sorted(set(ACCOUNT_NUMBERS.values()) | set(REQUIRED_EXTRA_NUMBERS)):
		row = frappe.db.get_value("Account", {"company": COMPANY, "account_number": number},
			["name", "disabled", "is_group", "account_type"], as_dict=True)
		if not row:
			account_problems.append("Missing account " + number)
		elif row.disabled or row.is_group:
			account_problems.append("Account " + number + " must be enabled and not a group")
		elif number in NATIVE_ACCOUNT_TYPES and row.account_type != NATIVE_ACCOUNT_TYPES[number]:
			account_problems.append("Account " + number + " must have type " + NATIVE_ACCOUNT_TYPES[number])
	problems.extend(account_problems)
	for field in REQUIRED_FIELDS:
		if not frappe.db.exists("Custom Field", field):
			problems.append("Missing custom field: " + field)
	for dt, name in (("Company", COMPANY), ("Supplier", CHD_SUPPLIER), ("Item", HP_ITEM_CODE),
		("Loan Product", "HP-STD"), ("Workflow", "Loan Application Workflow-1"),
		("Web Form", "loan-application-new"), ("Report", "HP Customer Statement"),
		("Workflow Transition Tasks", "Flexi Generate LPO"), ("Workflow Transition Tasks", "Flexi Loan Application Transition Tasks")):
		if not frappe.db.exists(dt, name):
			problems.append("Missing " + dt + ": " + name)
	company_exists = frappe.db.exists("Company", COMPANY)
	if company_exists:
		company = frappe.get_doc("Company", COMPANY)
		if company.default_currency != "FJD" or company.country != "Fiji":
			problems.append("Company must use FJD and Fiji")
		if not account_problems:
			defaults = {"default_bank_account": "1201", "default_cash_account": "1111", "default_receivable_account": "1120", "default_payable_account": "2112", "write_off_account": "5008", "round_off_account": "1195"}
			for field, number in defaults.items():
				expected = frappe.db.get_value("Account", {"company": COMPANY, "account_number": number}, "name")
				if company.get(field) != expected:
					problems.append("Company " + field + " must use account " + number)
	if not account_problems and company_exists:
		from comfort_homes.hp_lending.hp_setup import field_diffs
		for change in field_diffs():
			problems.append(change["doctype"] + " " + change["name"] + ": " + change["field"] + " differs from the approved setup")
	for product in frappe.get_all("Loan Product", filters={"company": COMPANY}, fields=["name", "penalty_interest_rate"]):
		if product.penalty_interest_rate:
			problems.append("Loan Product " + product.name + ": native penalty rate must be 0")
	return {"ok": not problems, "problems": problems, "native_acceptance_tested": False, "release_blockers": list(RELEASE_BLOCKERS)}


@frappe.whitelist(methods=["POST"])
def apply_company_settings(dry_run=1):
	"""Set the Company NPA threshold to 90 days.  Idempotent."""
	frappe.only_for("System Manager")
	before = frappe.db.get_value("Company", COMPANY, "days_past_due_threshold")
	if not int(dry_run):
		frappe.db.set_value("Company", COMPANY, "days_past_due_threshold", NPA_DAYS_PAST_DUE)
	return {"dry_run": int(dry_run), "before": before, "days_past_due_threshold": NPA_DAYS_PAST_DUE}
