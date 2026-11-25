"""Closed loans that still carry deferred interest or penalties, and what the daily job will do (read-only)."""
import frappe

from comfort_homes.hp_lending import hp_closure as c

ALLOWED_ROLES = ("Director", "Finance Manager", "Accounts Manager", "Accounts Officer")


def execute(filters=None):
    if not set(ALLOWED_ROLES) & set(frappe.get_roles()) and frappe.session.user != "Administrator":
        frappe.throw("HP Closure Review is for Directors, Finance and Accounts only.", frappe.PermissionError)
    filters = dict(filters or {})
    rows = c.review_rows()
    if filters.get("action"):
        rows = [r for r in rows if r["action"] == filters["action"]]
    rows.sort(key=lambda r: (r["action"] != "Rebate owed", str(r["closure_date"] or "")))
    money = lambda f, l: dict(fieldname=f, label=l, fieldtype="Currency", width=130)
    columns = [
        dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=150),
        dict(fieldname="customer", label="Customer", fieldtype="Data", width=200),
        dict(fieldname="application", label="Application", fieldtype="Link", options="Loan Application", width=160),
        dict(fieldname="closure_date", label="Closed On", fieldtype="Date", width=100),
        dict(fieldname="maturity", label="Contract Maturity", fieldtype="Date", width=120),
        money("deferred_interest", "Deferred Interest (2013)"),
        money("deferred_penalty", "Deferred Penalty (2015)"),
        dict(fieldname="action", label="Next Step", fieldtype="Data", width=150),
    ]
    rebate = sum(1 for r in rows if r["action"] == "Rebate owed")
    summary = [dict(value=len(rows), label="Closed loans with deferred balances", datatype="Int", indicator="Blue"),
               dict(value=rebate, label="Awaiting settlement rebate", datatype="Int", indicator="Red" if rebate else "Green")]
    return columns, rows, None, None, summary
