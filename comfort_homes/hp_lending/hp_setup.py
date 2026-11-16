"""One-time master data for the HP model.  Idempotent, and a dry run by default.

Run order on a new site (see docs/CONFIGURATION_GUIDE.md):
  1. Company exists, Chart of Accounts imported from setup_data/chart_of_accounts_flexi.csv
  2. ``setup_masters(dry_run=0)``   creates CHD supplier, HP-GOODS item, Loan Product, offset order
  3. ``check_setup()``              must return ok
"""

import frappe

from comfort_homes.hp_lending import hp_settings as s

PRODUCT_CODE = "HP-STD"
PRODUCT_NAME = "Hire Purchase - Standard"
DEFAULT_CONTRACT_RATE = 22  # flat % per annum, shown on agreements; the loan itself runs at 0%


def _acc(key):
	return s.account(key)


def _by_number(number):
	return frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": number, "is_group": 0}, "name")


def _plan():
	"""What would be created or changed.  Reads only."""
	plan = [f"Create role {r}" for r in WORKFLOW_ROLES if not frappe.db.exists("Role", r)]
	if not frappe.db.exists("Supplier", s.CHD_SUPPLIER):
		plan.append("Create Supplier " + s.CHD_SUPPLIER)
	if not frappe.db.exists("Item", s.HP_ITEM_CODE):
		plan.append("Create non-stock Item " + s.HP_ITEM_CODE)
	if not frappe.db.exists("Loan Product", PRODUCT_CODE):
		plan.append("Create Loan Product " + PRODUCT_CODE)
	if not frappe.db.exists("Loan Demand Offset Order", "Standard Loan Demand Offset Order"):
		plan.append("Create Loan Demand Offset Order 'Standard Loan Demand Offset Order'")
	company = frappe.db.get_value("Company", s.COMPANY, ["enable_loan_accounting", "days_past_due_threshold"], as_dict=True)
	if company and not company.enable_loan_accounting:
		plan.append("Enable loan accounting on the Company")
	if company and company.days_past_due_threshold != s.NPA_DAYS_PAST_DUE:
		plan.append(f"Set Company NPA threshold to {s.NPA_DAYS_PAST_DUE} days")
	return plan


def _offset_order():
	title = "Standard Loan Demand Offset Order"
	if not frappe.db.exists("Loan Demand Offset Order", title):
		doc = frappe.new_doc("Loan Demand Offset Order")
		doc.title = title
		for demand_type in ("Penalty", "Interest", "Principal"):
			doc.append("components", {"demand_type": demand_type})
		doc.insert(ignore_permissions=True)
	return title


WORKFLOW_ROLES = [
	"Loan LOS User", "Loan Officer", "Loan Processor", "Finance Manager", "Director",
	"Disbursement Officer", "Accounts Officer", "Accounts Manager", "Loan Manager", "Loan Reporter", "Auditor",
]


def _roles():
	for role in WORKFLOW_ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def _company():
	order = _offset_order()
	company = frappe.get_doc("Company", s.COMPANY)
	company.enable_loan_accounting = 1
	company.days_past_due_threshold = s.NPA_DAYS_PAST_DUE
	for field in (
		"collection_offset_sequence_for_standard_asset",
		"collection_offset_sequence_for_sub_standard_asset",
		"collection_offset_sequence_for_written_off_asset",
		"collection_offset_sequence_for_settlement_collection",
	):
		if company.meta.has_field(field) and not company.get(field):
			company.set(field, order)
	company.save(ignore_permissions=True)


def _supplier():
	if frappe.db.exists("Supplier", s.CHD_SUPPLIER):
		return
	group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name") or "All Supplier Groups"
	supplier = frappe.get_doc({
		"doctype": "Supplier",
		"supplier_name": s.CHD_SUPPLIER,
		"supplier_group": group,
		"supplier_type": "Company",
		"default_currency": "FJD",
		"accounts": [{"company": s.COMPANY, "account": _acc("ACC_CHD_PAYABLE")}],
	})
	supplier.insert(ignore_permissions=True)


def _item():
	if frappe.db.exists("Item", s.HP_ITEM_CODE):
		return
	group = frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups"
	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": s.HP_ITEM_CODE,
		"item_name": "Hire Purchase Goods (supplied by CHD)",
		"item_group": group,
		"stock_uom": "Nos",
		"is_stock_item": 0,
		"is_purchase_item": 1,
		"is_sales_item": 0,
		# CHD's invoice debits the clearing account, which the customer's contract already credits.
		"item_defaults": [{"company": s.COMPANY, "expense_account": _acc("ACC_CLEARING")}],
	})
	item.insert(ignore_permissions=True)


def _loan_product():
	if frappe.db.exists("Loan Product", PRODUCT_CODE):
		return
	order = _offset_order()
	bank = frappe.db.get_value("Account", {"company": s.COMPANY, "account_type": "Bank", "is_group": 0}, "name")
	recovery = _by_number("4006")
	product = frappe.get_doc({
		"doctype": "Loan Product",
		"company": s.COMPANY,
		"product_code": PRODUCT_CODE,
		"product_name": PRODUCT_NAME,
		"rate_of_interest": DEFAULT_CONTRACT_RATE,
		"is_term_loan": 1,
		"repayment_schedule_type": "Monthly as per repayment start date",
		"advance_payment_handling": "Reduce Tenure",
		"penalty_interest_rate": 0,
		"grace_period_in_days": s.GRACE_DAYS_DEFAULT,
		"days_past_due_threshold_for_npa": s.NPA_DAYS_PAST_DUE,
		"emi_rounding_method": "Round to Nearest",
		"custom_hp_penalty_rate_monthly": s.PENALTY_MONTHLY_RATE_DEFAULT,
		"custom_hp_penalty_stop_dpd": s.NPA_DAYS_PAST_DUE,
		# Lending requires all of these when loan accounting is on.
		"loan_account": _acc("ACC_RECEIVABLE"),
		"disbursement_account": _acc("ACC_CLEARING"),
		"payment_account": bank,
		"security_deposit_account": _acc("ACC_DEPOSITS"),
		"customer_refund_account": _by_number("2008"),
		"interest_income_account": _acc("ACC_INTEREST_INCOME"),
		"interest_accrued_account": _by_number("1105"),
		"interest_receivable_account": _by_number("1106"),
		"interest_waiver_account": _by_number("5029"),
		"broken_period_interest_recovery_account": _by_number("4016"),
		"penalty_income_account": _acc("ACC_PENALTY_INCOME"),
		"penalty_accrued_account": _by_number("1107"),
		"penalty_receivable_account": _by_number("1108"),
		"penalty_waiver_account": _by_number("5030"),
		"write_off_account": _acc("ACC_BAD_DEBT"),
		"write_off_recovery_account": recovery,
		"collection_offset_sequence_for_standard_asset": order,
		"collection_offset_sequence_for_sub_standard_asset": order,
		"collection_offset_sequence_for_written_off_asset": order,
		"collection_offset_sequence_for_settlement_collection": order,
	})
	product.insert(ignore_permissions=True)


@frappe.whitelist()
def setup_masters(dry_run=1):
	"""Create the HP master data.  ``dry_run`` (default) only lists what it would do."""
	frappe.only_for("System Manager")
	dry_run = int(dry_run)
	if not frappe.db.exists("Company", s.COMPANY):
		frappe.throw(f"Company {s.COMPANY} does not exist yet.")
	plan = _plan()
	if dry_run:
		return {"dry_run": 1, "would_do": plan}
	_roles()
	_company()
	_supplier()
	_item()
	_loan_product()
	frappe.db.commit()
	return {"dry_run": 0, "done": plan, "check": s.check_setup()}
