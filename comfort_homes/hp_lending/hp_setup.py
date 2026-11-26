"""One-time master data for the HP model.  Idempotent, and a dry run by default.

Run order on a new site (see docs/CONFIGURATION_GUIDE.md):
  1. Company exists, Chart of Accounts imported from setup_data/chart_of_accounts_flexi.csv
  2. ``setup_masters(dry_run=0)``   creates CHD supplier, HP Product item, Loan Product, offset order
  3. ``check_setup()``              must return ok
"""

import frappe

from comfort_homes.hp_lending import hp_settings as s

OFFSET_FIELDS = ['collection_offset_sequence_for_standard_asset', 'collection_offset_sequence_for_sub_standard_asset', 'collection_offset_sequence_for_written_off_asset', 'collection_offset_sequence_for_settlement_collection']

PRODUCT_CODE = "HP-STD"
PRODUCT_NAME = "Hire Purchase - Standard"
DEFAULT_CONTRACT_RATE = 22  # flat % per annum, shown on agreements; the loan itself runs at 0%


def _acc(key):
	return s.account(key)


def _by_number(number):
	return frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": number, "is_group": 0, "disabled": 0}, "name")


def _plan():
	"""What would be created or changed.  Reads only."""
	plan = [f"Create role {r}" for r in WORKFLOW_ROLES if not frappe.db.exists("Role", r)]
	for dt, name in (("Supplier Group", "Distributor"), ("Item Group", "Products")):
		if not frappe.db.exists(dt, name):
			plan.append("Create " + dt + " " + name)
	if not frappe.db.exists("Supplier", s.CHD_SUPPLIER):
		plan.append("Create Supplier " + s.CHD_SUPPLIER)
	if not frappe.db.exists("Item", s.HP_ITEM_CODE):
		plan.append(f"Create non-stock Item '{s.HP_ITEM_CODE}'")
	if not frappe.db.exists("Loan Product", PRODUCT_CODE):
		plan.append("Create Loan Product " + PRODUCT_CODE)
	plan += [f"Create Mode of Payment {mode} (label only, no account)" for mode in _label_modes() if not frappe.db.exists("Mode of Payment", mode)]
	if not frappe.db.exists("Loan Demand Offset Order", "Flexi HP Demand Offset Order"):
		plan.append("Create Loan Demand Offset Order 'Flexi HP Demand Offset Order'")
	company = frappe.db.get_value("Company", s.COMPANY, ["enable_loan_accounting", "days_past_due_threshold"], as_dict=True)
	if company and not company.enable_loan_accounting:
		plan.append("Enable loan accounting on the Company")
	if company and company.days_past_due_threshold != s.NPA_DAYS_PAST_DUE:
		plan.append(f"Set Company NPA threshold to {s.NPA_DAYS_PAST_DUE} days")
	return plan


def _offset_order():
	title = "Flexi HP Demand Offset Order"
	exists = frappe.db.exists("Loan Demand Offset Order", title)
	doc = frappe.get_doc("Loan Demand Offset Order", title) if exists else frappe.new_doc("Loan Demand Offset Order")
	wanted = ["Penalty", "Interest", "Principal"]
	if not exists or [row.demand_type for row in doc.get("components") or []] != wanted:
		doc.title = title
		doc.set("components", [])
		for demand_type in wanted:
			doc.append("components", {"demand_type": demand_type})
		if exists:
			doc.save(ignore_permissions=True)
		else:
			doc.insert(ignore_permissions=True)
	return title


WORKFLOW_ROLES = [
	"Loan LOS User", "Loan Officer", "Loan Processor", "Finance Manager", "Director",
	"Disbursement Officer", "Accounts Officer", "Accounts Manager", "Loan Manager", "Loan Reporter", "Auditor",
]


def _label_modes():
	"""7.1.3: every Mode of Payment an agreed payment method maps to.  They are labels: no default account,
	so staff choose the receiving account on any payment they record by hand."""
	from comfort_homes.loan_mapping import FALLBACK_MODE_OF_PAYMENT, MODE_OF_PAYMENT_MAP

	return sorted(set(MODE_OF_PAYMENT_MAP.values()) | {FALLBACK_MODE_OF_PAYMENT})


def _modes():
	for mode in _label_modes():
		if not frappe.db.exists("Mode of Payment", mode):
			frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": mode, "enabled": 1,
				"type": "Cash" if mode == "Cash" else "General"}).insert(ignore_permissions=True)


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
		if company.meta.has_field(field):
			company.set(field, order)
	company.save(ignore_permissions=True)


def _ensure_group(doctype, name, name_field, parent_field, parent):
	if frappe.db.exists(doctype, name):
		return name
	if not frappe.db.exists(doctype, parent):
		frappe.throw("Configure the native root group " + parent + " before setup")
	frappe.get_doc({"doctype": doctype, name_field: name,
		parent_field: parent, "is_group": 0}).insert(ignore_permissions=True)
	return name


def _supplier():
	group = _ensure_group("Supplier Group", "Distributor", "supplier_group_name", "parent_supplier_group", "All Supplier Groups")
	if frappe.db.exists("Supplier", s.CHD_SUPPLIER):
		_update_existing_mapping("Supplier", s.CHD_SUPPLIER, "accounts", "account", _acc("ACC_CHD_PAYABLE"), {"default_currency": "FJD", "supplier_group": group, "supplier_type": "Company"})
		return
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
	group = _ensure_group("Item Group", "Products", "item_group_name", "parent_item_group", "All Item Groups")
	if frappe.db.exists("Item", s.HP_ITEM_CODE):
		_update_existing_mapping("Item", s.HP_ITEM_CODE, "item_defaults", "expense_account", _acc("ACC_CLEARING"), {"is_stock_item": 0, "is_sales_item": 0, "is_purchase_item": 1, "item_group": group, "stock_uom": "Unit"})
		return
	item = frappe.get_doc({
		"doctype": "Item",
		"item_code": s.HP_ITEM_CODE,
		"item_name": "HP Product",
		"item_group": group,
		"stock_uom": "Unit",
		"is_stock_item": 0,
		"is_purchase_item": 1,
		"is_sales_item": 0,
		# CHD's invoice debits the clearing account, which the customer's contract already credits.
		"item_defaults": [{"company": s.COMPANY, "expense_account": _acc("ACC_CLEARING")}],
	})
	item.insert(ignore_permissions=True)


def _product_values():
	"""Read-only desired values; selecting the first bank is never acceptable."""
	order = "Flexi HP Demand Offset Order"
	bank = _by_number("1201")
	if not bank or frappe.db.get_value("Account", bank, "account_type") != "Bank":
		frappe.throw("Account 1201 must be the enabled BSP Flexi Bank leaf account")
	recovery = _by_number("4006")
	return {
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
		"days_past_due_threshold_for_npa": s.NATIVE_NPA_THRESHOLD,
		"emi_rounding_method": "No Rounding",
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
	}


def _loan_product():
	values = _product_values()
	if frappe.db.exists("Loan Product", PRODUCT_CODE):
		doc = frappe.get_doc("Loan Product", PRODUCT_CODE)
		if any(doc.get(k) != value for k, value in values.items() if k != "doctype"):
			doc.update({k: v for k, v in values.items() if k != "doctype"})
			doc.save(ignore_permissions=True)
	else:
		frappe.get_doc(values).insert(ignore_permissions=True)


def field_diffs():
	"""Exact Company/Product differences plus current supplier/item mappings."""
	diffs = []
	title = "Flexi HP Demand Offset Order"
	order = frappe.get_doc("Loan Demand Offset Order", title) if frappe.db.exists("Loan Demand Offset Order", title) else None
	before_order = [row.demand_type for row in order.get("components") or []] if order else []
	if before_order != ["Penalty", "Interest", "Principal"]:
		diffs.append({"doctype": "Loan Demand Offset Order", "name": title, "field": "components", "before": before_order, "after": ["Penalty", "Interest", "Principal"]})
	company = frappe.get_doc("Company", s.COMPANY)
	wanted = {"enable_loan_accounting": 1, "days_past_due_threshold": 90}
	for field in OFFSET_FIELDS:
		if company.meta.has_field(field):
			wanted[field] = "Flexi HP Demand Offset Order"
	for field, value in wanted.items():
		if company.get(field) != value:
			diffs.append({"doctype": "Company", "name": s.COMPANY, "field": field, "before": company.get(field), "after": value})
	product = frappe.get_doc("Loan Product", PRODUCT_CODE) if frappe.db.exists("Loan Product", PRODUCT_CODE) else None
	for field, value in _product_values().items():
		if field != "doctype" and (not product or product.get(field) != value):
			diffs.append({"doctype": "Loan Product", "name": PRODUCT_CODE, "field": field, "before": product.get(field) if product else None, "after": value})
	for dt, name, table, field, target in (
		("Supplier", s.CHD_SUPPLIER, "accounts", "account", _acc("ACC_CHD_PAYABLE")),
		("Item", s.HP_ITEM_CODE, "item_defaults", "expense_account", _acc("ACC_CLEARING")),
	):
		doc = frappe.get_doc(dt, name) if frappe.db.exists(dt, name) else None
		rows = [row for row in (doc.get(table) or []) if row.company == s.COMPANY] if doc else []
		before = rows[0].get(field) if len(rows) == 1 else None
		if before != target or len(rows) > 1:
			diffs.append({"doctype": dt, "name": name, "field": table + "." + field, "before": before, "after": target, "matching_rows": len(rows)})
		for f, value in ({"supplier_group": "Distributor", "supplier_type": "Company"} if dt == "Supplier" else {"item_group": "Products", "stock_uom": "Unit"}).items():
			if doc and doc.get(f) != value:
				diffs.append({"doctype": dt, "name": name, "field": f, "before": doc.get(f), "after": value})
		if doc and dt == "Item":
			for f, value in (("is_stock_item", 0), ("is_sales_item", 0), ("is_purchase_item", 1)):
				if doc.get(f) != value:
					diffs.append({"doctype": dt, "name": name, "field": f, "before": doc.get(f), "after": value})
		if doc and dt == "Supplier" and doc.get("default_currency") != "FJD":
			diffs.append({"doctype": dt, "name": name, "field": "default_currency", "before": doc.get("default_currency"), "after": "FJD"})
	return diffs


def _update_existing_mapping(doctype, name, table, field, target, values):
	doc = frappe.get_doc(doctype, name)
	rows = [row for row in doc.get(table) or [] if row.company == s.COMPANY]
	if len(rows) > 1:
		frappe.throw("Multiple company mappings on " + doctype + " " + name + "; review before setup")
	changed = False
	if not rows:
		doc.append(table, {"company": s.COMPANY, field: target})
		changed = True
	elif rows[0].get(field) != target:
		rows[0].set(field, target)
		changed = True
	for key, value in values.items():
		if doc.get(key) != value:
			doc.set(key, value)
			changed = True
	if changed:
		doc.save(ignore_permissions=True)


@frappe.whitelist(methods=["POST"])
def setup_masters(dry_run=1):
	"""Create the HP master data.  ``dry_run`` (default) only lists what it would do."""
	frappe.only_for("System Manager")
	dry_run = int(dry_run)
	if not frappe.db.exists("Company", s.COMPANY):
		frappe.throw(f"Company {s.COMPANY} does not exist yet.")
	plan = _plan()
	diffs = field_diffs()
	if dry_run:
		return {"dry_run": 1, "would_do": plan, "field_diffs": diffs}
	_roles()
	_modes()
	_company()
	_supplier()
	_item()
	_loan_product()
	check = s.check_setup()
	if not check["ok"]:
		frappe.throw("Setup validation failed; changes will roll back: " + "; ".join(check["problems"]))
	frappe.db.commit()
	return {"dry_run": 0, "done": plan, "field_diffs": diffs, "check": check}
