"""Servicing-fee VAT check. Read-only: shows every submitted SMS / Call / Field Visit invoice, what VAT it booked,
what the approved VAT-inclusive 12.5% split should be, and whether the income account is the approved one.
Nothing is changed or corrected here; submitted invoices are corrected only by an authorised, reviewed document."""
from decimal import Decimal, ROUND_HALF_UP

import frappe

from comfort_homes.hp_lending import hp_settings as s
from comfort_homes.hp_lending.hp_rollout import FEE_RATES

ALLOWED_ROLES = ("Director", "Finance Manager", "Accounts Officer")
VAT_DIVISOR = Decimal("1.125")  # 12.5% VAT included in the price


def _d(v): return Decimal(str(v or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def split(gross):
    """VAT-inclusive gross -> (net, vat). 2.50 -> 2.22 + 0.28; 25.00 -> 22.22 + 2.78."""
    gross = _d(gross); net = (gross / VAT_DIVISOR).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return net, gross - net


def assess(invoice, lines, vat_booked, expected_accounts):
    """Pure. lines: [{item_code, amount, income_account}]. Returns one result row."""
    # With the approved inclusive template the grand total is the VAT-inclusive price; a legacy invoice with
    # no tax row has the same grand total, so the expected split is always taken from it.
    billed = _d(invoice["grand_total"])
    net, vat = split(billed)
    booked = _d(vat_booked)
    wrong = [l["item_code"] for l in lines if l["income_account"] != expected_accounts.get(l["item_code"])]
    if booked == 0: status = "No VAT, account" if wrong else "No VAT"
    elif booked != vat: status = "Check"
    else: status = "Account" if wrong else "OK"
    return dict(invoice=invoice["name"], date=invoice["posting_date"], customer=invoice["customer"], loan=invoice.get("loan") or invoice.get("custom_hp_reference_loan") or "",
                route="Independent" if invoice.get("custom_hp_independent_fee") else "Legacy", items=", ".join(sorted({l["item_code"] for l in lines})),
                billed=float(billed), vat_booked=float(booked), expected_net=float(net), expected_vat=float(vat), status=status)


def require_role():
    if not set(ALLOWED_ROLES) & set(frappe.get_roles()) and frappe.session.user != "Administrator":
        frappe.throw("Fee VAT check is for Directors, Finance Managers and Accounts Officers only.", frappe.PermissionError)


def fetch(filters=None):
    require_role()
    from comfort_homes.hp_lending.hp_rollout import account
    expected = {code: account(num) for code, (_r, num) in FEE_RATES.items()}
    vat_account = account("2310")
    rows = frappe.get_all("Sales Invoice Item", filters={"item_code": ["in", list(FEE_RATES)], "docstatus": 1},
                          fields=["parent", "item_code", "amount", "income_account"], limit_page_length=0)
    names = sorted({r["parent"] for r in rows})
    if not names: return []
    invoices = {i["name"]: i for i in frappe.get_all("Sales Invoice", filters={"name": ["in", names], "company": s.COMPANY, "is_return": 0},
        fields=["name", "posting_date", "customer", "grand_total", "loan", "custom_hp_reference_loan", "custom_hp_independent_fee"], limit_page_length=0)}
    vat = {}
    for t in frappe.get_all("Sales Taxes and Charges", filters={"parent": ["in", list(invoices)], "account_head": vat_account},
                            fields=["parent", "tax_amount"], limit_page_length=0):
        vat[t["parent"]] = vat.get(t["parent"], 0) + (t["tax_amount"] or 0)
    by = {}
    for r in rows:
        if r["parent"] in invoices: by.setdefault(r["parent"], []).append(r)
    out = [assess(invoices[n], by[n], vat.get(n, 0), expected) for n in by]
    if filters and filters.get("only_issues"): out = [r for r in out if r["status"] != "OK"]
    return sorted(out, key=lambda r: (r["status"] == "OK", str(r["date"]), r["invoice"]))
