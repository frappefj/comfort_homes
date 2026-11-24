"""Independent servicing debts on native Sales Invoice / Payment Entry.

No Loan update, Loan Demand, Loan Repayment or balance adjustment is created here.
Disabled by default pending a separately approved devcom prototype and billing policy.
Existing submitted legacy invoices are neither repaired nor relinked.
"""
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
import frappe
from frappe.utils import cint, getdate
from comfort_homes.hp_lending import hp_settings as s
from comfort_homes.hp_lending.hp_rollout import FEE_RATES, PER_UNIT_FEES, TAX_TITLE, account

TRIGGERS = ("loan", "loan_disbursement", "loan_repayment", "loan_partner", "against_voucher", "against_voucher_type")


def money(value):
    try:
        result=Decimal(str(value or 0))
        if not result.is_finite(): raise InvalidOperation
        return result.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        frappe.throw("Fee amounts must be finite numbers")


def is_fee(doc):
    return doc.company==s.COMPANY and (cint(doc.get("custom_hp_independent_fee")) or any(r.item_code in FEE_RATES for r in doc.get("items") or []))


def _enabled():
    if not cint(frappe.db.get_value("Company",s.COMPANY,"custom_hp_independent_fees_enabled")):
        frappe.throw("Independent fee billing is disabled pending approved policy and native isolation acceptance")


def _reference(doc):
    name=doc.get("custom_hp_reference_loan")
    if not name: frappe.throw("Select the reference-only Loan; do not use the native Sales Invoice Loan field")
    loan=frappe.get_doc("Loan",name)
    loan.check_permission("read")
    if loan.company!=doc.company or loan.applicant_type!="Customer" or loan.applicant!=doc.customer:
        frappe.throw("Fee reference must match the invoice Company and Customer")
    if not loan.loan_application or loan.loan_application!=doc.get("custom_hp_reference_application"):
        frappe.throw("Fee application must match the referenced Loan")
    return loan


def _units(item):
    """1 for event fees; a whole number of pages (1 to the limit) for per-unit fees such as Statement Print."""
    limit=PER_UNIT_FEES.get(item.item_code)
    if not limit: return 1
    qty=abs(money(item.qty))
    if qty!=qty.to_integral_value() or not 1<=qty<=limit: frappe.throw(f"{item.item_code} quantity must be a whole number of pages from 1 to {limit}")
    return int(qty)


def _rules(doc):
    if any(doc.get(f) for f in TRIGGERS): frappe.throw("Independent fees must not use native Loan, partner or repayment trigger fields")
    if not cint(doc.get("custom_hp_independent_fee")): frappe.throw("Use the independent fee invoice path for these servicing items")
    if len(doc.get("items") or [])!=1: frappe.throw("Use one reviewed service event and one fee line per invoice initially")
    item=doc.get("items")[0]
    if item.item_code not in FEE_RATES: frappe.throw("Only approved independent servicing fee items are allowed")
    rate,_=FEE_RATES[item.item_code]
    sign=-1 if cint(doc.get("is_return")) else 1
    units=_units(item)
    if money(item.qty)!=money(sign*units) or money(item.rate)!=money(rate): frappe.throw("Fee quantity/rate must match the approved VAT-inclusive event")
    if doc.currency!="FJD" or money(doc.get("conversion_rate") or 1)!=Decimal("1.00"): frappe.throw("Independent fees must be in FJD at exchange rate 1")
    if doc.get("update_stock") or doc.get("is_pos") or doc.get("is_opening")=="Yes" or money(doc.get("discount_amount")) or money(doc.get("additional_discount_percentage")):
        frappe.throw("Stock/POS/opening/discount processing is not part of independent fees")
    if doc.get("disable_rounded_total")!=1: frappe.throw("Independent fee invoices must retain exact cents without invoice rounding")
    if not doc.get("posting_date") or not doc.get("value_date") or getdate(doc.value_date)!=getdate(doc.posting_date): frappe.throw("Value Date must equal Invoice Date")
    if not str(doc.get("custom_hp_fee_event") or "").strip() or not str(doc.get("custom_hp_fee_evidence") or "").strip(): frappe.throw("Record a unique service event and evidence; arrears alone is not authority to bill")
    if not doc.get("custom_hp_fee_event_date") or getdate(doc.custom_hp_fee_event_date)>getdate(doc.posting_date): frappe.throw("Record a truthful event date no later than Invoice Date")
    _reference(doc)


def defaults(doc, method=None):
    if not is_fee(doc): return
    _enabled()
    # Never silently clear native trigger fields or relink a legacy invoice.
    if any(doc.get(f) for f in TRIGGERS): frappe.throw("Office fees are separate debts; do not populate native Loan trigger fields")
    if not cint(doc.get("custom_hp_independent_fee")): frappe.throw("Prepare an independent fee invoice; existing legacy invoices are preserved")
    if not doc.get("value_date"): doc.value_date=doc.posting_date
    doc.debit_to=account("1120") if not doc.get("debit_to") else doc.debit_to
    if not doc.get("taxes_and_charges"):
        template=frappe.db.get_value("Sales Taxes and Charges Template",{"title":TAX_TITLE,"company":s.COMPANY,"disabled":0},"name")
        if not template: frappe.throw("Configure the approved inclusive fee tax template before billing")
        doc.taxes_and_charges=template
    if not doc.get("taxes"):
        tax_doc=frappe.get_doc("Sales Taxes and Charges Template",doc.taxes_and_charges)
        if tax_doc.company!=doc.company: frappe.throw("Fee tax template must belong to this Company")
        for tax in tax_doc.taxes:
            doc.append("taxes",{k:tax.get(k) for k in ("charge_type","account_head","rate","included_in_print_rate","description")})
    _rules(doc)


def validate(doc, method=None):
    if not is_fee(doc): return
    _enabled(); _rules(doc)
    item=doc.get("items")[0]; rate,number=FEE_RATES[item.item_code]
    if doc.debit_to!=account("1120") or item.income_account!=account(number): frappe.throw("Independent fee accounting must use 1120 and the approved fee income account")
    taxes=doc.get("taxes") or []
    if len(taxes)!=1 or taxes[0].charge_type!="On Net Total" or taxes[0].account_head!=account("2310") or money(taxes[0].rate)!=Decimal("12.50") or not cint(taxes[0].included_in_print_rate):
        frappe.throw("Apply exactly the approved 12.5% inclusive output VAT row")
    gross=money(rate)*_units(item)*(-1 if cint(doc.get("is_return")) else 1)
    net=(gross/Decimal("1.125")).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    if money(doc.grand_total)!=gross or money(doc.net_total)!=net or money(doc.total_taxes_and_charges)!=gross-net:
        frappe.throw("Fee totals do not match the approved inclusive rate and VAT cents")
    # Both draft and submitted event references reserve identity. A cancelled event is not silently rebilled.
    frappe.db.sql("SELECT name FROM `tabCustomer` WHERE name=%s FOR UPDATE",(doc.customer,))
    duplicate=frappe.db.exists("Sales Invoice",{"custom_hp_fee_event":doc.custom_hp_fee_event,"name":["!=",doc.name]})
    if duplicate: frappe.throw("This service event already has a fee invoice; inspect it instead of retrying")
    if cint(doc.get("is_return")):
        original=frappe.get_doc("Sales Invoice",doc.return_against)
        original.check_permission("read")
        if not cint(original.get("custom_hp_independent_fee")) or original.docstatus!=1 or original.customer!=doc.customer or original.get("custom_hp_reference_loan")!=doc.custom_hp_reference_loan or original.get("items")[0].item_code!=item.item_code:
            frappe.throw("Credit only the matching independent fee invoice; never use this route to correct legacy fees")


def before_submit(doc, method=None):
    if not is_fee(doc): return
    frappe.only_for(("Finance Manager","Director"))
    if not cint(doc.get("custom_hp_fee_authorised")): frappe.throw("Authorised reviewer must attest event chargeability under the agreed billing policy")
    validate(doc)


@frappe.whitelist()
def draft_values(loan, fee_item):
    """Read-only native draft defaults. Does not insert an invoice or send communication."""
    _enabled()
    if fee_item not in FEE_RATES: frappe.throw("Unsupported fee item")
    reference=frappe.get_doc("Loan",loan);reference.check_permission("read")
    if not frappe.has_permission("Sales Invoice","create"): frappe.throw("Not permitted",frappe.PermissionError)
    if reference.company!=s.COMPANY or reference.applicant_type!="Customer": frappe.throw("Select a Flexi customer Loan")
    rate,number=FEE_RATES[fee_item]
    template=frappe.db.get_value("Sales Taxes and Charges Template",{"title":TAX_TITLE,"company":s.COMPANY,"disabled":0},"name")
    # The desk form fills these when an item is picked; a pre-built draft has to carry them itself.
    item=frappe.db.get_value("Item",fee_item,["item_name","description","stock_uom"],as_dict=True) or {}
    uom=item.get("stock_uom") or "Nos"
    return dict(doctype="Sales Invoice",company=s.COMPANY,customer=reference.applicant,currency="FJD",conversion_rate=1,
        custom_hp_independent_fee=1,custom_hp_reference_loan=loan,custom_hp_reference_application=reference.loan_application,
        debit_to=account("1120"),disable_rounded_total=1,taxes_and_charges=template,
        items=[dict(item_code=fee_item,item_name=item.get("item_name") or fee_item,description=item.get("description") or fee_item,
            uom=uom,stock_uom=uom,conversion_factor=1,qty=1,rate=rate,income_account=account(number))])


def validate_receipt(doc, method=None):
    """Prevent a service-fee receipt being combined with an EMI/deposit allocation."""
    refs=doc.get("references") or []
    fees=[]
    for row in refs:
        if row.reference_doctype=="Sales Invoice":
            invoice=frappe.get_doc("Sales Invoice",row.reference_name)
            if cint(invoice.get("custom_hp_independent_fee")): fees.append(invoice)
    if not fees: return
    _enabled()
    if doc.company!=s.COMPANY or doc.payment_type!="Receive" or doc.party_type!="Customer" or len(fees)!=len(refs):
        frappe.throw("Keep independent fee receipts separate from instalments, deposits and other documents")
    if doc.paid_from!=account("1120") or money(doc.get("unallocated_amount")) or money(doc.get("difference_amount")):
        frappe.throw("Allocate the fee receipt exactly to its 1120 invoice debt; no excess or deductions")
    for invoice in fees:
        invoice.check_permission("read")
        if invoice.docstatus!=1 or invoice.customer!=doc.party or invoice.company!=doc.company or any(invoice.get(f) for f in TRIGGERS):
            frappe.throw("Receipt must reference only submitted independent invoices for this Customer and Company")
    if any(r for r in doc.get("deductions") or []): frappe.throw("Fee waivers require separately authorised credit notes, not receipt deductions")
