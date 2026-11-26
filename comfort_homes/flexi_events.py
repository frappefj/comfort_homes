"""7.1: the Flexi document events that used to be Server Scripts, now app code.

Each function is the former server script, unchanged in effect. They are registered in hooks.py at the END
of each event's list, because Frappe ran server scripts after the app's own doc_events hooks for the same
event. Server Script event names map to: "Before Validate" -> before_validate, "Before Save" -> validate,
"After Save" -> on_update.

Former script names are kept in each docstring so the change can be traced.
"""
import functools

import frappe
from frappe.utils import flt


def as_server_script(fn):
	"""Frappe skipped doc-event Server Scripts during install and migrate; keep that, so behaviour is unchanged."""
	@functools.wraps(fn)
	def run(doc, *args, **kwargs):
		if getattr(frappe.flags, "in_install", False) or getattr(frappe.flags, "in_migrate", False):
			return None
		return fn(doc, *args, **kwargs)
	return run

CANONICAL_RATES = ("12", "19", "22", "25", "27")


# ---------------------------------------------------------------------------
# Loan
# ---------------------------------------------------------------------------

@as_server_script
def copy_hp_items_to_loan(doc, method=None):
	"""Was server script "Flexi Copy HP Items to Loan" (Loan, Before Validate)."""
	if doc.loan_application and not doc.get("custom_flexi_purchase_items"):
		application = frappe.get_doc("Loan Application", doc.loan_application)
		for source_row in application.get("custom_flexi_purchase_items") or []:
			target_row = doc.append("custom_flexi_purchase_items", {})
			target_row.product_description = source_row.product_description
			target_row.quantity = source_row.quantity
			target_row.unit_price = source_row.unit_price
			target_row.total_amount = source_row.total_amount


# ---------------------------------------------------------------------------
# Loan Disbursement / Loan Repayment
# ---------------------------------------------------------------------------

def _agreed_mode(loan):
	loan_application = frappe.db.get_value("Loan", loan, "loan_application")
	agreed = frappe.db.get_value("Loan Application", loan_application, "custom_flexi_payment_method") if loan_application else None
	from comfort_homes.loan_mapping import agreed_mode_of_payment
	return agreed_mode_of_payment(agreed)


@as_server_script
def default_disbursement_mode(doc, method=None):
	"""Was server script "Flexi Default Loan Disbursement Payment Method" (Before Validate)."""
	if not doc.mode_of_payment and doc.against_loan:
		mode = _agreed_mode(doc.against_loan)
		if mode:
			doc.mode_of_payment = mode


@as_server_script
def default_repayment_mode(doc, method=None):
	"""Was server script "Flexi Default Loan Repayment Payment Method" (Before Validate)."""
	if not doc.mode_of_payment and doc.against_loan and not doc.get("bank_account"):
		disbursements = frappe.get_all("Loan Disbursement",
			filters={"against_loan": doc.against_loan, "docstatus": 1},
			fields=["mode_of_payment"], order_by="creation desc", limit_page_length=1)
		if disbursements and disbursements[0].get("mode_of_payment"):
			doc.mode_of_payment = disbursements[0].get("mode_of_payment")
		else:
			mode = _agreed_mode(doc.against_loan)
			if mode:
				doc.mode_of_payment = mode


# ---------------------------------------------------------------------------
# Loan Application
# ---------------------------------------------------------------------------

@as_server_script
def create_customer(doc, method=None):
	"""Was server script "Flexi Create Customer From Loan Application" (Before Validate).

	dev27 (F24): created as Individual and disabled, marked with the application; enabled on approval
	(hp_approval). An existing customer with the same TIN is used as it is."""
	if doc.applicant_type != "Customer" or doc.applicant:
		return
	existing_customer = frappe.db.get_value("Customer", {"custom_tin_number": doc.custom_tin_number}, "name") \
		if doc.custom_tin_number else None
	if existing_customer:
		doc.applicant = existing_customer
		return
	customer = frappe.new_doc("Customer")
	customer.customer_name = doc.applicant_name
	customer.customer_type = "Individual"
	customer.disabled = 1
	customer.custom_hp_pending_application = doc.name  # named before before_validate, also on insert
	customer.mobile_number = doc.applicant_phone_number
	customer.email_address = doc.applicant_email_address
	customer.custom_tin_number = doc.custom_tin_number
	customer.save(ignore_permissions=True)
	contact = frappe.new_doc("Contact")
	contact.first_name = doc.applicant_name
	contact.append("email_ids", {"email_id": doc.applicant_email_address, "is_primary": 1})
	contact.append("phone_nos", {"phone": doc.applicant_phone_number, "is_primary_mobile_no": 1})
	contact.append("links", {"link_doctype": "Customer", "link_name": customer.name})
	contact.save(ignore_permissions=True)
	address_city = doc.city or doc.custom_new_city
	address_state = doc.state or doc.custom_province
	if address_city:
		doc.city = address_city
		doc.state = address_state
		address = frappe.new_doc("Address")
		address.address_type = "Billing"
		address.address_line1 = doc.address_line_1
		address.address_line2 = doc.address_line_2
		address.city = address_city
		address.state = address_state
		address.country = doc.country or "Fiji"
		address.pincode = doc.zip_code
		address.append("links", {"link_doctype": "Customer", "link_name": customer.name})
		address.save(ignore_permissions=True)
		customer.customer_primary_address = address.name
	customer.customer_primary_contact = contact.name
	customer.save(ignore_permissions=True)
	doc.applicant = customer.name


@as_server_script
def prepare_loan_amount(doc, method=None):
	"""Was server script "Flexi Prepare Loan Amount" (Before Validate)."""
	# Web Forms can serialise a selected rate as 22.000000000, while the Select field stores 22.
	raw_interest_rate = str(doc.get("custom_flexi_interest_rate") or "").strip()
	interest_rate_number = flt(raw_interest_rate)
	if interest_rate_number:
		canonical = str(int(interest_rate_number)) if interest_rate_number == int(interest_rate_number) \
			else str(interest_rate_number)
		if canonical in CANONICAL_RATES:
			doc.custom_flexi_interest_rate = canonical
			doc.rate_of_interest = interest_rate_number

	purchase_value = 0
	for row in doc.get("custom_flexi_purchase_items") or []:
		purchase_value += flt(row.quantity) * flt(row.unit_price)
	purchase_value = purchase_value or flt(doc.custom_flexi_cash_price)
	if purchase_value:
		surcharge_rate = max(flt(doc.custom_flexi_surcharge_rate), 0)
		surcharge_amount = round(purchase_value * surcharge_rate / 100, 2)
		doc.custom_flexi_surcharge_amount = surcharge_amount
		doc.custom_flexi_full_value = round(purchase_value + surcharge_amount, 2)


@as_server_script
def default_customer_agreements(doc, method=None):
	"""Was server script "Flexi Default Customer Agreements" (Before Save = validate)."""
	templates = frappe.get_all("Loan Document Template",
		filters={"status": "Current", "document_code": ("!=", "REQ")},
		fields=["name", "document_code", "version", "sequence"], order_by="sequence asc")
	existing_templates = []
	for row in doc.custom_customer_documents or []:
		if row.document_template:
			existing_templates.append(row.document_template)
			if not row.document_code:
				row.document_code = frappe.db.get_value("Loan Document Template", row.document_template, "document_code")
	for template in templates:
		if template.name not in existing_templates:
			row = doc.append("custom_customer_documents", {})
			row.document_template = template.name
			row.document_code = template.document_code
			row.version = template.version or 1
			row.status = "Pending"
	if not doc.custom_flexi_hp_account_number and not doc.is_new():
		doc.custom_flexi_hp_account_number = doc.name


@as_server_script
def status_display(doc, method=None):
	"""Was server script "Loan Status Updation" (Before Save = validate)."""
	doc.custom_loan_status_display = doc.status or ""


@as_server_script
def link_documents(doc, method=None):
	"""Was server script "link documents to loan application" (After Save = on_update)."""
	for row in doc.get("documents") or []:
		if not row.file:
			continue
		files = frappe.get_all("File", filters={"file_url": row.file},
			fields=["name", "attached_to_doctype", "attached_to_name"])
		if not files:
			frappe.get_doc({
				"doctype": "File",
				"file_url": row.file,
				"is_private": 1 if row.file.startswith("/private/") else 0,
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
			}).insert(ignore_permissions=True)
			continue
		for f in files:
			if f.attached_to_name:
				continue
			frappe.db.set_value("File", f.name, {"attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
				update_modified=False)


def application_transition(doc, transition=None):
	"""Was server script "Flexi Loan Application Workflow Task" (Workflow Task on the Loan Application
	workflow transitions). Now the app workflow method "Flexi Loan Application Task"."""
	if doc.workflow_state == "Approved - Contract Preparation":
		doc.status = "Approved"
		doc.custom_loan_status_display = "Approved"
	elif doc.workflow_state == "Loan Activated":
		if doc.status != "Approved":
			frappe.throw("The Loan Application must be Approved before the loan can be activated.")
		if not frappe.db.get_value("Loan", {"loan_application": doc.name}, "name"):
			from lending.loan_management.doctype.loan_application.loan_application import create_loan
			loan = create_loan(source_name=doc.name, submit=0)
			loan.repayment_periods = int(flt(doc.custom_flexi_term_months or doc.repayment_periods or 12))
			# Gross HP: amount, 0% rate and terms are applied by comfort_homes.loan_mapping.
			loan.repayment_schedule_type = "Monthly as per repayment start date"
			loan.insert()


# ---------------------------------------------------------------------------
# ToDo
# ---------------------------------------------------------------------------

@as_server_script
def sync_prepared_by(doc, method=None):
	"""Was server script "Flexi Sync Prepared By From Assignment" (ToDo, After Save = on_update)."""
	if doc.reference_type == "Loan Application" and doc.reference_name \
			and frappe.db.exists("Loan Application", doc.reference_name):
		active = frappe.get_all("ToDo",
			filters={"reference_type": "Loan Application", "reference_name": doc.reference_name, "status": "Open"},
			fields=["allocated_to"], order_by="creation desc", limit_page_length=1)
		prepared_by = active[0].get("allocated_to") if active else None
		frappe.db.set_value("Loan Application", doc.reference_name, "custom_flexi_prepared_by", prepared_by)


# ---------------------------------------------------------------------------
# Public web form API
# ---------------------------------------------------------------------------

@frappe.whitelist(allow_guest=True, methods=["GET", "POST"])
def get_loan_products():
	"""Was API server script "Dynamic Laon P" (guest, `get_loan_products`): enabled Flexi loan products and
	their rate, for the public application form. Returns only name and rate, as before."""
	from comfort_homes.hp_lending import hp_settings as s
	return frappe.get_all("Loan Product", filters={"disabled": 0, "company": s.COMPANY},
		fields=["name", "rate_of_interest"], order_by="name asc", ignore_permissions=True)
