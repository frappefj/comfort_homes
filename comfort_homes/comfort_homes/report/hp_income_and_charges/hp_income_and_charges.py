"""HP income and charges by loan for a period (read-only). 7.1.6."""
import frappe
from frappe.utils import get_first_day, getdate, today

from comfort_homes.hp_lending import hp_income as h


def execute(filters=None):
	h.require_role()
	filters = dict(filters or {})
	to_date = getdate(filters.get("to_date") or today())
	from_date = getdate(filters.get("from_date") or get_first_day(to_date))
	if to_date < from_date:
		frappe.throw("To Date must be on or after From Date.")
	rows = h.figures(from_date, to_date)
	loans = {l.name: l for l in frappe.get_all("Loan", filters={"name": ["in", [r["loan"] for r in rows if r["loan"] != h.UNLINKED]]},
		fields=["name", "applicant", "status", "cost_center"], limit_page_length=0)} if rows else {}
	for r in rows:
		loan = loans.get(r["loan"])
		r.update(customer=loan.applicant if loan else "", status=loan.status if loan else "", location=loan.cost_center if loan else "")
	if filters.get("location"):
		rows = [r for r in rows if r["location"] == filters["location"]]
	if not filters.get("show_all"):
		rows = [r for r in rows if any(r[k] for k in r if k not in ("loan", "customer", "status", "location"))]
	money = lambda f, l, w=115: dict(fieldname=f, label=l, fieldtype="Currency", width=w)
	columns = [
		dict(fieldname="loan", label="Loan", fieldtype="Data", width=150),
		dict(fieldname="customer", label="Customer", fieldtype="Data", width=180),
		dict(fieldname="location", label="Location", fieldtype="Link", options="Cost Center", width=120),
		dict(fieldname="status", label="Status", fieldtype="Data", width=85),
		money("interest_earned", "Interest earned"), money("interest_to_date", "Interest earned to date", 130),
		money("unearned_interest", "Interest not yet earned", 130),
		money("admin_fee", "Admin fee"), money("doc_fee", "Doc fee"),
		money("charges_raised", "Default charges raised", 130), money("charges_cleared", "Charges cleared", 120),
		money("charges_held", "Charges held (to closure)", 140), money("charges_income", "Charges to income", 125),
		money("fees_billed", "Fees billed"), money("fees_paid", "Fees paid"),
	]
	t = h.totals(rows)
	summary = [
		dict(label="Interest earned", value=t.get("interest_earned", 0), datatype="Currency", indicator="Green"),
		dict(label="Interest not yet earned", value=t.get("unearned_interest", 0), datatype="Currency", indicator="Blue"),
		dict(label="Admin + doc fees", value=t.get("admin_fee", 0) + t.get("doc_fee", 0), datatype="Currency", indicator="Green"),
		dict(label="Default charges held", value=t.get("charges_held", 0), datatype="Currency", indicator="Orange"),
		dict(label="Fees billed", value=t.get("fees_billed", 0), datatype="Currency", indicator="Blue"),
		dict(label="Fees paid", value=t.get("fees_paid", 0), datatype="Currency", indicator="Green"),
	]
	return columns, rows, None, None, summary
