"""Approval controls on the Loan Application (dev27, batch 5).

* Approval is bound to the terms approved (F11).  On the move into Approved the financial terms,
  goods and co-applicants are fingerprinted (``custom_hp_approved_terms_hash``).  If any of them
  change while the application is Approved or at Contract Signing, the save returns it to
  Pending Approval (status Open), so a Finance Manager or Director must approve or reject again.
* "Not Achieved" (or not assessed) eligibility can only be approved with a written override
  reason (F13a).
* A customer created by the application is created disabled and enabled when the application is
  approved (F24); rejected or withdrawn applications leave no active customer.
* A withdrawn or rejected application's deposit is refunded with a draft Payment Entry (F26).

Registered last in Loan Application ``before_save``, after the document controls.
"""

import frappe
from frappe import _

from comfort_homes.hp_lending import hp_settings as s

APPROVED = "Approved"
BOUND_STATES = ("Approved", "Contract Signing")
RETURN_STATE = "Pending Approval"
APPROVAL_TERMS = (
	"loan_product", "applicant", "custom_flexi_cash_price", "custom_flexi_deposit_amount",
	"custom_flexi_admin_fee", "custom_flexi_documentation_fee", "custom_flexi_net_amount_financed",
	"custom_flexi_term_charges", "custom_flexi_total_receivable", "custom_flexi_term_months",
	"custom_flexi_interest_rate", "custom_flexi_payment_period", "custom_flexi_first_instalment_date",
	"custom_hp_default_rate_monthly",
)
ITEM_FIELDS = ("product_description", "quantity", "unit_price", "total_amount")
REFUND_STATES = ("Withdrawn", "Rejected")
REFUND_ROLES = ("Finance Manager", "Accounts Manager", "System Manager")


def approved_terms(doc):
	"""Pure: the facts an approver approved."""
	from comfort_homes.print_helpers import get_co_applicants
	try:
		co_applicants = sorted(p.name for p in get_co_applicants(doc))
	except Exception:
		co_applicants = []
	return {
		"terms": {f: str(doc.get(f) if doc.get(f) is not None else "") for f in APPROVAL_TERMS},
		"items": [{f: str(row.get(f) if row.get(f) is not None else "") for f in ITEM_FIELDS}
			for row in doc.get("custom_flexi_purchase_items") or []],
		"co_applicants": co_applicants,
	}


def terms_hash(doc):
	from comfort_homes.hp_lending.document_policy import fingerprint
	return fingerprint(approved_terms(doc))


def changed_terms(before, after):
	"""Pure: labels of what differs between two approved_terms() snapshots."""
	changes = [f for f in APPROVAL_TERMS if before["terms"].get(f) != after["terms"].get(f)]
	if before["items"] != after["items"]:
		changes.append("goods")
	if before["co_applicants"] != after["co_applicants"]:
		changes.append("co-applicants")
	return changes


def bind_approval(doc, method=None):
	if doc.get("company") != s.COMPANY or doc.get("custom_hp_converted"):
		return
	previous = doc.get_doc_before_save()
	old = previous.get("workflow_state") if previous else "Draft"
	state = doc.get("workflow_state") or "Draft"

	if state == APPROVED and old != APPROVED:
		_require_override_reason(doc)
		doc.custom_hp_approved_terms_hash = terms_hash(doc)
		_enable_customer(doc)
		return

	if old in BOUND_STATES and doc.get("custom_hp_approved_terms_hash"):
		if terms_hash(doc) == doc.custom_hp_approved_terms_hash:
			return
		changes = _labels(doc, changed_terms(approved_terms(previous), approved_terms(doc)) if previous else [])
		if state != old:
			frappe.throw(_("Terms changed after approval ({0}). Save the change first: the application returns to "
				"Pending Approval for the Finance Manager or Director.").format(", ".join(changes) or _("terms")),
				title=_("Re-approval Required"))
		doc.workflow_state = RETURN_STATE
		doc.status = "Open"
		doc.custom_hp_approved_terms_hash = None
		frappe.msgprint(_("Terms changed after approval ({0}). The application has gone back to Pending Approval "
			"for the Finance Manager or Director.").format(", ".join(changes) or _("terms")),
			title=_("Returned for Re-approval"), indicator="orange")
		doc.add_comment("Workflow", _("Returned for re-approval: {0} changed after approval.").format(", ".join(changes) or _("terms")))


def _labels(doc, fields):
	meta = doc.get("meta") if isinstance(doc, dict) else getattr(doc, "meta", None)
	return [meta.get_label(f) if meta and f.startswith(("custom_", "loan_", "applicant")) and hasattr(meta, "get_label") else f for f in fields]


def _require_override_reason(doc):
	status = doc.get("custom_hp_decision_eligibility") or doc.get("custom_flexi_eligibility_status")
	if status != "Achieved" and not (doc.get("custom_hp_eligibility_override_reason") or "").strip():
		frappe.throw(_("Eligibility is {0}. Enter the Eligibility Override Reason before approving.").format(status or _("Not Assessed")),
			title=_("Override Reason Required"))


def _enable_customer(doc):
	if not doc.get("applicant"):
		return
	row = frappe.db.get_value("Customer", doc.applicant, ["disabled", "custom_hp_pending_application"], as_dict=True)
	if row and row.disabled and row.custom_hp_pending_application == doc.name:
		frappe.db.set_value("Customer", doc.applicant, {"disabled": 0, "custom_hp_pending_application": None})


@frappe.whitelist(methods=["POST"])
def create_deposit_refund(application):
	"""Draft Payment Entry refunding the deposit of a withdrawn or rejected application (Dr 2010 / Cr bank)."""
	frappe.only_for(REFUND_ROLES)
	doc = frappe.get_doc("Loan Application", application)
	doc.check_permission("read")
	if doc.get("workflow_state") not in REFUND_STATES:
		frappe.throw(_("Only a withdrawn or rejected application's deposit can be refunded."))
	receipt = doc.get("custom_hp_deposit_payment_entry")
	if not receipt or frappe.db.get_value("Payment Entry", receipt, "docstatus") != 1:
		frappe.throw(_("{0} has no submitted deposit Payment Entry.").format(application))
	key = f"HP-DEP-REFUND-{application}"
	existing = frappe.db.get_value("Payment Entry", {"reference_no": key, "docstatus": ["!=", 2]}, "name")
	if existing:
		return {"payment_entry": existing, "existing": True}
	paid = frappe.get_doc("Payment Entry", receipt)
	refund = frappe.get_doc({
		"doctype": "Payment Entry", "payment_type": "Pay", "company": paid.company,
		"posting_date": frappe.utils.today(), "party_type": "Customer", "party": paid.party,
		"paid_from": paid.paid_to, "paid_to": paid.paid_from,
		"paid_amount": paid.paid_amount, "received_amount": paid.paid_amount,
		"reference_no": key, "reference_date": frappe.utils.today(),
		"remarks": f"Refund of HP deposit {receipt}: application {application} {doc.workflow_state.lower()} [{key}]",
	})
	refund.insert()
	return {"payment_entry": refund.name, "existing": False}
