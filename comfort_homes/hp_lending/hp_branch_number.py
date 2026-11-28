"""Branch HP account numbers for new contracts (7.1.6).

New contracts get the same kind of number the branches used in MYOB: branch prefix + year + running number,
e.g. GRA2026527. The number is given once, when an approved application moves to Contract Signing, so it
prints on the contract pack; drafts and rejected applications use none. It goes on the application (the
Loan reads it from there), on the customer as their HP account when they have none yet, and on the Bank PPT
Authority as the narration the customer's bank must use.

The next number is the highest already used for that prefix and year + 1, read from applications and
customers at the moment it is given, so numbers brought over from MYOB or typed by hand are counted and the
sequence neither breaks nor repeats. It restarts at 001 each calendar year.

Prefixes (owner, 28-11-2026): Grantham GRA, Namaka NAM, Lautoka LTK, Nausori NAU, Denarau DEN, Nakasi NAK,
HQ and Web HQ. Nadi is not a branch; Suva is Grantham.

Contracts recorded after branch numbering stopped (from 13-10-2026: MYOB contracts carrying UAT application
numbers, then devcom contracts carrying their application ID) are renumbered once from a reviewed list
(``apply_plan``); the old number stays on the application and the customer as the previous HP account number
and keeps matching bank and M-PAiSA payments.
"""

import re

import frappe
from frappe import _
from frappe.utils import getdate, today

from comfort_homes.hp_lending import hp_settings as s

FIELD = "custom_flexi_hp_account_number"
PREVIOUS = "custom_hp_previous_account_number"
PLAN_ROLES = ("System Manager", "Finance Manager")
PREFIX_BY_BRANCH = {
	"GRANTHAM": "GRA", "NAMAKA": "NAM", "LAUTOKA": "LTK", "NAUSORI": "NAU",
	"DENARAU": "DEN", "NAKASI": "NAK", "HQ": "HQ", "WEB": "HQ",
}
PREFIXES = tuple(sorted(set(PREFIX_BY_BRANCH.values())))
NUMBER = re.compile(r"^(?P<prefix>[A-Z]{2,3})(?P<year>20\d\d)(?P<seq>\d{3,4})$")


def branch_key(location):
	"""Pure: "Nausori - Flexi" -> "NAUSORI" (the Branch/Location is a Cost Center named "<branch> - <abbr>")."""
	name = str(location or "").strip()
	if " - " in name:
		name = name.rsplit(" - ", 1)[0]
	return name.strip().split(" ")[0].upper() if name.strip() else ""


def prefix_for(location):
	return PREFIX_BY_BRANCH.get(branch_key(location))


def is_branch_number(value):
	match = NUMBER.match(str(value or "").strip().upper())
	return bool(match) and match.group("prefix") in PREFIXES


def next_number(prefix, year, existing):
	"""Pure: the next number for ``prefix`` and ``year`` after the highest in ``existing`` (any other values ignored)."""
	highest = 0
	for value in existing:
		match = NUMBER.match(str(value or "").strip().upper())
		if match and match.group("prefix") == prefix and int(match.group("year")) == int(year):
			highest = max(highest, int(match.group("seq")))
	return f"{prefix}{year}{highest + 1:03d}"


def existing_numbers(prefix, year):
	like = f"{prefix}{year}%"
	values = [r[0] for r in frappe.db.sql(f"SELECT `{FIELD}` FROM `tabLoan Application` WHERE `{FIELD}` LIKE %s", (like,))]
	if frappe.db.has_column("Customer", "custom_ch_account_reference_no"):
		values += [r[0] for r in frappe.db.sql(
			"SELECT custom_ch_account_reference_no FROM `tabCustomer` WHERE custom_ch_account_reference_no LIKE %s", (like,))]
	return values


def assign(doc, on_date=None):
	"""Give ``doc`` its branch number unless it already has one. Returns the number."""
	current = str(doc.get(FIELD) or "").strip()
	if is_branch_number(current):
		return current
	prefix = prefix_for(doc.get("custom_branchlocation"))
	if not prefix:
		frappe.throw(_("Branch/Location {0} has no HP account number prefix. Ask the system administrator to add it.").format(
			doc.get("custom_branchlocation") or _("(blank)")), title=_("HP account number"))
	year = getdate(on_date or today()).year
	try:
		frappe.db.sql("SELECT GET_LOCK(%s, 10)", (f"hp-branch-number-{prefix}{year}",))
	except Exception:
		pass  # the uniqueness check in hp_account_number still stops a duplicate
	number = next_number(prefix, year, existing_numbers(prefix, year))
	doc.set(FIELD, number)
	_record_on_customer(doc.get("applicant"), number)
	return number


def _record_on_customer(customer, number):
	"""The bank and M-PAiSA matching reads the customer's HP account; set it when the customer has none."""
	if not customer or not frappe.db.has_column("Customer", "custom_ch_account_reference_no"):
		return
	if frappe.db.get_value("Customer", customer, "custom_ch_account_reference_no"):
		return  # a returning customer keeps theirs; matching also reads the application's number
	frappe.db.set_value("Customer", customer, "custom_ch_account_reference_no", number, update_modified=False)


def assign_on_contract_signing(doc, method=None):
	"""Loan Application before_validate: Approved -> Contract Signing gives the contract its branch number."""
	if doc.get("company") and doc.company != s.COMPANY:
		return
	old = doc.get_doc_before_save()
	if not old or old.get("workflow_state") != "Approved" or doc.get("workflow_state") != "Contract Signing":
		return
	assign(doc)


def _cost_center(store):
	return frappe.db.get_value("Cost Center", {"cost_center_name": store, "is_group": 0}, "name") or f"{store} - Flexi"


def check_plan(rows, current):
	"""Pure: problems in a reviewed plan. ``current`` = {application: HP number now}."""
	problems, seen = [], set()
	for r in rows:
		app, action = r.get("application"), r.get("action")
		if action not in ("Renumber", "Change Branch/Location"):
			continue
		if app not in current:
			problems.append(f"{app}: not found")
			continue
		if str(current[app] or "") != str(r.get("current") or ""):
			problems.append(f"{app}: HP number is now {current[app]}, the plan expected {r.get('current')}")
		if action == "Renumber":
			new = str(r.get("new_number") or "").upper()
			if not is_branch_number(new):
				problems.append(f"{app}: {new} is not a branch number")
			if new in seen:
				problems.append(f"{app}: {new} is used twice in the plan")
			seen.add(new)
	return problems


@frappe.whitelist(methods=["POST"])
def apply_plan(rows, dry_run=1):
	"""Apply a reviewed renumbering list: [{application, current, action, new_number, new_branch}].

	Renumber: the application gets the new number and keeps the old one as previous; the customer's HP account
	moves to the new number (old kept as previous) when it held the old one or was blank.
	Change Branch/Location: only the application's Branch/Location changes; the number stays.
	``dry_run`` (default) checks everything and changes nothing."""
	if not set(PLAN_ROLES) & set(frappe.get_roles()):
		frappe.throw(_("Only a System Manager or Finance Manager can renumber contracts."), frappe.PermissionError)
	rows = frappe.parse_json(rows) if isinstance(rows, str) else rows
	names = [r["application"] for r in rows if r.get("action") in ("Renumber", "Change Branch/Location")]
	current = {a.name: a.get(FIELD) for a in frappe.get_all("Loan Application", filters={"name": ["in", names]},
		fields=["name", FIELD], limit_page_length=0)}
	problems = check_plan(rows, current)
	taken = {r.get("new_number") for r in rows if r.get("action") == "Renumber"}
	if taken:
		clash = frappe.get_all("Loan Application", filters={FIELD: ["in", list(taken)]}, fields=["name", FIELD], limit_page_length=0)
		problems += [f"{c.name} already holds {c.get(FIELD)}" for c in clash]
	result = {"renumbered": 0, "branch_changed": 0, "customers_updated": 0, "problems": problems, "applied": False}
	if problems or int(dry_run):
		result["would_renumber"] = sum(1 for r in rows if r.get("action") == "Renumber")
		result["would_change_branch"] = sum(1 for r in rows if r.get("action") == "Change Branch/Location")
		return result
	for r in rows:
		app, action = r["application"], r.get("action")
		if action == "Renumber":
			old, new = r["current"], r["new_number"].upper()
			frappe.db.set_value("Loan Application", app, {FIELD: new, PREVIOUS: old}, update_modified=False)
			customer = frappe.db.get_value("Loan Application", app, "applicant")
			if customer and frappe.db.has_column("Customer", "custom_ch_account_reference_no"):
				held = frappe.db.get_value("Customer", customer, "custom_ch_account_reference_no")
				if not held or held == old:
					values = {"custom_ch_account_reference_no": new}
					if held:
						values[PREVIOUS] = old
					frappe.db.set_value("Customer", customer, values, update_modified=False)
					result["customers_updated"] += 1
			frappe.get_doc("Loan Application", app).add_comment("Comment",
				f"HP account number changed from {old} to {new} (branch numbering, reviewed list 28-11-2026). "
				f"The old number still matches bank and M-PAiSA payments.")
			result["renumbered"] += 1
		elif action == "Change Branch/Location":
			frappe.db.set_value("Loan Application", app, "custom_branchlocation", _cost_center(r["new_branch"]), update_modified=False)
			frappe.get_doc("Loan Application", app).add_comment("Comment",
				f"Branch/Location corrected to {r['new_branch']} to match its HP account number {r['current']}.")
			result["branch_changed"] += 1
	result["applied"] = True
	return result
