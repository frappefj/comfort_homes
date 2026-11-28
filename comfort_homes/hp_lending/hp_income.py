"""HP income and charges by loan (7.1.6): Portfolio Control cards and the HP Income & Charges report.

Flat HP is booked gross, so a receipt is not split into principal and interest: interest is EARNED as the
deferred term charges (2013) are released to 4001. Default charges are held in 2015 and become income (4004)
when the loan closes. Every figure is read from the ledger and tied to its loan through the voucher
(Journal Entry custom_hp_loan, Loan Balance Adjustment loan); ledger lines with no loan are shown on one
"Not linked to a loan" row, so the report totals equal the account totals.
"""

import frappe
from frappe.utils import flt, get_first_day, getdate, today

from comfort_homes.hp_lending import hp_settings as s

ROLES = ("Director", "Finance Manager", "Accounts Manager", "Loan Manager", "System Manager")
UNLINKED = "Not linked to a loan"
KEYS = {"ACC_INTEREST_INCOME": "interest", "ACC_DEF_INTEREST": "unearned", "ACC_ADMIN_FEE": "admin_fee",
	"ACC_DOC_FEE": "doc_fee", "ACC_DEF_PENALTY": "charges", "ACC_PENALTY_INCOME": "charges_income"}


def require_role():
	if not set(ROLES) & set(frappe.get_roles()):
		frappe.throw("Only Finance, Accounts and Loan Managers can see HP income.", frappe.PermissionError)


def combine(gl_rows, fee_rows, accounts, from_date, to_date):
	"""Pure: per-loan figures from ledger rows and fee invoices.

	``gl_rows`` = [{loan, account, posting_date, debit, credit}], ``accounts`` = {account name: key},
	``fee_rows`` = [{loan, posting_date, billed, outstanding}]. Income and charges raised are for the period;
	unearned interest and charges held are balances at ``to_date``."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	out = {}

	def row(loan):
		return out.setdefault(loan or UNLINKED, {"loan": loan or UNLINKED, "interest_earned": 0.0, "interest_to_date": 0.0,
			"unearned_interest": 0.0, "admin_fee": 0.0, "doc_fee": 0.0, "charges_raised": 0.0, "charges_cleared": 0.0,
			"charges_held": 0.0, "charges_income": 0.0, "fees_billed": 0.0, "fees_paid": 0.0})

	for g in gl_rows:
		key = accounts.get(g["account"])
		when = getdate(g["posting_date"])
		if not key or when > to_date:
			continue
		r, net_credit = row(g.get("loan")), flt(g.get("credit")) - flt(g.get("debit"))
		in_period = when >= from_date
		if key == "interest":
			r["interest_to_date"] += net_credit
			if in_period:
				r["interest_earned"] += net_credit
		elif key == "unearned":
			r["unearned_interest"] += net_credit
		elif key == "charges":
			r["charges_held"] += net_credit
			if in_period:
				r["charges_raised"] += flt(g.get("credit"))
				r["charges_cleared"] += flt(g.get("debit"))
		elif in_period:
			r[{"admin_fee": "admin_fee", "doc_fee": "doc_fee", "charges_income": "charges_income"}[key]] += net_credit
	for f in fee_rows:
		when = getdate(f["posting_date"])
		if from_date <= when <= to_date:
			r = row(f.get("loan"))
			r["fees_billed"] += flt(f.get("billed"))
			r["fees_paid"] += flt(f.get("billed")) - flt(f.get("outstanding"))
	for r in out.values():
		for k, v in r.items():
			if k != "loan":
				r[k] = round(v, 2)
	return sorted(out.values(), key=lambda r: (r["loan"] == UNLINKED, r["loan"]))


def accounts():
	return {s.account(key): value for key, value in KEYS.items()}


def gl_rows(to_date):
	names = list(accounts())
	return frappe.db.sql(
		"""SELECT COALESCE(je.custom_hp_loan, lba.loan) AS loan, gle.account, gle.posting_date, gle.debit, gle.credit
		   FROM `tabGL Entry` gle
		   LEFT JOIN `tabJournal Entry` je ON gle.voucher_type = 'Journal Entry' AND je.name = gle.voucher_no
		   LEFT JOIN `tabLoan Balance Adjustment` lba ON gle.voucher_type = 'Loan Balance Adjustment' AND lba.name = gle.voucher_no
		   WHERE gle.is_cancelled = 0 AND gle.company = %s AND gle.account IN %s AND gle.posting_date <= %s""",
		(s.COMPANY, tuple(names), to_date), as_dict=True)


def fee_rows(from_date, to_date):
	if not frappe.db.has_column("Sales Invoice", "custom_hp_independent_fee"):
		return []
	return frappe.db.sql(
		"""SELECT custom_hp_reference_loan AS loan, posting_date, grand_total AS billed, outstanding_amount AS outstanding
		   FROM `tabSales Invoice` WHERE docstatus = 1 AND company = %s AND custom_hp_independent_fee = 1
		   AND posting_date BETWEEN %s AND %s""", (s.COMPANY, from_date, to_date), as_dict=True)


def figures(from_date=None, to_date=None):
	to_date = getdate(to_date or today())
	from_date = getdate(from_date or get_first_day(to_date))
	return combine(gl_rows(to_date), fee_rows(from_date, to_date), accounts(), from_date, to_date)


def totals(rows):
	keys = [k for k in (rows[0] if rows else {}) if k != "loan"]
	return {k: round(sum(r[k] for r in rows), 2) for k in keys}


def _card(key, label_route=True):
	require_role()
	value = totals(figures()).get(key, 0)
	return {"value": flt(value, 2), "fieldtype": "Currency", "route": ["query-report", "HP Income and Charges"]}


@frappe.whitelist()
def card_interest_earned():
	"""Interest earned this month (4001)."""
	return _card("interest_earned")


@frappe.whitelist()
def card_unearned_interest():
	"""Interest not yet earned (2013 balance)."""
	return _card("unearned_interest")


@frappe.whitelist()
def card_charges_held():
	"""Default charges held until the loans close (2015 balance)."""
	return _card("charges_held")


@frappe.whitelist()
def card_fees_billed():
	"""Servicing fees billed this month."""
	return _card("fees_billed")
