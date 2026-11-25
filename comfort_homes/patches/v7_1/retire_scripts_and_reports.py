"""7.1 Lean and Clean: remove the site-stored Server Scripts, the disabled signing Client Scripts and the
two retired reports.

* Live server-script logic now runs from app code (comfort_homes.flexi_events, registered in hooks.py).
* Dormant scripts (disabled, or APIs nothing calls: digital signing is not enabled and the HP Agreement
  handlers live in comfort_homes.agreement_signing) are deleted, not moved.
* "Flexi Manager Overview" is merged into Portfolio Control; "HP Customer Statement" is replaced by the
  HP Statement of Account print format.

Idempotent. Runs before fixtures sync, so it first points the Loan Application transition task at the app
method (the fixture says the same) and only then deletes the script it used to link.
"""
import frappe

SERVER_SCRIPTS = (
	"Attach signed CIRA Authority PDF",
	"Auto Create Draft PO on Loan Activation",
	"Auto Create Draft PO on Loan Activation - Co-Applicant",
	"Auto Create Draft PO on Loan Activation After Submission",
	"Auto Create Draft PO on Loan Activation After Submission - Co-Applicant",
	"Calculations",
	"Dynamic Laon P",
	"Flexi Copy HP Items to Loan",
	"Flexi Create Customer From Loan Application",
	"Flexi Default Customer Agreements",
	"Flexi Default Loan Disbursement Payment Method",
	"Flexi Default Loan Repayment Payment Method",
	"FLEXI DOCUMENT CUSTOMER SIGN",
	"FLEXI DOCUMENT DIRECTOR SIGN",
	"FLEXI DOCUMENT PREVIEW",
	"FLEXI DOCUMENT SYNC",
	"Flexi Flat Repayment Schedule Sync",
	"Flexi Loan Application Workflow Task",
	"Flexi Prepare Loan Amount",
	"Flexi Sync Prepared By From Assignment",
	"form1_print",
	"form2_print",
	"HIRE PURCHASE AGREEMENT",
	"HP AGREEMENT CUSTOMER SIGN",
	"HP AGREEMENT DIRECTOR SIGN",
	"HP AGREEMENT PREVIEW",
	"link documents to loan application",
	"Loan Application",
	"Loan Status Updation",
	"schedule1_print",
)
CLIENT_SCRIPTS = ("Flexi Customer Document Signing", "Flexi Customer Document Signing Order")
REPORTS = ("Flexi Manager Overview", "HP Customer Statement")
TRANSITION_TASKS = "Flexi Loan Application Transition Tasks"
APP_TASK = "Flexi Loan Application Task"


def _repoint_transition_task():
	if not frappe.db.exists("Workflow Transition Tasks", TRANSITION_TASKS):
		return
	for row in frappe.get_all("Workflow Transition Task", filters={"parent": TRANSITION_TASKS, "task": "Server Script"},
			fields=["name"]):
		frappe.db.set_value("Workflow Transition Task", row.name, {"task": APP_TASK, "link": None}, update_modified=False)


def execute():
	_repoint_transition_task()
	for name in SERVER_SCRIPTS:
		if frappe.db.exists("Server Script", name):
			frappe.delete_doc("Server Script", name, force=True, ignore_permissions=True)
	for name in CLIENT_SCRIPTS:
		if frappe.db.exists("Client Script", name):
			frappe.delete_doc("Client Script", name, force=True, ignore_permissions=True)
	for name in REPORTS:
		if frappe.db.exists("Report", name):
			frappe.delete_doc("Report", name, force=True, ignore_permissions=True)
	frappe.clear_cache()
