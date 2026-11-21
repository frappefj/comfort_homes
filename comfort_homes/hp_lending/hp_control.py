"""Portfolio Control: one read-only line per application/loan with a traffic light, worst first.

No ledger or status writes. Role gated (Director, Finance Manager, Loan Manager). All data is read with
a handful of set-based queries, never per-loan lookups. The traffic-light rules are deliberately simple:

* Closed                          -> Green
* Active loan, repayments current -> Green; in 7-day grace -> Orange; 8+ days past due -> Red
* Any earlier stage (still moving)-> Red when nothing has happened for more than 72 hours, else Orange
"""
import frappe
from frappe.utils import flt, getdate, now_datetime, today

from comfort_homes.hp_lending import hp_settings as s

ALLOWED_ROLES = ("Director", "Finance Manager", "Loan Manager")
IDLE_RED_HOURS = 72
GRACE_DAYS = 7
NPA_DAYS = 90

# workflow state -> (one-word label, order)
STAGES = {
    "Draft": ("Draft", 1), "Initiated": ("Intake", 2), "Under Review": ("Review", 3),
    "Pending Approval": ("Approval", 4), "Approved": ("Approved", 5), "Contract Signing": ("Contract", 6),
    "Ready for Disbursement": ("Disburse", 7), "Loan Activated": ("Activated", 8),
    "LPO Pending Approval": ("LPO", 9), "LPO Approved": ("Release", 10), "LPO Sent to Supplier": ("Sent", 11),
    "Delivered - Awaiting Documents": ("Delivered", 12), "Active Loan Account": ("Active", 13),
    "Closed": ("Closed", 14), "Rejected": ("Rejected", 0),
}
STAGE_OPTIONS = [""] + [v[0] for v in sorted(STAGES.values(), key=lambda v: v[1])]
DELIVERED_ORDER = STAGES["Delivered - Awaiting Documents"][1]
ACTIVE_ORDER = STAGES["Active Loan Account"][1]
CLOSED_ORDER = STAGES["Closed"][1]
LIGHT_RANK = {"Red": 0, "Orange": 1, "Green": 2, "Grey": 3}
BUCKET_RANK = {"NPA": 5, "D90": 4, "D60": 3, "D30": 2, "Grace": 1, "Current": 0, "": 0}


def require_role():
    if not set(ALLOWED_ROLES) & set(frappe.get_roles()) and frappe.session.user != "Administrator":
        frappe.throw("Portfolio Control is for Directors and Managers only.", frappe.PermissionError)


def bucket(days_past_due, is_npa=False):
    d = int(days_past_due or 0)
    if is_npa or d >= NPA_DAYS: return "NPA"
    if d > 60: return "D90"      # 61-89
    if d > 30: return "D60"      # 31-60
    if d > GRACE_DAYS: return "D30"   # 8-30
    if d > 0: return "Grace"     # 1-7
    return "Current"


def light(stage_order, bucket_name, idle_hours):
    """Traffic light for the lowest (current) status of a case."""
    if stage_order == 0: return "Grey"
    if stage_order >= CLOSED_ORDER: return "Green"
    if stage_order == ACTIVE_ORDER:
        return {"Current": "Green", "Grace": "Orange"}.get(bucket_name, "Red")
    return "Red" if idle_hours > IDLE_RED_HOURS else "Orange"


def pi_status(invoices):
    if not invoices: return "None"
    total = sum(flt(i["grand_total"]) for i in invoices)
    out = sum(flt(i["outstanding_amount"]) for i in invoices)
    if out <= 0.005: return "Paid"
    return "Part" if out < total - 0.005 else "Open"


def delivery_status(stage_order, state):
    if stage_order >= DELIVERED_ORDER: return "Yes"
    return "Pending" if state == "LPO Sent to Supplier" else "No"


def sort_key(row):
    return (LIGHT_RANK[row["light"]], row["stage_order"] >= CLOSED_ORDER, -BUCKET_RANK[row["bucket"]], -flt(row["idle_hours"]), -flt(row["overdue"]), row["application"] or "")


def assemble(apps, loans, demands, next_due, invoices, activity, now, filters=None, co_apps=None):
    """Pure: join already-fetched data into rows. `activity` maps application -> latest datetime."""
    filters = filters or {}
    loan_by_app = {l["loan_application"]: l for l in loans if l.get("loan_application")}
    rows, seen = [], set()
    for app in apps:
        loan = loan_by_app.get(app["name"])
        seen.add(loan["name"] if loan else None)
        rows.append(_row(app, loan, demands, next_due, invoices, activity, now, co_apps))
    for loan in loans:  # loans with no application (for example imported) still count
        if loan["name"] not in seen and not loan.get("loan_application"):
            rows.append(_row(None, loan, demands, next_due, invoices, activity, now, co_apps))
    rows = [r for r in rows if _wanted(r, filters)]
    rows.sort(key=sort_key)
    return rows


def co_app_status(states):
    """None without co-applicants, Approved when every one is approved, otherwise Pending."""
    if not states: return "None"
    return "Approved" if all(x == "Approved" for x in states) else "Pending"


def _row(app, loan, demands, next_due, invoices, activity, now, co_apps=None):
    state = (app or {}).get("workflow_state") or ("Active Loan Account" if loan else "")
    label, order = STAGES.get(state, (state or "Unknown", 0 if not state else 1))
    if loan and loan.get("status") == "Closed": label, order = "Closed", CLOSED_ORDER
    d = demands.get(loan["name"]) if loan else None
    dpd = max((getdate(today()) - getdate(d["oldest"])).days, 0) if d and d["oldest"] else 0
    bkt = bucket(dpd, bool(loan and loan.get("is_npa"))) if loan and order >= ACTIVE_ORDER else ""
    if loan and order >= ACTIVE_ORDER and order < CLOSED_ORDER and not bkt: bkt = "Current"
    last = activity.get((app or {}).get("name")) or (app or loan or {}).get("modified")
    idle = round((now - last).total_seconds() / 3600, 1) if last else 0.0
    inv = invoices.get((app or {}).get("name"), [])
    balance = None
    if loan and loan.get("docstatus") == 1:
        balance = round(flt(loan["total_payment"]) - flt(loan["total_principal_paid"])
                        + flt(loan["debit_adjustment_amount"]) - flt(loan["credit_adjustment_amount"]), 2)
    return dict(
        application=(app or {}).get("name"), loan=(loan or {}).get("name"),
        customer=(app or {}).get("applicant_name") or (loan or {}).get("applicant_name"),
        location=(app or {}).get("custom_branchlocation") or (loan or {}).get("cost_center"),
        stage=label, stage_order=order, light=light(order, bkt, idle), idle_hours=idle,
        bucket=bkt, dpd=dpd if bkt else None, overdue=round(flt(d["amount"]), 2) if d else 0.0,
        next_due=next_due.get(loan["name"]) if loan else None, balance=balance,
        pi=pi_status(inv), delivery=delivery_status(order, state),
        co_app=co_app_status((co_apps or {}).get((app or {}).get("name"), [])))


def _wanted(row, f):
    if f.get("stage") and row["stage"] != f["stage"]: return False
    if f.get("light") and row["light"] != f["light"]: return False
    if f.get("bucket") and row["bucket"] != f["bucket"]: return False
    if not f.get("include_closed", 1) and row["stage"] == "Closed": return False
    if not f.get("include_rejected", 0) and row["stage"] == "Rejected": return False
    return True


def fetch(filters=None):
    require_role()
    filters = filters or {}
    q = {"company": s.COMPANY, "docstatus": ["!=", 2]}
    if filters.get("location"): q["custom_branchlocation"] = filters["location"]
    apps = frappe.get_all("Loan Application", filters=q,
        fields=["name", "workflow_state", "custom_branchlocation", "applicant_name", "modified"], limit_page_length=0)
    loans = frappe.get_all("Loan", filters={"company": s.COMPANY, "docstatus": ["!=", 2]},
        fields=["name", "loan_application", "status", "docstatus", "is_npa", "cost_center", "applicant_name", "modified",
                "total_payment", "total_principal_paid", "debit_adjustment_amount", "credit_adjustment_amount"], limit_page_length=0)
    if filters.get("location"):
        keep = {a["name"] for a in apps}
        loans = [l for l in loans if l.get("loan_application") in keep]
    # one grouped query each, never one per loan
    demands = {r.loan: dict(oldest=r.oldest, amount=r.amount) for r in frappe.db.sql(
        """SELECT loan, MIN(demand_date) oldest, SUM(outstanding_amount) amount FROM `tabLoan Demand`
           WHERE docstatus=1 AND demand_type='EMI' AND outstanding_amount>0 AND demand_date < %s GROUP BY loan""",
        (today(),), as_dict=True)}
    next_due = {r.loan: r.nxt for r in frappe.db.sql(
        """SELECT lrs.loan, MIN(rs.payment_date) nxt FROM `tabRepayment Schedule` rs
           JOIN `tabLoan Repayment Schedule` lrs ON lrs.name = rs.parent
           WHERE lrs.docstatus=1 AND lrs.status='Active' AND rs.payment_date >= %s GROUP BY lrs.loan""",
        (today(),), as_dict=True)}
    invoices = {}
    for r in frappe.get_all("Purchase Invoice", filters={"company": s.COMPANY, "docstatus": 1, "custom_loan_application": ["is", "set"]},
                            fields=["custom_loan_application", "grand_total", "outstanding_amount"], limit_page_length=0):
        invoices.setdefault(r.custom_loan_application, []).append(r)
    activity = {}
    def bump(name, when):
        if name and when and (name not in activity or when > activity[name]): activity[name] = when
    for r in frappe.db.sql("""SELECT reference_name n, MAX(creation) w FROM `tabComment`
            WHERE reference_doctype='Loan Application' GROUP BY reference_name""", as_dict=True): bump(r.n, r.w)
    for r in frappe.db.sql("""SELECT custom_loan_application n, MAX(modified) w FROM `tabPurchase Order`
            WHERE custom_loan_application IS NOT NULL AND custom_loan_application != '' GROUP BY custom_loan_application""", as_dict=True): bump(r.n, r.w)
    for a in apps: bump(a["name"], a["modified"])
    co_apps = {}
    for r in frappe.get_all("Co-Applicant", filters={"loan_application": ["is", "set"], "docstatus": ["!=", 2]},
                            fields=["loan_application", "workflow_state"], limit_page_length=0):
        co_apps.setdefault(r.loan_application, []).append(r.workflow_state)
    return assemble(apps, loans, demands, next_due, invoices, activity, now_datetime(), filters, co_apps)


def summary(rows):
    count = lambda c: sum(1 for r in rows if r["light"] == c)
    live = [r for r in rows if r["stage"] == "Active"]
    return dict(red=count("Red"), orange=count("Orange"), green=count("Green"),
        npa=sum(1 for r in rows if r["bucket"] == "NPA"),
        overdue=round(sum(flt(r["overdue"]) for r in rows), 2),
        book=round(sum(flt(r["balance"]) for r in live), 2))


def _card(key, fieldtype="Int"):
    value = summary(fetch({"include_closed": 0}))[key]
    return {"value": value, "fieldtype": fieldtype, "route": ["query-report", "Portfolio Control"]}


@frappe.whitelist()
def card_red(): return _card("red")
@frappe.whitelist()
def card_orange(): return _card("orange")
@frappe.whitelist()
def card_green(): return _card("green")
@frappe.whitelist()
def card_npa(): return _card("npa")
@frappe.whitelist()
def card_overdue(): return _card("overdue", "Currency")
