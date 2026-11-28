"""Loan Bank Reconciliation: readable IDs and a review-friendly table (7.1.6).

IDs are BREC-<bank code>-<year>-<running number>, e.g. BREC-BSP-2026-0001; the counter restarts each
calendar year per bank (Frappe keeps one counter per prefix). The bank code is a short field on the Bank
Account; the after-migrate step fills it for the company's accounts (by ledger account number) where blank.
Reconciliations created before 7.1.6 keep their names: they are quoted in journal remarks and duplicate notes.
"""

import re

import frappe
from frappe.utils import getdate, today

CODE_FIELD = "custom_hp_bank_code"
# Ledger account number -> code (owner, 28-11-2026)
CODES_BY_ACCOUNT_NUMBER = {
	"1201": "BSP", "1005": "BSPCHF", "2016": "WBC", "1008": "WBCC", "1009": "BRED",
	"1010": "HFC", "1204": "ANZ", "1012": "WBCCHD", "1206": "MPAISA",
}


def clean_code(value):
	"""Pure: letters and digits only, upper case, at most 8 characters."""
	return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())[:8]


def series_prefix(code, year):
	return f"BREC-{clean_code(code) or 'BANK'}-{int(year)}-.####"


def bank_code(bank_account):
	if not bank_account:
		return "BANK"
	if frappe.db.has_column("Bank Account", CODE_FIELD):
		code = clean_code(frappe.db.get_value("Bank Account", bank_account, CODE_FIELD))
		if code:
			return code
	return clean_code(frappe.db.get_value("Bank Account", bank_account, "bank")) or "BANK"


def autoname(doc, method=None):
	"""Loan Bank Reconciliation autoname: BREC-BSP-2026-0001."""
	doc.name = make_autoname(series_prefix(bank_code(doc.get("bank_account")), getdate(today()).year), doc=doc)


def make_autoname(series, doc=None):
	from frappe.model.naming import make_autoname as frappe_make_autoname
	return frappe_make_autoname(series, doc=doc)


def ensure_bank_codes():
	"""Idempotent: fill the reconciliation code on the company's bank accounts where it is blank."""
	if not frappe.db.has_column("Bank Account", CODE_FIELD):
		return []
	done = []
	for row in frappe.get_all("Bank Account", filters={"is_company_account": 1}, fields=["name", "account", CODE_FIELD],
			limit_page_length=0):
		if row.get(CODE_FIELD) or not row.account:
			continue
		number = frappe.db.get_value("Account", row.account, "account_number")
		code = CODES_BY_ACCOUNT_NUMBER.get(str(number or ""))
		if code:
			frappe.db.set_value("Bank Account", row.name, CODE_FIELD, code, update_modified=False)
			done.append((row.name, code))
	return done
