"""MYOB conversion (dev14): existing HP contracts moved onto Lending's native import route.

How a converted contract is held
--------------------------------
* Loan Application: the ORIGINAL contract (sale date, term, term charges, total
  receivable), marked ``custom_hp_converted``.  It is written as a record of a
  contract signed in MYOB, so origination hooks and workflow gates never run on it.
* Loan: ``is_imported`` with the original schedule, ``migration_date`` = MYOB
  cut-over and, in Loan Import Details, the principal OVERDUE at cut-over
  (Lending's meaning of "opening principal").  Lending posts the opening
  receivable itself: Dr 1101 / Cr the product's payment account, which on the
  HP-CONV product is 1910 Temporary Opening.  Imported disbursements, demands and
  repayments post no GL, so the gross-booking reclass (hp_lifecycle) never runs:
  the Loan is linked to its application only after Lending has finished.
* On Loan submit (``on_loan_submit``) the rest of the opening position is posted
  once, each step idempotent:
    unearned interest     Dr 1910 / Cr 2013  (Journal Entry "Opening Balance")
    unpaid default int.   Loan Balance Adjustment Debit -> 2015 (HP-PEN-<loan>-OPEN)
    other unpaid fees     Dr 1120 (customer) / Cr 1910
    paid ahead            Advance Payment repayment received into 1910
    overdue since         the imported arrears demand is dated at the oldest unpaid
                          instalment, so days past due and NPA continue from MYOB.
* Interest release and late charges for converted loans run from this module, so
  the pinned jobs in hp_interest / hp_penalties stay untouched (they skip
  imported loans).  Late charges start only when ``custom_hp_charges_active`` is
  ticked on the loan, after its MYOB repayments have been caught up.

Nothing in hp_interest.py, hp_penalties.py, hp_lifecycle.py or hp_settlement.py
is changed; this module only reuses their public helpers.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import add_days, flt, get_first_day, getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

PRODUCT = "HP-CONV"
BASE_PRODUCT = "HP-STD"
OPENING = "Opening Balance"
OPENING_ACCOUNT_NUMBER = "1910"
FEES_ACCOUNT_NUMBER = "1120"
APPLICATION_STATE = "Active Loan Account"
# Account added for the conversion (MYOB 11129). Created by setup_conversion when missing; the approved chart
# in setup_data stays as approved.
CONVERSION_ACCOUNTS = (
	dict(account_number="2016", account_name="Westpac Bank - OD - Flexi - 9808559661", parent_number="2100-2400",
		account_type="Bank"),
)

LOAN_FIELDS = (
	"loan_product", "posting_date", "loan_amount", "rate_of_interest", "custom_hp_contract_rate", "is_term_loan",
	"is_imported", "migration_date", "repayment_method", "repayment_frequency", "repayment_periods",
	"repayment_start_date", "cost_center", "custom_hp_converted", "custom_hp_myob_invoice",
	"custom_hp_opening_outstanding", "custom_hp_opening_unearned", "custom_hp_opening_penalty",
	"custom_hp_opening_fees", "custom_hp_opening_advance", "custom_hp_oldest_unpaid_date",
	"custom_hp_dpd_at_migration", "review_reason",
)
APPLICATION_FIELDS = (
	"posting_date", "loan_amount", "custom_branchlocation", "custom_flexi_hp_account_number",
	"custom_flexi_quotation_number", "custom_flexi_cash_price", "custom_flexi_surcharge_amount",
	"custom_flexi_deposit_amount", "custom_flexi_admin_fee", "custom_flexi_doc_fee_net", "custom_flexi_doc_fee_vat",
	"custom_flexi_net_amount_financed", "custom_flexi_term_charges", "custom_flexi_total_receivable",
	"custom_flexi_term_months", "custom_flexi_interest_rate", "custom_flexi_payment_period",
	"custom_flexi_first_instalment_date", "custom_hp_myob_invoice", "custom_hp_opening_outstanding",
	"custom_hp_opening_unearned", "custom_hp_myob_payment_method",
)


def is_converted(doc):
	return bool(doc and doc.get("custom_hp_converted"))


def _account(number):
	name = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": number, "is_group": 0, "disabled": 0}, "name")
	if not name:
		frappe.throw(_("Account number {0} is missing from the Chart of Accounts.").format(number))
	return name


def _tagged_entry_exists(loan, tag):
	return bool(frappe.db.exists("Journal Entry", {"custom_hp_loan": loan, "docstatus": 1, "user_remark": ["like", f"%[{tag}]%"]}))


def _journal(loan, posting_date, tag, remark, rows):
	je = frappe.get_doc({
		"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": s.COMPANY,
		"posting_date": posting_date, "custom_hp_loan": loan, "custom_hp_entry_type": OPENING,
		"user_remark": f"{remark} [{tag}]", "accounts": rows,
	})
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


# ---------------------------------------------------------------------------
# Loan submit: the rest of the opening position
# ---------------------------------------------------------------------------

def on_loan_submit(doc, method=None):
	"""Loan on_submit (runs after Lending's own import processing)."""
	if not is_converted(doc) or doc.company != s.COMPANY:
		return
	if not doc.get("is_imported") or not doc.get("migration_date"):
		frappe.throw(_("A converted HP loan must be an imported loan with a migration date."))
	previous = getattr(frappe.flags, "hp_conversion", None)
	frappe.flags.hp_conversion = True
	try:
		_link_application(doc)
		_post_opening_unearned(doc)
		_post_opening_penalty(doc)
		_post_opening_fees(doc)
		_post_opening_advance(doc)
		_date_opening_arrears(doc)
	finally:
		frappe.flags.hp_conversion = previous


def _link_application(doc):
	app = doc.get("custom_hp_application")
	if not app or doc.get("loan_application") == app:
		return
	row = frappe.db.get_value("Loan Application", app, ["company", "applicant", "custom_hp_converted"], as_dict=True)
	if not row or row.company != doc.company or row.applicant != doc.applicant or not row.custom_hp_converted:
		frappe.throw(_("Converted loan {0}: application {1} is not the matching converted application.").format(doc.name, app))
	if frappe.db.exists("Loan", {"loan_application": app, "name": ["!=", doc.name], "docstatus": 1}):
		frappe.throw(_("Application {0} is already linked to another submitted loan.").format(app))
	doc.db_set("loan_application", app, update_modified=False)


def _post_opening_unearned(doc):
	amount = m.money(doc.get("custom_hp_opening_unearned"))
	tag = f"HP-OPEN-INT-{doc.name}"
	if amount <= 0 or _tagged_entry_exists(doc.name, tag):
		return
	_journal(doc.name, doc.migration_date, tag, f"Unearned HP interest brought forward from MYOB for {doc.name}", [
		{"account": _account(OPENING_ACCOUNT_NUMBER), "debit_in_account_currency": float(amount), "cost_center": doc.cost_center},
		{"account": s.account("ACC_DEF_INTEREST"), "credit_in_account_currency": float(amount)},
	])


def _post_opening_penalty(doc):
	amount = m.money(doc.get("custom_hp_opening_penalty"))
	key = f"HP-PEN-{doc.name}-OPEN"
	if amount <= 0 or frappe.db.exists("Loan Balance Adjustment", {"reference_number": key, "docstatus": ["!=", 2]}):
		return
	adj = frappe.get_doc({
		"doctype": "Loan Balance Adjustment", "loan": doc.name, "applicant_type": "Customer", "applicant": doc.applicant,
		"company": doc.company, "posting_date": doc.migration_date, "cost_center": doc.cost_center,
		"adjustment_type": "Debit Adjustment", "amount": float(amount), "adjustment_account": s.account("ACC_DEF_PENALTY"),
		"reference_number": key, "remarks": "Unpaid MYOB default interest brought forward",
	})
	adj.insert(ignore_permissions=True)
	adj.submit()


def _post_opening_fees(doc):
	amount = m.money(doc.get("custom_hp_opening_fees"))
	tag = f"HP-OPEN-FEE-{doc.name}"
	if amount <= 0 or _tagged_entry_exists(doc.name, tag):
		return
	_journal(doc.name, doc.migration_date, tag, f"Unpaid MYOB fees (SMS, demand notice, bailiff, SCT) for {doc.name}", [
		{"account": _account(FEES_ACCOUNT_NUMBER), "debit_in_account_currency": float(amount),
			"party_type": "Customer", "party": doc.applicant, "cost_center": doc.cost_center},
		{"account": _account(OPENING_ACCOUNT_NUMBER), "credit_in_account_currency": float(amount), "cost_center": doc.cost_center},
	])


def _post_opening_advance(doc):
	amount = m.money(doc.get("custom_hp_opening_advance"))
	key = f"HP-OPEN-ADV-{doc.name}"
	if amount <= 0 or frappe.db.exists("Loan Repayment", {"against_loan": doc.name, "reference_number": key, "docstatus": 1}):
		return
	repayment = frappe.get_doc({
		"doctype": "Loan Repayment", "against_loan": doc.name, "applicant_type": "Customer", "applicant": doc.applicant,
		"company": doc.company, "loan_product": doc.loan_product, "repayment_type": "Advance Payment",
		"value_date": f"{doc.migration_date} 23:59:59", "amount_paid": float(amount),
		"payment_account": _account(OPENING_ACCOUNT_NUMBER), "reference_number": key, "reference_date": doc.migration_date,
	})
	repayment.insert(ignore_permissions=True)
	repayment.submit()


def _date_opening_arrears(doc):
	oldest = doc.get("custom_hp_oldest_unpaid_date")
	if not oldest:
		return
	frappe.db.set_value(
		"Loan Demand",
		{"loan": doc.name, "is_imported": 1, "docstatus": 1, "demand_type": "EMI", "demand_subtype": "Principal"},
		"demand_date", getdate(oldest), update_modified=False,
	)
	from lending.loan_management.doctype.loan.loan import update_days_past_due_in_loans
	update_days_past_due_in_loans(loan_name=doc.name, posting_date=getdate(), loan_product=doc.loan_product,
		force_update_dpd_in_loan=True)


def guard_repayment_account(doc, method=None):
	"""Loan Repayment validate: money received on a converted loan must go to a bank or cash account.

	HP-CONV's payment account is 1910 so that Lending's opening entry lands there; a
	repayment left on that default would credit the loan without any bank."""
	if getattr(frappe.flags, "hp_conversion", None) or doc.get("is_imported"):
		return
	if not frappe.db.get_value("Loan", doc.against_loan, "custom_hp_converted"):
		return
	if doc.get("payment_account") == _account(OPENING_ACCOUNT_NUMBER):
		frappe.throw(_("Choose the bank or cash account this repayment was received into."), title=_("Converted HP loan"))


# ---------------------------------------------------------------------------
# Scheduled: interest release and late charges for converted loans
# ---------------------------------------------------------------------------

def _converted_loans(fields, extra=None):
	filters = {"company": s.COMPANY, "status": ["in", ["Disbursed", "Partially Disbursed"]], "docstatus": 1,
		"is_imported": 1, "custom_hp_converted": 1, "applicant_type": "Customer", "loan_application": ["!=", ""]}
	filters.update(extra or {})
	return frappe.get_all("Loan", filters=filters, fields=fields, limit_page_length=0)


def interest_due(term_charges, opening_unearned, term_months, months_elapsed, released_in_frappe):
	"""Same straight-line rule as hp_interest, counting what MYOB had already earned as released."""
	earned_in_myob = max(m.money(term_charges) - m.money(opening_unearned), m.d(0))
	return m.interest_release_due(term_charges, term_months, months_elapsed, earned_in_myob + m.money(released_in_frappe))


def release_converted_interest(as_of=None):
	"""Scheduled on the 1st with hp_interest: release for the month that has just ended."""
	from comfort_homes.hp_lending.hp_interest import TYPE, released_to_date
	month_end = add_days(get_first_day(as_of or today()), -1) if not as_of else getdate(as_of)
	released = []
	for loan in _converted_loans(["name", "loan_application", "cost_center", "posting_date", "custom_hp_opening_unearned"]):
		frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan.name,))
		if (frappe.db.get_value("Loan", loan.name, "days_past_due") or 0) > 0:
			continue  # overdue: interest stays deferred until the loan is current (agreed rule)
		if frappe.db.exists("Loan Balance Adjustment", {"loan": loan.name, "reference_number": "HP-REBATE-" + loan.name, "docstatus": 1}):
			continue
		tag = f"HP-INT-{loan.name}-{month_end}"
		if s.entry_exists(TYPE, loan.name, tag):
			continue
		app = frappe.db.get_value("Loan Application", loan.loan_application,
			["custom_flexi_term_charges", "custom_flexi_term_months"], as_dict=True)
		if not app or not app.custom_flexi_term_charges:
			continue
		disbursed_on = frappe.db.get_value("Loan Disbursement", {"against_loan": loan.name, "docstatus": 1},
			"disbursement_date", order_by="disbursement_date asc") or loan.posting_date
		due = interest_due(app.custom_flexi_term_charges, loan.custom_hp_opening_unearned, app.custom_flexi_term_months or 12,
			m.months_elapsed(getdate(disbursed_on), getdate(month_end)), released_to_date(loan.name))
		if due <= 0:
			continue
		je = frappe.get_doc({
			"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": s.COMPANY, "posting_date": month_end,
			"custom_hp_loan": loan.name, "custom_hp_entry_type": TYPE,
			"user_remark": f"Deferred HP interest earned, {loan.name}, to {month_end} [{tag}]",
			"accounts": [
				{"account": s.ACC_DEF_INTEREST, "debit_in_account_currency": float(due)},
				{"account": s.ACC_INTEREST_INCOME, "credit_in_account_currency": float(due), "cost_center": loan.cost_center},
			],
		})
		je.insert(ignore_permissions=True)
		je.submit()
		released.append(loan.name)
	return released


def assess_converted_penalties(on_date=None):
	"""Scheduled daily with hp_penalties; only loans with late charges switched on."""
	from comfort_homes.hp_lending.hp_penalties import KEY, _is_repayment_date, _product_settings, overdue_base
	on_date = getdate(on_date or today())
	charged = []
	for loan in _converted_loans(["name", "applicant", "loan_product", "repayment_frequency", "cost_center"],
			{"custom_hp_charges_active": 1}):
		frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan.name,))
		dpd = frappe.db.get_value("Loan", loan.name, "days_past_due") or 0
		rate, stop_dpd, grace = _product_settings(loan.loan_product)
		if not rate or dpd >= stop_dpd or not _is_repayment_date(loan.name, on_date):
			continue
		key = f"{KEY}-{loan.name}-{on_date}"
		if frappe.db.exists("Loan Balance Adjustment", {"reference_number": key, "docstatus": ["!=", 2]}):
			continue
		base = overdue_base(loan.name, on_date, grace)
		amount = m.penalty_amount(base, rate, loan.repayment_frequency)
		if amount <= 0:
			continue
		adj = frappe.get_doc({
			"doctype": "Loan Balance Adjustment", "loan": loan.name, "applicant_type": "Customer", "applicant": loan.applicant,
			"company": s.COMPANY, "posting_date": on_date, "cost_center": loan.cost_center,
			"adjustment_type": "Debit Adjustment", "amount": float(amount), "adjustment_account": s.ACC_DEF_PENALTY,
			"reference_number": key,
			"remarks": f"Late payment charge: {rate}% a month on overdue {base:.2f} ({loan.repayment_frequency})",
		})
		adj.insert(ignore_permissions=True)
		adj.submit()
		charged.append(loan.name)
	return charged


# ---------------------------------------------------------------------------
# Setup and loader (System Manager, POST)
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def setup_conversion(dry_run=1):
	"""Create the HP-CONV product (a copy of HP-STD at 0% with 1910 as payment/disbursement account). Idempotent."""
	frappe.only_for("System Manager")
	plan = []
	opening = _account(OPENING_ACCOUNT_NUMBER)
	for acc in CONVERSION_ACCOUNTS:
		if frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": acc["account_number"]}, "name"):
			continue
		plan.append(f"Create account {acc['account_number']} {acc['account_name']}")
		if not int(dry_run):
			parent = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": acc["parent_number"], "is_group": 1}, "name")
			frappe.get_doc({"doctype": "Account", "company": s.COMPANY, "account_name": acc["account_name"],
				"account_number": acc["account_number"], "parent_account": parent, "account_type": acc["account_type"],
				"account_currency": "FJD", "is_group": 0}).insert()
	if not frappe.db.exists("Loan Product", PRODUCT):
		plan.append(f"Create Loan Product {PRODUCT} from {BASE_PRODUCT}")
		if not int(dry_run):
			product = frappe.copy_doc(frappe.get_doc("Loan Product", BASE_PRODUCT))
			product.product_code = PRODUCT
			product.product_name = "Hire Purchase - Converted from MYOB"
			product.rate_of_interest = 0
			product.payment_account = opening
			product.disbursement_account = opening
			product.insert()
	else:
		row = frappe.db.get_value("Loan Product", PRODUCT, ["rate_of_interest", "payment_account", "penalty_interest_rate"], as_dict=True)
		if row.rate_of_interest or row.payment_account != opening or row.penalty_interest_rate:
			plan.append(f"Review {PRODUCT}: rate must be 0, payment account {opening}, native penalty 0")
	return {"dry_run": int(dry_run), "plan": plan}


def _find_customer(row):
	ref = row.get("custom_ch_account_reference_no")
	name = frappe.db.get_value("Customer", {"custom_ch_account_reference_no": ref}, "name") if ref else None
	tin = row.get("custom_tin_number")
	if not name and tin and not str(tin).startswith("MYOB-"):
		name = frappe.db.get_value("Customer", {"custom_tin_number": tin}, "name")
	return name


def _customer(row):
	name = _find_customer(row)
	if name:
		return name, False
	doc = frappe.get_doc({
		"doctype": "Customer", "customer_name": row["customer_name"], "customer_type": row.get("customer_type") or "Individual",
		"customer_group": row.get("customer_group") or "Individual", "territory": row.get("territory") or "Fiji",
		"default_currency": "FJD", "custom_tin_number": row["custom_tin_number"],
		"custom_ch_account_reference_no": row["custom_ch_account_reference_no"], "mobile_no": row.get("mobile_no") or None,
	})
	doc.insert(ignore_permissions=True)
	return doc.name, True


def _application(row, customer, items):
	"""The original contract, written as a record: no origination hooks or workflow gates run."""
	existing = frappe.db.get_value("Loan Application", {"custom_hp_myob_invoice": row["custom_hp_myob_invoice"], "custom_hp_converted": 1}, "name")
	if existing:
		return existing, False
	values = {f: row.get(f) for f in APPLICATION_FIELDS if row.get(f) not in (None, "")}
	doc = frappe.get_doc(dict(values, doctype="Loan Application", company=s.COMPANY, applicant_type="Customer",
		applicant=customer, loan_product=PRODUCT, rate_of_interest=0, is_term_loan=1, custom_hp_converted=1,
		status="Approved", workflow_state=APPLICATION_STATE, docstatus=1,
		custom_flexi_purchase_items=[{k: i.get(k) for k in ("product_description", "quantity", "unit_price", "total_amount")} for i in items]))
	doc.set_new_name()
	for step in ("_set_defaults", "set_user_and_timestamp"):  # what insert() does before writing, minus the hooks
		if hasattr(doc, step):
			getattr(doc, step)()
	doc.set_parent_in_children()
	doc.db_insert()
	for child in doc.get_all_children():
		child.docstatus = 1
		child.db_insert()
	return doc.name, True


def _loan(row, customer, application, items):
	existing = frappe.db.get_value("Loan", {"custom_hp_myob_invoice": row["custom_hp_myob_invoice"], "custom_hp_converted": 1, "docstatus": ["!=", 2]}, ["name", "docstatus"], as_dict=True)
	if existing:
		return existing.name, existing.docstatus, False
	values = {f: row.get(f) for f in LOAN_FIELDS if row.get(f) not in (None, "")}
	values.pop("review_reason", None)
	doc = frappe.get_doc(dict(values, doctype="Loan", company=s.COMPANY, applicant_type="Customer", applicant=customer,
		loan_product=PRODUCT, rate_of_interest=0, is_imported=1, custom_hp_converted=1, custom_hp_application=application,
		custom_hp_review_reason=row.get("review_reason") or None,
		loan_import_details=[{
			"disbursement_date": row["imp_disbursement_date"], "disbursed_amount": row["imp_disbursed_amount"],
			"repayment_method": row.get("imp_repayment_method"), "repayment_frequency": row.get("imp_repayment_frequency"),
			"repayment_periods": row.get("imp_repayment_periods"), "repayment_start_date": row.get("imp_repayment_start_date"),
			"opening_principal_outstanding": flt(row.get("imp_opening_principal_outstanding")),
			"opening_interest_outstanding": 0, "opening_penalty_outstanding": 0, "opening_charge_outstanding": 0,
		}],
		custom_flexi_purchase_items=[{k: i.get(k) for k in ("product_description", "quantity", "unit_price", "total_amount")} for i in items]))
	doc.insert(ignore_permissions=True)
	if row.get("load_as") == "Submit":
		doc.submit()
	return doc.name, doc.docstatus, True


def _iso(value):
	"""Extraction files use dd-mm-yyyy; the database wants yyyy-mm-dd."""
	if isinstance(value, dict):
		return {k: _iso(v) for k, v in value.items()}
	if isinstance(value, list):
		return [_iso(v) for v in value]
	if isinstance(value, str) and re.fullmatch(r"\d{2}-\d{2}-\d{4}", value):
		return f"{value[6:]}-{value[3:5]}-{value[:2]}"
	return value


@frappe.whitelist(methods=["POST"])
def load_batch(payload, dry_run=1):
	"""Load converted contracts.  ``payload`` = {"loans": [rows of file 03 with "customer" (file 01 row),
	"application" (file 02 row) and "items" (file 02b rows)]}.  Each contract is its own savepoint; one failure
	does not stop the batch.  dry_run=1 does everything and then rolls the whole batch back."""
	frappe.only_for("System Manager")
	if PRODUCT not in (frappe.get_all("Loan Product", pluck="name") or []):
		frappe.throw(_("Run setup_conversion first: Loan Product {0} is missing.").format(PRODUCT))
	data = _iso(json.loads(payload) if isinstance(payload, str) else payload)
	results = []
	for row in data.get("loans") or []:
		ref = row.get("custom_hp_myob_invoice")
		frappe.db.savepoint("hp_conversion_row")
		try:
			customer, c_new = _customer(row["customer"])
			application, a_new = _application(row["application"], customer, row.get("items") or [])
			loan, docstatus, l_new = _loan(row, customer, application, row.get("items") or [])
			results.append({"invoice": ref, "ok": True, "customer": customer, "application": application, "loan": loan,
				"docstatus": docstatus, "created": {"customer": c_new, "application": a_new, "loan": l_new}})
		except Exception as e:
			frappe.db.rollback(save_point="hp_conversion_row")
			frappe.clear_messages()
			results.append({"invoice": ref, "ok": False, "error": str(e)[:500]})
	if int(dry_run):
		frappe.db.rollback()
	return {"dry_run": int(dry_run), "ok": sum(1 for r in results if r["ok"]), "failed": sum(1 for r in results if not r["ok"]),
		"results": results}
