"""HP Account Number guard on Loan Application (7.1.4).

The HP account number printed on the contract and the statement must belong to this contract.
On devcom two new applications (00947, 00952) had another application's ID copied into the field
(their UAT application numbers), so their statements pointed at a different customer.

* An application ID (``ACC-LOAP-YYYY-NNNNN``) is accepted only if it is this application's own ID.
  This is checked on every save, also after submit, so a wrong value has to be corrected.
* When the number is entered or changed, it must not already be used by an application of a
  different customer (the same customer may share one, as some MYOB contracts do).
"""

import re

import frappe
from frappe import _

FIELD = "custom_flexi_hp_account_number"
APPLICATION_ID = re.compile(r"^ACC-LOAP-\d{4}-\d+$", re.IGNORECASE)


def normalise(value):
	return re.sub(r"\s+", "", str(value or "")).upper()


def is_other_application_id(value, own_name):
	"""Pure: True when ``value`` is an application ID other than ``own_name``."""
	value = normalise(value)
	return bool(APPLICATION_ID.match(value)) and value != normalise(own_name)


def _changed(doc):
	if doc.is_new():
		return True
	changed = getattr(doc, "has_value_changed", None)
	return changed(FIELD) if changed else True


def validate(doc, method=None):
	value = str(doc.get(FIELD) or "").strip()
	if not value:
		return
	if value != doc.get(FIELD):
		doc.set(FIELD, value)
	if is_other_application_id(value, doc.name):
		owner = frappe.db.get_value("Loan Application", normalise(value), "applicant_name")
		frappe.throw(_("HP Account Number {0} is the ID of another application{1}. Enter this contract's own HP account "
			"number, or leave it blank to use this application's ID ({2}).").format(
				value, _(" ({0})").format(owner) if owner else "", doc.name),
			title=_("Wrong HP Account Number"))
	if not _changed(doc):
		return
	others = frappe.get_all("Loan Application", filters={FIELD: value, "name": ["!=", doc.name]},
		fields=["name", "applicant", "applicant_name"], limit_page_length=5)
	clash = [o for o in others if not doc.get("applicant") or o.applicant != doc.get("applicant")]
	if clash:
		frappe.throw(_("HP Account Number {0} is already used by {1} ({2}). Each customer's contract needs its own number.").format(
			value, clash[0].name, clash[0].applicant_name or clash[0].applicant or ""), title=_("HP Account Number In Use"))
