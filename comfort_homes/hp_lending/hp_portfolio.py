"""Current-state native-document overview. Read-only, permission filtered, no status engine."""
import frappe
from frappe.utils import flt, getdate, today
from comfort_homes.hp_lending import hp_settings as s


def _rows(doctype, filters, fields, or_filters=None):
    if not frappe.has_permission(doctype,"read"): return None
    args=dict(filters=filters,fields=fields,limit_page_length=0)
    if or_filters: args["or_filters"]=or_filters
    return frappe.get_list(doctype,**args)


def fee_position(loan_name):
    """Invoice debt once, never demands + invoices. Native outstanding includes actual allocations."""
    rows=_rows("Sales Invoice",{"company":s.COMPANY,"docstatus":1},
        ["name","loan","custom_hp_independent_fee","custom_hp_reference_loan","outstanding_amount","value_date","posting_date","is_return"],
        {"custom_hp_reference_loan":loan_name,"loan":loan_name})
    if rows is None: return dict(independent_fees=None,legacy_fee_exceptions=None,invoices=None)
    independent=sum(flt(r.outstanding_amount) for r in rows if r.custom_hp_independent_fee and r.custom_hp_reference_loan==loan_name and not r.loan)
    legacy=sum(flt(r.outstanding_amount) for r in rows if r.loan==loan_name)
    return dict(independent_fees=round(independent,2),legacy_fee_exceptions=round(legacy,2),invoices=rows)


CHD_INVOICE_DAYS=14
LPO_EXPECTED=("LPO Approved","LPO Sent to Supplier","Delivered - Awaiting Documents","Active Loan Account")


def chd_invoice_age(orders, on=None):
    """Days since the oldest submitted LPO, or None when no LPO has been submitted."""
    dates=[getdate(r.get("transaction_date")) for r in orders if r.docstatus==1 and r.get("transaction_date")]
    return (getdate(on or today())-min(dates)).days if dates else None


def portfolio(filters=None):
    filters=filters or {};query={"company":s.COMPANY,"docstatus":["!=",2]}
    if filters.get("loan"):query["name"]=filters["loan"]
    if filters.get("branch"):query["cost_center"]=filters["branch"]
    loans=_rows("Loan",query,["name","loan_application","status","docstatus","loan_amount","disbursed_amount","total_payment","total_principal_paid","debit_adjustment_amount","credit_adjustment_amount","days_past_due","is_npa","cost_center","custom_hp_contract_rate"])
    if loans is None: frappe.throw("Loan read permission is required",frappe.PermissionError)
    result=[];seen_apps=set()
    for loan in loans:
        seen_apps.add(loan.loan_application)
        balance=round(flt(loan.total_payment)-flt(loan.total_principal_paid)+flt(loan.debit_adjustment_amount)-flt(loan.credit_adjustment_amount),2) if loan.docstatus==1 else None
        fees=fee_position(loan.name)
        app=frappe.get_doc("Loan Application",loan.loan_application) if loan.loan_application and frappe.has_permission("Loan Application","read",doc=loan.loan_application) else None
        orders=_rows("Purchase Order",{"custom_loan_application":loan.loan_application,"company":s.COMPANY,"docstatus":["!=",2]},["name","docstatus","status","grand_total","per_billed","per_received","transaction_date"]) if app else None
        invoices=_rows("Purchase Invoice",{"custom_loan_application":loan.loan_application,"company":s.COMPANY,"docstatus":1},["name","grand_total","outstanding_amount","due_date"]) if app else None
        exceptions=[]
        # dev27 (F18): converted MYOB contracts have no LPO or CHD invoice by design.  For new contracts an
        # LPO is expected once it is approved, and a CHD invoice within CHD_INVOICE_DAYS of the LPO.
        converted=bool(app and app.get("custom_hp_converted"))
        if not converted and orders is not None and not orders and app and app.workflow_state in LPO_EXPECTED:
            exceptions.append("Missing LPO")
        if not converted and invoices is not None and not invoices and orders:
            age=chd_invoice_age(orders)
            if age is not None and age>CHD_INVOICE_DAYS: exceptions.append(f"CHD invoice overdue - {age} days")
        if fees["legacy_fee_exceptions"]: exceptions.append("Legacy native-linked fee debt; do not correct")
        if app and app.workflow_state=="LPO Pending Approval" and orders and any(r.docstatus==1 for r in orders): exceptions.append("PO submitted/application pending approval")
        if app and app.workflow_state=="Active Loan Account" and (loan.docstatus!=1 or not flt(loan.disbursed_amount)):exceptions.append("Active application/not financially disbursed")
        result.append(dict(loan=loan.name,application=loan.loan_application,branch=loan.cost_center,
            workflow=app.workflow_state if app else "Not accessible",loan_status=loan.status,contract_flat_rate=loan.custom_hp_contract_rate,
            loan_balance=balance,own_dpd=loan.days_past_due,borrower_npa=loan.is_npa,
            independent_fees=fees["independent_fees"],legacy_fees=fees["legacy_fee_exceptions"],
            customer_position=None if balance is None or fees["independent_fees"] is None else round(balance+fees["independent_fees"]+fees["legacy_fee_exceptions"],2),
            po_status="; ".join(r.name+": "+("Draft" if r.docstatus==0 else r.status) for r in orders) if orders is not None else "Not accessible",
            supplier_outstanding=round(sum(flt(r.outstanding_amount) for r in invoices),2) if invoices is not None else None,
            delivery="Not independently evidenced in this report",chd_terms="Statement cycle not configured/verified",
            exceptions="; ".join(exceptions)))
    # Include applications without a Loan, not only procurement-backed cases.
    app_filters={"company":s.COMPANY,"docstatus":["!=",2]}
    if filters.get("branch"):app_filters["custom_branchlocation"]=filters["branch"]
    if not filters.get("loan"):
        apps=_rows("Loan Application",app_filters,["name","workflow_state","custom_branchlocation"])
        for app in apps or []:
            if app.name not in seen_apps:result.append(dict(application=app.name,workflow=app.workflow_state,branch=app.custom_branchlocation,exceptions="No visible linked Loan; review native documents"))
    return result
