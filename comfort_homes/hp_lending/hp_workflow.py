"""Native-document handoffs and server-side financial progression gates.

No automatic Loan/disbursement posting, no fabricated workflow or GL status.
"""
import frappe
from frappe.utils import flt
from comfort_homes.hp_lending import hp_settings as s

LPO_STATES = {'LPO Pending Approval', 'LPO Approved', 'LPO Sent to Supplier',
              'Delivered - Awaiting Documents', 'Active Loan Account', 'Closed'}
APPROVED_PO_STATES = LPO_STATES - {'LPO Pending Approval'}


def _one_loan(application):
    rows = frappe.get_all('Loan', filters={'loan_application': application.name,
        'company': application.company, 'docstatus': ['!=', 2]}, fields=['name'])
    if len(rows) != 1:
        frappe.throw('Exactly one linked native Loan is required; review Financial Handoff')
    loan = frappe.get_doc('Loan', rows[0].name)
    if loan.applicant_type != 'Customer' or loan.applicant != application.applicant:
        frappe.throw('The linked Loan customer does not match this application')
    return loan


def _require_disbursed(application):
    loan = _one_loan(application)
    amount = flt(application.custom_flexi_total_receivable, 2)
    if (loan.docstatus != 1 or loan.rate_of_interest != 0 or amount <= 0
            or flt(loan.loan_amount, 2) != amount or flt(loan.disbursed_amount, 2) != amount):
        frappe.throw('Submit the linked native Loan and full 0% HP disbursement before generating or progressing the LPO. Loan Activated alone is not proof of disbursement.')
    disbursements = frappe.get_all('Loan Disbursement', filters={
        'against_loan': loan.name, 'docstatus': 1}, fields=['name', 'disbursed_amount'])
    if len(disbursements) != 1 or flt(disbursements[0].disbursed_amount, 2) != amount:
        frappe.throw('One submitted whole-balance Loan Disbursement is required')
    if not frappe.db.exists('Journal Entry', {'custom_hp_loan': loan.name,
            'custom_hp_entry_type': 'Gross Booking Reclass', 'docstatus': 1}):
        frappe.throw('The submitted gross-booking journal is missing; stop and reconcile')
    return loan


def _validate_po(po, app):
    if (po.company != app.company or po.supplier != s.CHD_SUPPLIER
            or po.get('custom_loan_application') != app.name or po.currency != 'FJD'
            or flt(po.grand_total, 2) != flt(app.custom_flexi_cash_price, 2)):
        frappe.throw('The LPO must match this application, CHD, FJD and approved cash price')


def _purchase_order(application):
    rows = frappe.get_all('Purchase Order', filters={'custom_loan_application': application.name,
        'company': application.company, 'docstatus': ['!=', 2]}, fields=['name'])
    if len(rows) != 1:
        frappe.throw('Exactly one linked CHD Purchase Order is required; review Connections')
    po = frappe.get_doc('Purchase Order', rows[0].name)
    _validate_po(po, application)
    return po


def _require_invoice(app):
    rows = frappe.get_all('Purchase Invoice', filters={'custom_loan_application': app.name,
        'company': app.company, 'supplier': s.CHD_SUPPLIER, 'docstatus': 1, 'is_return': 0},
        fields=['name', 'grand_total'])
    if not rows or flt(sum(flt(r.grand_total) for r in rows), 2) != flt(app.custom_flexi_cash_price, 2):
        frappe.throw('Submitted CHD invoices matching the cash price are required before Active Loan Account')
    for row in rows:
        invoice = frappe.get_doc('Purchase Invoice', row.name)
        if (invoice.currency != 'FJD' or any(flt(t.get('tax_amount')) for t in invoice.get('taxes') or [])
                or invoice.credit_to != s.account('ACC_CHD_PAYABLE', app.company)
                or not invoice.get('items') or any(item.expense_account != s.account('ACC_CLEARING', app.company)
                                                  for item in invoice.get('items'))):
            frappe.throw('CHD invoices must clear 1180 to 2111 in FJD with no input VAT')


def validate_progress(doc, method=None):
    """Enforce transitions including update-after-submit; do not reset legacy states."""
    if doc.company != s.COMPANY or doc.get('custom_hp_converted'):
        return  # dev14: a contract converted from MYOB has no origination/LPO stages to enforce
    old = doc.get_doc_before_save()
    if not old or old.get('workflow_state') == doc.get('workflow_state'):
        return
    target = doc.get('workflow_state')
    if target not in LPO_STATES:
        return
    loan = _require_disbursed(doc)
    if target in APPROVED_PO_STATES and _purchase_order(doc).docstatus != 1:
        frappe.throw('Submit the approved linked Purchase Order before progressing the LPO')
    if target == 'Delivered - Awaiting Documents' and not doc.get('custom_hp_delivery_docket'):
        frappe.throw('Attach the supplier delivery docket before confirming delivery')
    if target in ('Active Loan Account', 'Closed'):
        _require_invoice(doc)
    if target == 'Closed' and loan.status != 'Closed':
        frappe.throw('Close the native Loan through Lending before closing the application')


def require_deposit_before_contract(doc, method=None):
    """The deposit is part of the agreement: it must be received (submitted Payment Entry) before the customer
    documents are generated, i.e. on Approved -> Contract Signing. hp_lifecycle keeps its own later check at
    compliance sign-off. Reuses that module's deposit validation; nothing financial is changed here."""
    if doc.company != s.COMPANY:
        return
    old = doc.get_doc_before_save()
    if not old or old.get('workflow_state') != 'Approved' or doc.get('workflow_state') != 'Contract Signing':
        return
    from comfort_homes.hp_lending.hp_lifecycle import check_deposit
    check_deposit(doc)


def submit_approved_lpo(doc):
    """Idempotent native task on BOTH Approve LPO transitions; no permission bypass."""
    frappe.only_for(('Finance Manager', 'Director'))
    if (doc.company != s.COMPANY or doc.docstatus != 1
            or doc.workflow_state != 'LPO Approved' or doc.status != 'Approved'):
        frappe.throw('Use the approved application Approve LPO workflow action')
    frappe.db.sql('SELECT name FROM `tabLoan Application` WHERE name = %s FOR UPDATE', (doc.name,))
    _require_disbursed(doc)
    po = _purchase_order(doc)
    frappe.db.sql('SELECT name FROM `tabPurchase Order` WHERE name = %s FOR UPDATE', (po.name,))
    po.reload()
    _validate_po(po, doc)
    po.check_permission('read')
    if po.docstatus == 1:
        return
    if po.docstatus != 0:
        frappe.throw('Only the intended draft LPO can be approved')
    po.check_permission('submit')
    po.submit()


def _visible_docs(doctype, filters, fields):
    rows = frappe.get_all(doctype, filters=filters, fields=['name'] + fields)
    return [dict(row) for row in rows if frappe.has_permission(doctype, 'read', doc=row.name)]


@frappe.whitelist()
def financial_handoff(loan_application):
    """Read-only actual native status, filtered by per-document read permission."""
    app = frappe.get_doc('Loan Application', loan_application)
    app.check_permission('read')
    if app.company != s.COMPANY:
        frappe.throw('Financial Handoff is restricted to Flexi applications')
    loans = _visible_docs('Loan', {'loan_application': app.name, 'company': app.company,
        'docstatus': ['!=', 2]}, ['docstatus', 'status', 'loan_amount', 'disbursed_amount', 'rate_of_interest'])
    disbursements = []
    for loan in loans:
        disbursements.extend(_visible_docs('Loan Disbursement', {'against_loan': loan['name'],
            'docstatus': ['!=', 2]}, ['docstatus', 'status', 'disbursed_amount']))
    deposit = None
    if app.get('custom_hp_deposit_payment_entry'):
        pe = app.custom_hp_deposit_payment_entry
        if frappe.has_permission('Payment Entry', 'read', doc=pe):
            deposit = frappe.db.get_value('Payment Entry', pe,
                ['name', 'docstatus', 'paid_amount'], as_dict=True)
    return dict(deposit=dict(deposit) if deposit else None, loans=loans,
        disbursements=disbursements, purchase_orders=_visible_docs('Purchase Order',
            {'custom_loan_application': app.name, 'company': app.company, 'docstatus': ['!=', 2]},
            ['docstatus', 'status', 'grand_total']),
        warning='Workflow labels do not certify posted GL. Submit native documents and reconcile.')


def generate_lpo(doc):
    """One native draft PO under application lock, called only by Generate LPO transition."""
    from frappe.utils import today
    frappe.only_for("Disbursement Officer")
    if doc.company!=s.COMPANY or doc.docstatus!=1 or doc.workflow_state!="LPO Pending Approval":
        frappe.throw("Use the application Generate LPO workflow action")
    frappe.db.sql("SELECT name FROM `tabLoan Application` WHERE name=%s FOR UPDATE",(doc.name,))
    _require_disbursed(doc)
    existing=frappe.get_all("Purchase Order",filters={"custom_loan_application":doc.name,"company":doc.company,"docstatus":["!=",2]},fields=["name"])
    if existing:
        if len(existing)!=1: frappe.throw("Multiple LPOs exist; stop and review")
        po=frappe.get_doc("Purchase Order",existing[0].name);po.check_permission("read");_validate_po(po,doc)
        return po.name
    # A legacy remarks-only PO must be reviewed, not silently relinked or duplicated.
    import re
    legacy=frappe.get_all("Purchase Order",filters={"company":doc.company,"docstatus":["!=",2],"remarks":["like","%Loan Application:%"+doc.name+"%"]},fields=["name","remarks"])
    for row in legacy:
        match=re.search(r"Loan Application:\s*([^|\n]+)",row.remarks or "")
        if match and match.group(1).strip()==doc.name:
            frappe.throw("A legacy LPO already references this application; review links before generating another")
    po=frappe.get_doc(dict(doctype="Purchase Order",company=doc.company,supplier=s.CHD_SUPPLIER,currency="FJD",
        transaction_date=today(),schedule_date=today(),custom_loan_application=doc.name,
        customer=doc.applicant,remarks="Loan Application: "+doc.name))
    po.check_permission("create")
    for item in doc.get("custom_flexi_purchase_items") or []:
        if not str(item.get("product_description") or "").strip() or flt(item.get("quantity"))<=0 or flt(item.get("unit_price"))<0:
            frappe.throw("Complete each approved purchase row before generating LPO")
        po.append("items",dict(item_code=s.HP_ITEM_CODE,description=item.product_description,qty=item.quantity,rate=item.unit_price,uom="Unit",schedule_date=today()))
    if not po.get("items"): frappe.throw("Approved purchase items are missing")
    po.insert()  # native validation and native create permission, never ignore_permissions
    _validate_po(po,doc)
    return po.name
