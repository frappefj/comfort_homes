"""7.1.3 collections: store cash accounts, the M-PAiSA wallet, in-store receipt defaults and Daily Collections.

Money in is read from the ledger: every debit to a Cash or Bank account, classified by the document that
posted it.  So the report ties to the cash and bank books, and a receipt counts once whichever screen
recorded it.  Transfers between cash and bank accounts (an M-PAiSA settlement to 1008, a till banking),
opening balances and customer credit applied to an instalment are not money in and are left out.
"""

import functools
import json
import os

import frappe
from frappe import _
from frappe.utils import flt, getdate, today

from comfort_homes.hp_lending import hp_settings as s

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "setup_data", "collection_locations.json")
ROLES = ("Director", "Finance Manager", "Accounts Manager", "Accounts Officer", "Loan Manager", "System Manager")
COLUMNS = ("deposits", "repayments", "fees", "credit", "unidentified", "other")
LABELS = {"deposits": "Deposits", "repayments": "Repayments", "fees": "Fees", "credit": "Held as credit",
	"unidentified": "Unidentified", "other": "Other receipts"}


@functools.lru_cache(maxsize=1)
def config():
	with open(CONFIG_PATH, encoding="utf-8") as handle:
		return json.load(handle)


def cutover_date():
	return getdate(config()["cutover_date"])


def _abbr():
	return frappe.get_cached_value("Company", s.COMPANY, "abbr")


def account_by_number(number, is_group=0):
	return frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": number, "is_group": is_group}, "name")


def store_for_cost_center(cost_center):
	"""The configured store whose cost centre is ``cost_center`` ("Grantham - Flexi" -> Grantham)."""
	if not cost_center:
		return None
	base = str(cost_center).rsplit(" - ", 1)[0].strip()
	return next((store for store in config()["stores"] if store.get("cost_center") and store["cost_center"] == base), None)


def store_cash_account(cost_center):
	store = store_for_cost_center(cost_center)
	return account_by_number(store["number"]) if store else None


def wallet_account():
	return account_by_number(config()["mpaisa"]["wallet_number"])


# ---------------------------------------------------------------------------
# Setup (System Manager, dry run by default)
# ---------------------------------------------------------------------------

def _wanted_accounts():
	cfg = config()
	rows = [dict(number=store["number"], name=store["account_name"], parent=cfg["cash_parent_number"], account_type="Cash")
		for store in cfg["stores"] if not store.get("existing")]
	mp = cfg["mpaisa"]
	rows.append(dict(number=mp["wallet_number"], name=mp["wallet_name"], parent=mp["wallet_parent_number"], account_type="Bank"))
	rows.append(dict(number=mp["charges_number"], name=mp["charges_name"], parent=mp["charges_parent_number"], account_type="Expense Account"))
	return rows


def collections_plan():
	"""What setup_collections would create.  Reads only."""
	plan, problems = [], []
	for row in _wanted_accounts():
		existing = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": row["number"]}, ["name", "account_name", "is_group"], as_dict=True)
		if existing:
			if existing.is_group or existing.account_name != row["name"]:
				problems.append(f"Account number {row['number']} is already used by {existing.name}; change it in collection_locations.json")
			continue
		if not account_by_number(row["parent"], is_group=1):
			problems.append(f"Parent group {row['parent']} for {row['name']} does not exist")
		plan.append(f"Create account {row['number']} - {row['name']} ({row['account_type']})")
	for store in config()["stores"]:
		if store.get("cost_center") and not frappe.db.exists("Cost Center", f"{store['cost_center']} - {_abbr()}"):
			problems.append(f"Cost centre {store['cost_center']} - {_abbr()} does not exist for store {store['store']}")
	mp = config()["mpaisa"]
	if not frappe.db.exists("Bank", mp["bank"]):
		plan.append(f"Create Bank {mp['bank']}")
	if not frappe.db.exists("Bank Account", f"{mp['bank_account_name']} - {mp['bank']}"):
		plan.append(f"Create company Bank Account {mp['bank_account_name']} - {mp['bank']} on {mp['wallet_number']}")
	if not account_by_number(mp["settlement_account_number"]):
		problems.append(f"M-PAiSA settlement account {mp['settlement_account_number']} does not exist")
	return plan, problems


@frappe.whitelist(methods=["POST"])
def setup_collections(dry_run=1):
	"""Create the store cash accounts, the M-PAiSA wallet and charges accounts, and the wallet's Bank Account."""
	frappe.only_for("System Manager")
	plan, problems = collections_plan()
	if int(dry_run) or problems:
		return {"dry_run": int(dry_run), "would_do": plan, "problems": problems}
	for row in _wanted_accounts():
		if frappe.db.exists("Account", {"company": s.COMPANY, "account_number": row["number"]}):
			continue
		frappe.get_doc({"doctype": "Account", "company": s.COMPANY, "account_name": row["name"], "account_number": row["number"],
			"parent_account": account_by_number(row["parent"], is_group=1), "is_group": 0,
			"account_type": row["account_type"], "account_currency": "FJD"}).insert(ignore_permissions=True)
	mp = config()["mpaisa"]
	if not frappe.db.exists("Bank", mp["bank"]):
		frappe.get_doc({"doctype": "Bank", "bank_name": mp["bank"]}).insert(ignore_permissions=True)
	if not frappe.db.exists("Bank Account", f"{mp['bank_account_name']} - {mp['bank']}"):
		frappe.get_doc({"doctype": "Bank Account", "account_name": mp["bank_account_name"], "bank": mp["bank"],
			"account": wallet_account(), "is_company_account": 1, "company": s.COMPANY}).insert(ignore_permissions=True)
	frappe.db.commit()
	return {"dry_run": 0, "done": plan, "problems": []}


# ---------------------------------------------------------------------------
# In-store receipts
# ---------------------------------------------------------------------------

def counter_receipt_account(doc, method=None):
	"""Loan Repayment validate: a payment taken in store goes into that store's cash account.

	A statement reconciliation sets the bank account itself and is left alone."""
	if doc.docstatus or not doc.get("custom_hp_received_at") or doc.get("bank_account"):
		return
	account = store_cash_account(doc.custom_hp_received_at)
	if not account:
		frappe.throw(_("No cash account is set up for {0}. Choose the store the money was paid at, or leave "
			"'Received at store' blank for a bank receipt.").format(doc.custom_hp_received_at), title=_("In-store receipt"))
	doc.payment_account = account


# ---------------------------------------------------------------------------
# Daily Collections
# ---------------------------------------------------------------------------

def _cash_bank_accounts():
	return frappe.get_all("Account", filters={"company": s.COMPANY, "is_group": 0, "account_type": ["in", ["Cash", "Bank"]]},
		fields=["name", "account_number", "account_name"], limit_page_length=0)


def location_label(account, number=None, account_name=None):
	cfg = config()
	number = number or frappe.db.get_value("Account", account, "account_number")
	store = next((store for store in cfg["stores"] if store["number"] == number), None)
	if store:
		return store["store"]
	if number == cfg["mpaisa"]["wallet_number"]:
		return "M-PAiSA"
	return account_name or account


def classify(voucher_type, paid_from=None, references=(), credit_accounts=(), deposit_account=None,
		credit_account=None, unidentified_account=None, cash_bank=frozenset()):
	"""Which column a receipt belongs to, or None for a transfer.  Pure, for tests."""
	if voucher_type == "Loan Repayment":
		return "repayments"
	if voucher_type == "Payment Entry":
		if paid_from and paid_from == deposit_account:
			return "deposits"
		if any(ref == "Sales Invoice" for ref in references):
			return "fees"
		return "other"
	if voucher_type == "Journal Entry":
		credits = set(credit_accounts)
		if credits and credits <= set(cash_bank):
			return None  # money moved between our own cash and bank accounts
		if credit_account in credits:
			return "credit"
		if unidentified_account in credits:
			return "unidentified"
		return "other"
	return "other"


def collections(from_date=None, to_date=None):
	"""Receipts (one per voucher and receiving account) between the dates, classified."""
	from_date, to_date = getdate(from_date or today()), getdate(to_date or from_date or today())
	accounts = _cash_bank_accounts()
	if not accounts:
		return []
	by_name = {a.name: a for a in accounts}
	rows = frappe.db.sql(
		"""SELECT posting_date, account, voucher_type, voucher_no, SUM(debit) - SUM(credit) AS amount
		   FROM `tabGL Entry`
		   WHERE company = %(company)s AND is_cancelled = 0 AND IFNULL(is_opening, 'No') = 'No'
		     AND posting_date BETWEEN %(from)s AND %(to)s AND account IN %(accounts)s
		   GROUP BY posting_date, account, voucher_type, voucher_no
		   HAVING amount > 0.004""",
		{"company": s.COMPANY, "from": from_date, "to": to_date, "accounts": tuple(by_name)}, as_dict=True)
	deposit = s.account("ACC_DEPOSITS")
	credit = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": "2017"}, "name")
	unidentified = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": "2007"}, "name")
	cash_bank = frozenset(by_name)
	out = []
	for row in rows:
		info = {}
		if row.voucher_type == "Payment Entry":
			pe = frappe.db.get_value("Payment Entry", row.voucher_no, ["paid_from", "party", "cost_center"], as_dict=True) or {}
			refs = frappe.get_all("Payment Entry Reference", filters={"parent": row.voucher_no}, pluck="reference_doctype")
			kind = classify(row.voucher_type, paid_from=pe.get("paid_from"), references=refs, deposit_account=deposit)
			info = {"party": pe.get("party")}
		elif row.voucher_type == "Journal Entry":
			lines = frappe.get_all("Journal Entry Account", filters={"parent": row.voucher_no, "credit": [">", 0]},
				fields=["account", "party"])
			kind = classify(row.voucher_type, credit_accounts=[line.account for line in lines], credit_account=credit,
				unidentified_account=unidentified, cash_bank=cash_bank)
			info = {"loan": frappe.db.get_value("Journal Entry", row.voucher_no, "custom_hp_loan"),
				"party": next((line.party for line in lines if line.party), None)}
		elif row.voucher_type == "Loan Repayment":
			rep = frappe.db.get_value("Loan Repayment", row.voucher_no, ["against_loan", "applicant"], as_dict=True) or {}
			kind = "repayments"
			info = {"loan": rep.get("against_loan"), "party": rep.get("applicant")}
		else:
			kind = "other"
		if not kind:
			continue
		account = by_name[row.account]
		out.append(dict(date=row.posting_date, account=row.account, location=location_label(row.account, account.account_number, account.account_name),
			kind=kind, amount=flt(row.amount, 2), voucher_type=row.voucher_type, voucher_no=row.voucher_no, **info))
	return out


def summarise(receipts, by_date=True):
	"""Totals per (date,) location, one column per kind."""
	totals = {}
	for receipt in receipts:
		key = (receipt["date"] if by_date else None, receipt["location"])
		row = totals.setdefault(key, dict(date=key[0], location=key[1], **{c: 0.0 for c in COLUMNS}, total=0.0))
		row[receipt["kind"]] = flt(row[receipt["kind"]] + receipt["amount"], 2)
		row["total"] = flt(row["total"] + receipt["amount"], 2)
	return sorted(totals.values(), key=lambda r: (str(r["date"] or ""), r["location"]))


def require_role():
	if not set(ROLES) & set(frappe.get_roles()) and frappe.session.user != "Administrator":
		frappe.throw(_("Daily Collections is for Directors, Finance and Accounts."), frappe.PermissionError)


@frappe.whitelist()
def card_collected_today():
	require_role()
	total = sum(r["amount"] for r in collections(today(), today()))
	return {"value": flt(total, 2), "fieldtype": "Currency", "route": ["query-report", "HP Daily Collections"]}
