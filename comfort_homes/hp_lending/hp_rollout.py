"""Explicit create-missing rollout. Never called by install/migrate or financial jobs."""
from copy import deepcopy
import frappe
from frappe.utils import cint
from comfort_homes.hp_lending import hp_settings as s

DOCUMENT_TYPES = ("Identity Document", "Latest Payslip", "Bank Statement", "Proof of Address", "Supplier Quotation", "Other Supporting Document")
FEE_RATES = {"SMS Charge": (2.50, "4011"), "Call Charge": (2.50, "4011"), "Field Visit": (25.00, "4008")}
TAX_TITLE = "Flexi Servicing Fees VAT 12.5% Inclusive"


def account(number):
    name = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": number, "is_group": 0, "disabled": 0}, "name")
    if not name:
        frappe.throw("Required enabled account is missing: " + number)
    return name


def required_documents():
    return [dict(doctype="Loan Document Type", loan_document_type=name) for name in DOCUMENT_TYPES]


def catalogue():
    documents = []
    for code, (rate, number) in FEE_RATES.items():
        documents.append(dict(doctype="Item", item_code=code, item_name=code, item_group="Services", stock_uom="Unit",
            is_stock_item=0, is_sales_item=1, is_purchase_item=0, disabled=0,
            item_defaults=[dict(company=s.COMPANY, income_account=account(number))]))
        documents.append(dict(doctype="Item Price", item_code=code, price_list="Standard Selling", currency="FJD", price_list_rate=rate, uom="Unit", selling=1))
    documents.append(dict(doctype="Sales Taxes and Charges Template", title=TAX_TITLE, company=s.COMPANY, is_default=0, disabled=0,
        taxes=[dict(charge_type="On Net Total", account_head=account("2310"), rate=12.5, included_in_print_rate=1, description="VAT 12.5% inclusive")]))
    return documents


def _key(doc):
    dt = doc["doctype"]
    fields = {"Loan Document Type": ("loan_document_type",), "Item": ("item_code",),
        "Item Price": ("item_code", "price_list", "currency", "uom"),
        "Sales Taxes and Charges Template": ("title", "company")}[dt]
    return {key:doc[key] for key in fields}


def _mismatches(actual, expected, prefix=""):
    differences = []
    for field, value in expected.items():
        if field == "doctype": continue
        before = actual.get(field)
        if isinstance(value, list):
            # Compare required row values without native bookkeeping fields.
            old = before or []
            if field == "item_defaults": old = [r for r in old if r.get("company") == s.COMPANY]
            if len(old) != len(value):
                differences.append(dict(field=prefix+field, before=len(old), after=len(value)))
            else:
                for i, row in enumerate(value): differences += _mismatches(old[i], row, prefix+field+f"[{i}].")
        elif before != value:
            differences.append(dict(field=prefix+field, before=before, after=value))
    return differences


def preview(include_fees=False, include_company=False):
    plans=[]
    for expected in required_documents() + (catalogue() if include_fees else []):
        names=frappe.get_all(expected["doctype"], filters=_key(expected), pluck="name", limit_page_length=0)
        if not names:
            plans.append(dict(action="create", doctype=expected["doctype"], key=_key(expected), values=expected))
        elif len(names) != 1:
            plans.append(dict(action="conflict", doctype=expected["doctype"], key=_key(expected), reason="Multiple matching native masters"))
        else:
            actual=frappe.get_doc(expected["doctype"], names[0])
            differences=_mismatches(actual, expected)
            if differences: plans.append(dict(action="conflict", doctype=expected["doctype"], name=names[0], differences=differences))
    if include_company:
        company=frappe.get_doc("Company", s.COMPANY)
        for field in ("enable_perpetual_inventory", "enable_provisional_accounting_for_non_stock_items"):
            if company.get(field):
                plans.append(dict(action="update", doctype="Company", name=s.COMPANY, field=field, before=company.get(field), after=0))
        if any(p["action"]=="update" for p in plans) and frappe.db.exists("Stock Ledger Entry", {"company":s.COMPANY}):
            plans.append(dict(action="conflict", doctype="Company", name=s.COMPANY, reason="Stock ledger exists: inventory settings must be reviewed, never automatically disabled"))
    return plans


@frappe.whitelist(methods=["POST"])
def setup_rollout(dry_run=1, include_fees=0, include_company=0):
    """Preview first. Apply only an exact separately approved batch; do not overwrite conflicts."""
    frappe.only_for("System Manager")
    plans=preview(bool(cint(include_fees)), bool(cint(include_company)))
    if cint(dry_run): return dict(dry_run=1, changes=plans, financial_acceptance=False)
    # Serialize approval recheck and master creation. No explicit commit; native request transaction owns it.
    frappe.db.sql("SELECT name FROM `tabCompany` WHERE name=%s FOR UPDATE", (s.COMPANY,))
    plans=preview(bool(cint(include_fees)), bool(cint(include_company)))
    if any(p["action"]=="conflict" for p in plans): frappe.throw("Rollout conflicts require review; nothing was applied")
    for p in plans:
        if p["action"]=="create": frappe.get_doc(deepcopy(p["values"])).insert()
    updates={p["field"]:p["after"] for p in plans if p["action"]=="update"}
    if updates:
        company=frappe.get_doc("Company", s.COMPANY)
        for field,value in updates.items(): company.set(field,value)
        company.save()
    return dict(dry_run=0, changes=plans, remaining=preview(bool(cint(include_fees)), bool(cint(include_company))), financial_acceptance=False)


def diagnostics():
    problems=[]
    for name in DOCUMENT_TYPES:
        if not frappe.db.exists("Loan Document Type", name): problems.append("Missing upload document type: " + name)
    company=frappe.get_doc("Company",s.COMPANY)
    for field in ("enable_perpetual_inventory", "enable_provisional_accounting_for_non_stock_items"):
        if company.get(field): problems.append("Non-inventory HP requires Company " + field + " off; preview guarded rollout")
    if company.get("custom_hp_independent_fees_enabled"):
        for change in preview(include_fees=True):
            problems.append("Independent fee setup requires review: " + change["doctype"] + " " + change["action"])
    if not frappe.db.exists("Workflow","Loan Application Workflow-1"):
        return problems
    workflow=frappe.get_doc("Workflow","Loan Application Workflow-1")
    approvals=[r for r in workflow.transitions if r.action=="Approve LPO"]
    if {r.allowed for r in approvals}!={"Finance Manager","Director"} or len(approvals)!=2 or any(r.transition_tasks!="Flexi Approve LPO" for r in approvals):
        problems.append("Both native Approve LPO role transitions must bind Flexi Approve LPO")
    return problems
