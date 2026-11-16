"""Minimal-access permissions for the roles that run Flexi.

Principles
  * least privilege: a role gets only what its workflow step or job needs;
  * maker and checker are different roles (the person who disburses cannot raise
    or pay the supplier invoice, and the person who receives money cannot
    disburse);
  * nobody gets Delete on lending or accounting documents: cancel and amend only;
  * Guest and All hold no rights on customer data.

``plan()`` lists every change and touches nothing.  ``apply()`` replaces the Custom
DocPerm rows for exactly the doctypes below and removes the listed stray rows.
Deleted rows are kept by Frappe in "Deleted Document", so a change can be restored.
Roles named here that do not exist on the site are skipped and reported.
"""

import frappe

# r read, w write, c create, s submit, x cancel, a amend.  Never d (delete).
# report/export/print/email follow read; share is not granted.
MATRIX = {
	"Loan Application": {
		"Loan LOS User": "rwc", "Loan Officer": "rw", "Loan Processor": "rw", "Finance Manager": "rw",
		"Director": "rw", "Disbursement Officer": "rws", "Accounts Officer": "rw",
		"Loan Manager": "rwcxa", "Loan Reporter": "r", "Auditor": "r", "System Manager": "r",
	},
	"Co-Applicant": {
		"Loan LOS User": "rwc", "Loan Officer": "rw", "Loan Processor": "r", "Loan Manager": "rwcxa",
		"Loan Reporter": "r", "Auditor": "r", "System Manager": "r",
	},
	"Loan": {
		"Disbursement Officer": "rwcs", "Finance Manager": "r", "Accounts Officer": "r", "Accounts Manager": "r",
		"Loan Manager": "rwxa", "Loan Reporter": "r", "Auditor": "r", "System Manager": "r",
	},
	"Loan Disbursement": {
		"Disbursement Officer": "rwcs", "Finance Manager": "r", "Accounts Officer": "r", "Accounts Manager": "rx",
		"Loan Manager": "r", "Loan Reporter": "r", "Auditor": "r", "System Manager": "r",
	},
	"Loan Repayment": {
		"Accounts Officer": "rwcs", "Accounts Manager": "rwcsxa", "Finance Manager": "r", "Loan Manager": "r",
		"Loan Reporter": "r", "Auditor": "r", "System Manager": "r",
	},
	"Loan Write Off": {
		"Accounts Manager": "rwc", "Finance Manager": "rsx", "Loan Manager": "r", "Loan Reporter": "r",
		"Auditor": "r", "System Manager": "r",
	},
	"Loan Balance Adjustment": {
		"Accounts Manager": "rwcsx", "Finance Manager": "r", "Loan Manager": "r", "Loan Reporter": "r",
		"Auditor": "r", "System Manager": "r",
	},
	"Loan Product": {
		"Loan Manager": "rwc", "Finance Manager": "r", "Loan LOS User": "r", "Loan Officer": "r",
		"Loan Processor": "r", "Disbursement Officer": "r", "Accounts Officer": "r", "Auditor": "r",
		"System Manager": "rw",
	},
	"Payment Entry": {
		"Accounts Officer": "rwcs", "Accounts Manager": "rwcsxa", "Finance Manager": "r", "Auditor": "r",
		"System Manager": "r",
	},
	"Journal Entry": {
		"Accounts Manager": "rwcsxa", "Accounts Officer": "r", "Finance Manager": "r", "Auditor": "r",
		"System Manager": "r",
	},
	"Purchase Order": {
		"Disbursement Officer": "rwc", "Finance Manager": "rwsx", "Director": "rws", "Accounts Officer": "r",
		"Auditor": "r", "System Manager": "r",
	},
	"Purchase Invoice": {
		"Accounts Officer": "rwcs", "Accounts Manager": "rwcsxa", "Finance Manager": "r", "Auditor": "r",
		"System Manager": "r",
	},
}

# (doctype, role) pairs whose rows are simply removed.  Rights that should never have existed.
REMOVE = [
	("Contact", "Guest"), ("Address", "Guest"), ("Contact", "All"), ("File", "All"),
	("Loan Bank Reconciliation", "All"), ("Loan Application", "Employee"), ("Co-Applicant", "Employee"),
]

FLAGS = {"r": "read", "w": "write", "c": "create", "s": "submit", "x": "cancel", "a": "amend"}


def _row(doctype, role, letters):
	row = {"doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType", "parentfield": "permissions",
		"role": role, "permlevel": 0, "delete": 0}
	for letter, field in FLAGS.items():
		row[field] = 1 if letter in letters else 0
	if "r" in letters:
		row.update({"report": 1, "export": 1, "print": 1, "email": 1})
	return row


def plan():
	changes = {"replace": {}, "remove": [], "missing_roles": []}
	roles = set(frappe.get_all("Role", pluck="name"))
	for doctype, grants in MATRIX.items():
		current = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=["role", "read", "write", "create", "delete", "submit", "cancel", "amend"])
		changes["replace"][doctype] = {
			"before": sorted(f"{r.role}:{''.join(l for l, f in FLAGS.items() if r.get(f))}{'d' if r.get('delete') else ''}" for r in current),
			"after": sorted(f"{role}:{letters}" for role, letters in grants.items() if role in roles),
		}
		for role in grants:
			if role not in roles and role not in changes["missing_roles"]:
				changes["missing_roles"].append(role)
	for doctype, role in REMOVE:
		names = frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": role}, pluck="name")
		if names:
			changes["remove"].append(f"{doctype} / {role}")
	return changes


@frappe.whitelist()
def apply(dry_run=1):
	"""Plan by default.  With dry_run=0, replace the rows.  System Manager only."""
	frappe.only_for("System Manager")
	result = plan()
	if int(dry_run):
		return {"dry_run": 1, **result}
	roles = set(frappe.get_all("Role", pluck="name"))
	for doctype, grants in MATRIX.items():
		for name in frappe.get_all("Custom DocPerm", filters={"parent": doctype}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, force=1, ignore_permissions=True)
		for role, letters in grants.items():
			if role in roles:
				frappe.get_doc(_row(doctype, role, letters)).insert(ignore_permissions=True)
		frappe.clear_cache(doctype=doctype)
	for doctype, role in REMOVE:
		for name in frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": role}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, force=1, ignore_permissions=True)
		frappe.clear_cache(doctype=doctype)
	frappe.db.commit()
	return {"dry_run": 0, **result}
