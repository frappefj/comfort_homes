"""Quarterly statements issued, with email status, for the team to print and send individually (read-only)."""
import frappe

ALLOWED_ROLES = ("Director", "Finance Manager", "Accounts Manager", "Accounts Officer", "Loan Officer")


def execute(filters=None):
    if not set(ALLOWED_ROLES) & set(frappe.get_roles()) and frappe.session.user != "Administrator":
        frappe.throw("HP Statement Register is restricted.", frappe.PermissionError)
    filters = dict(filters or {})
    period = (filters.get("period") or "").strip()
    pattern = f"Statement-%-{period}.pdf" if period else "Statement-%-____-Q_.pdf"
    files = frappe.get_all("File", filters={"attached_to_doctype": "Loan", "file_name": ["like", pattern]},
        fields=["name", "file_name", "file_url", "attached_to_name", "creation"], order_by="file_name asc", limit_page_length=0)
    rows = []
    for f in files:
        loan = frappe.db.get_value("Loan", f.attached_to_name, ["applicant", "applicant_name", "cost_center"], as_dict=True) or {}
        label = f.file_name.rsplit("-", 2)[-2] + "-" + f.file_name.rsplit("-", 1)[-1].replace(".pdf", "")
        mail = frappe.db.get_value("Communication", {"reference_doctype": "Loan", "reference_name": f.attached_to_name,
            "subject": ["like", f"%{label}%"]}, ["recipients", "delivery_status"], as_dict=True)
        email = frappe.db.get_value("Customer", loan.get("applicant"), "email_id") if loan.get("applicant") else None
        if filters.get("needs_print") and mail:
            continue
        rows.append(dict(loan=f.attached_to_name, customer=loan.get("applicant_name") or loan.get("applicant"),
            branch=loan.get("cost_center"), period=label, statement=f.file_url, issued=f.creation,
            email=(mail.recipients if mail else email) or "", email_status=(mail.delivery_status or "Queued") if mail else ("No email address" if not email else "Not sent")))
    columns = [
        dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=150),
        dict(fieldname="customer", label="Customer", fieldtype="Data", width=200),
        dict(fieldname="branch", label="Branch", fieldtype="Data", width=120),
        dict(fieldname="period", label="Period", fieldtype="Data", width=90),
        dict(fieldname="statement", label="Statement PDF", fieldtype="Data", width=260),
        dict(fieldname="email", label="Email", fieldtype="Data", width=200),
        dict(fieldname="email_status", label="Email Status", fieldtype="Data", width=130),
        dict(fieldname="issued", label="Issued", fieldtype="Datetime", width=150),
    ]
    return columns, rows
