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
		"Director": "rw", "Disbursement Officer": "rws", "Accounts Officer": "rw", "Accounts Manager": "r",
		"Loan Manager": "rwcxa", "Loan Reporter": "r", "Auditor": "r", "System Manager": "r",
	},
	"Co-Applicant": {
		"Loan LOS User": "rwc", "Loan Officer": "rw", "Loan Processor": "rw", "Finance Manager": "rw", "Director": "rw", "Loan Manager": "rwc",
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


# Assessment field-level access is separate from document create/submit rights.
ASSESSMENT = {
	"Loan Application": {"Loan LOS User": "r", "Loan Officer": "rw", "Loan Processor": "rw",
		"Finance Manager": "rw", "Director": "rw", "Loan Manager": "rw", "Auditor": "r",
		"Loan Reporter": "r", "Disbursement Officer": "r", "Accounts Officer": "r", "Accounts Manager": "r", "System Manager": "r"},
	"Co-Applicant": {"Loan LOS User": "r", "Loan Officer": "rw", "Loan Processor": "rw",
		"Finance Manager": "rw", "Director": "rw", "Loan Manager": "rw", "Auditor": "r", "Loan Reporter": "r", "System Manager": "r"},
}
MATRIX["Loan Bank Reconciliation"] = {"Accounts Officer": "rwc", "Accounts Manager": "rwc", "Finance Manager": "r", "Auditor": "r", "System Manager": "r"}

# (doctype, role) pairs whose rows are simply removed.  Rights that should never have existed.
REMOVE = [
	("Contact", "Guest"), ("Address", "Guest"), ("Contact", "All"), ("File", "All"),
	("Loan Bank Reconciliation", "All"), ("Loan Application", "Employee"), ("Co-Applicant", "Employee"),
]

FLAGS = {"r": "read", "w": "write", "c": "create", "s": "submit", "x": "cancel", "a": "amend"}


def _row(doctype, role, letters, permlevel=0):
	row = {"doctype": "Custom DocPerm", "parent": doctype, "parenttype": "DocType", "parentfield": "permissions",
		"role": role, "permlevel": permlevel, "delete": 0, "share": 0, "email": 0, "import": 0}
	for letter, field in FLAGS.items():
		row[field] = 1 if letter in letters else 0
	if "r" in letters:
		row.update({"report": 1, "export": int(role in ("Auditor", "Loan Reporter", "Accounts Manager", "Finance Manager")), "print": 1})
	return row


PERMISSION_FIELDS = ["role", "permlevel", "if_owner", "read", "write", "create", "delete", "submit", "cancel", "amend", "report", "export", "print", "email", "share", "import"]


def desired_rows(doctype, roles):
	rows = [_row(doctype, role, letters) for role, letters in MATRIX[doctype].items() if role in roles]
	rows.extend(_row(doctype, role, letters, 1) for role, letters in ASSESSMENT.get(doctype, {}).items() if role in roles)
	return rows


def plan():
	changes = {"replace": {}, "remove": [], "missing_roles": []}
	roles = set(frappe.get_all("Role", pluck="name"))
	for doctype in MATRIX:
		current = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=PERMISSION_FIELDS)
		changes["replace"][doctype] = {
			"before": [dict(row) for row in current] if current else [dict(row) for row in frappe.get_all("DocPerm", filters={"parent": doctype}, fields=PERMISSION_FIELDS)],
			"before_source": "Custom DocPerm" if current else "DocPerm",
			"custom_before": [dict(row) for row in current],
			"after": [{key: row.get(key, 0) for key in PERMISSION_FIELDS} for row in desired_rows(doctype, roles)],
		}
		for role in set(MATRIX[doctype]) | set(ASSESSMENT.get(doctype, {})):
			if role not in roles and role not in changes["missing_roles"]:
				changes["missing_roles"].append(role)
	for doctype, role in REMOVE:
		if frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role}):
			changes["remove"].append({"doctype": doctype, "role": role})
	return changes


@frappe.whitelist(methods=["POST"])
def apply(dry_run=1):
	"""Plan by default.  With dry_run=0, replace the rows.  System Manager only."""
	frappe.only_for("System Manager")
	result = plan()
	if int(dry_run):
		return {"dry_run": 1, **result}
	if result["missing_roles"]:
		frappe.throw("Create the missing workflow roles before applying permissions")
	roles = set(frappe.get_all("Role", pluck="name"))
	for doctype, grants in MATRIX.items():
		for name in frappe.get_all("Custom DocPerm", filters={"parent": doctype}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, force=1, ignore_permissions=True)
		for row in desired_rows(doctype, roles):
			frappe.get_doc(row).insert(ignore_permissions=True)
		frappe.clear_cache(doctype=doctype)
	for doctype, role in REMOVE:
		for name in frappe.get_all("Custom DocPerm", filters={"parent": doctype, "role": role}, pluck="name"):
			frappe.delete_doc("Custom DocPerm", name, force=1, ignore_permissions=True)
		frappe.clear_cache(doctype=doctype)
	frappe.db.commit()
	return {"dry_run": 0, **result}
