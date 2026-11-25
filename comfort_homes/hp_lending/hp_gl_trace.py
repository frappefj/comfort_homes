"""HP GL Trace: every GL line behind a loan, customer side and supplier side, in one read-only list.

Pick a Loan or Loan Application (or none for the whole book) and optionally a "Since" time to see only what
the last action posted. Nothing is written. Role gated (Director, Finance Manager, Accounts Officer).
"""
import frappe
from frappe.utils import flt

from comfort_homes.hp_lending import hp_settings as s

ALLOWED_ROLES = ("Director", "Finance Manager", "Accounts Officer")
# (doctype, field that links it to the Loan): native Lending documents that post to the GL
LOAN_DOCS = (("Loan Disbursement", "against_loan"), ("Loan Repayment", "against_loan"),
             ("Loan Interest Accrual", "loan"), ("Loan Demand", "loan"), ("Loan Balance Adjustment", "loan"),
             ("Loan Write Off", "loan"), ("Loan Refund", "loan"), ("Loan Restructure", "loan"),
             ("Journal Entry", "custom_hp_loan"))
VIEWS = ("Lines", "By account", "By voucher")


def require_role():
    if not set(ALLOWED_ROLES) & set(frappe.get_roles()) and frappe.session.user != "Administrator":
        frappe.throw("HP GL Trace is for Directors, Finance Managers and Accounts Officers only.", frappe.PermissionError)


def checks(voucher_type, lines):
    """Short flags for one voucher's lines: Unbalanced, No VAT (Sales Invoice with no VAT line)."""
    flags = []
    if abs(sum(flt(l["debit"]) for l in lines) - sum(flt(l["credit"]) for l in lines)) > 0.005: flags.append("Unbalanced")
    if voucher_type == "Sales Invoice" and not any("VAT" in (l["account"] or "").upper() for l in lines): flags.append("No VAT")
    return ", ".join(flags)


def assemble(gl, loan_of, filters=None):
    """Pure. gl: GL Entry dicts. loan_of: {(voucher_type, voucher_no): loan}. Returns line rows sorted by time."""
    filters = filters or {}
    gl = [g for g in gl if not filters.get("account") or g["account"] == filters["account"]]
    by_voucher = {}
    for g in gl: by_voucher.setdefault((g["voucher_type"], g["voucher_no"]), []).append(g)
    rows = []
    for g in sorted(gl, key=lambda x: (str(x["creation"]), x["voucher_no"], x.get("name") or "")):
        key = (g["voucher_type"], g["voucher_no"])
        rows.append(dict(posted=g["creation"], date=g["posting_date"], type=g["voucher_type"], voucher=g["voucher_no"],
            account=g["account"], debit=flt(g["debit"]), credit=flt(g["credit"]), party=g.get("party") or "",
            loan=loan_of.get(key, ""), cancelled="Cancelled" if g.get("is_cancelled") else "",
            check=checks(g["voucher_type"], by_voucher[key])))
    return rows


def by_account(rows):
    acc = {}
    for r in rows:
        a = acc.setdefault(r["account"], [0.0, 0.0]); a[0] += r["debit"]; a[1] += r["credit"]
    out = [dict(account=k, debit=round(v[0], 2), credit=round(v[1], 2), net=round(v[0] - v[1], 2)) for k, v in sorted(acc.items())]
    return out


def by_voucher(rows):
    v = {}
    for r in rows:
        x = v.setdefault(r["voucher"], dict(voucher=r["voucher"], type=r["type"], date=r["date"], loan=r["loan"],
                                            party=r["party"], debit=0.0, credit=0.0, check=r["check"], posted=r["posted"]))
        x["debit"] += r["debit"]; x["credit"] += r["credit"]
    for x in v.values(): x["debit"] = round(x["debit"], 2); x["credit"] = round(x["credit"], 2)
    return sorted(v.values(), key=lambda x: (str(x["posted"]), x["voucher"]))


def _ids(rows, key="name"): return [r[key] for r in rows]


def fetch(filters=None):
    require_role()
    filters = filters or {}
    company = s.COMPANY
    lf = {"company": company, "docstatus": ["!=", 2]}
    if filters.get("loan"): lf["name"] = filters["loan"]
    if filters.get("application"): lf["loan_application"] = filters["application"]
    loans = frappe.get_all("Loan", filters=lf, fields=["name", "loan_application", "applicant"], limit_page_length=0)
    apps = {l["loan_application"] for l in loans if l["loan_application"]}
    if filters.get("application"): apps.add(filters["application"])
    loan_names = _ids(loans)
    vouchers, loan_of = {}, {}

    def add(vt, names, loan_for=None):
        for n in names:
            vouchers.setdefault(vt, set()).add(n)
            if loan_for: loan_of[(vt, n)] = loan_for(n)

    for dt, field in LOAN_DOCS:
        if not loan_names or not frappe.db.has_column(dt, field): continue
        for r in frappe.get_all(dt, filters={field: ["in", loan_names], "docstatus": ["!=", 0]}, fields=["name", field], limit_page_length=0):
            add(dt, [r["name"]]); loan_of[(dt, r["name"])] = r[field]
    app_loan = {l["loan_application"]: l["name"] for l in loans if l["loan_application"]}
    # deposit receipt
    if apps and frappe.db.has_column("Loan Application", "custom_hp_deposit_payment_entry"):
        for r in frappe.get_all("Loan Application", filters={"name": ["in", list(apps)]}, fields=["name", "custom_hp_deposit_payment_entry"], limit_page_length=0):
            if r["custom_hp_deposit_payment_entry"]:
                add("Payment Entry", [r["custom_hp_deposit_payment_entry"]]); loan_of[("Payment Entry", r["custom_hp_deposit_payment_entry"])] = app_loan.get(r["name"], "")
    # fee invoices, and the receipts that settle them
    sis = []
    if loan_names:
        sis = frappe.get_all("Sales Invoice", filters={"docstatus": 1}, or_filters=[["custom_hp_reference_loan", "in", loan_names], ["loan", "in", loan_names]],
                             fields=["name", "custom_hp_reference_loan", "loan"], limit_page_length=0)
    for r in sis:
        add("Sales Invoice", [r["name"]]); loan_of[("Sales Invoice", r["name"])] = r.get("custom_hp_reference_loan") or r.get("loan") or ""
    pe_refs = _payment_refs("Sales Invoice", _ids(sis))
    for pe, ref in pe_refs: add("Payment Entry", [pe]); loan_of[("Payment Entry", pe)] = loan_of.get(("Sales Invoice", ref), "")
    # supplier side: purchase orders/invoices of the application, and the payments that settle them
    if filters.get("include_supplier", 1) and apps:
        pis = frappe.get_all("Purchase Invoice", filters={"company": company, "docstatus": 1, "custom_loan_application": ["in", list(apps)]},
                             fields=["name", "custom_loan_application"], limit_page_length=0)
        for r in pis:
            add("Purchase Invoice", [r["name"]]); loan_of[("Purchase Invoice", r["name"])] = app_loan.get(r["custom_loan_application"], "")
        for pe, ref in _payment_refs("Purchase Invoice", _ids(pis)):
            add("Payment Entry", [pe]); loan_of[("Payment Entry", pe)] = loan_of.get(("Purchase Invoice", ref), "")
    if filters.get("voucher_type"): vouchers = {k: v for k, v in vouchers.items() if k == filters["voucher_type"]}
    gl = []
    for vt, names in vouchers.items():
        gf = {"voucher_type": vt, "voucher_no": ["in", sorted(names)], "company": company}
        if filters.get("since"): gf["creation"] = [">=", filters["since"]]
        gl += frappe.get_all("GL Entry", filters=gf, fields=["name", "creation", "posting_date", "account", "debit", "credit",
                             "voucher_type", "voucher_no", "party", "is_cancelled"], limit_page_length=0)
    return assemble(gl, loan_of, filters)


def _payment_refs(reference_doctype, names):
    if not names: return []
    refs = frappe.get_all("Payment Entry Reference", filters={"reference_doctype": reference_doctype, "reference_name": ["in", names], "docstatus": 1},
                          fields=["parent", "reference_name"], limit_page_length=0)
    return [(r["parent"], r["reference_name"]) for r in refs]
