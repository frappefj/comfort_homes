"""Money in per day and receiving location: deposits, repayments, fees, credit held, unidentified (read-only)."""
import frappe
from frappe.utils import getdate, today

from comfort_homes.hp_lending import hp_collections as c


def execute(filters=None):
	c.require_role()
	filters = dict(filters or {})
	from_date = getdate(filters.get("from_date") or today())
	to_date = getdate(filters.get("to_date") or from_date)
	if to_date < from_date:
		frappe.throw("To Date must be on or after From Date.")
	receipts = c.collections(from_date, to_date)
	if filters.get("location"):
		receipts = [r for r in receipts if r["location"] == filters["location"]]
	if filters.get("show_receipts"):
		columns = [
			dict(fieldname="date", label="Date", fieldtype="Date", width=100),
			dict(fieldname="location", label="Received at", fieldtype="Data", width=150),
			dict(fieldname="kind_label", label="Type", fieldtype="Data", width=120),
			dict(fieldname="amount", label="Amount", fieldtype="Currency", width=120),
			dict(fieldname="party", label="Customer", fieldtype="Data", width=200),
			dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=160),
			dict(fieldname="voucher_type", label="Voucher Type", fieldtype="Data", width=130),
			dict(fieldname="voucher_no", label="Voucher", fieldtype="Dynamic Link", options="voucher_type", width=170),
			dict(fieldname="account", label="Account", fieldtype="Link", options="Account", width=220),
		]
		rows = [dict(r, kind_label=c.LABELS[r["kind"]]) for r in sorted(receipts, key=lambda r: (str(r["date"]), r["location"], r["voucher_no"]))]
		return columns, rows
	by_date = not filters.get("totals_only")
	columns = ([dict(fieldname="date", label="Date", fieldtype="Date", width=100)] if by_date else []) + [
		dict(fieldname="location", label="Received at", fieldtype="Data", width=160)] + [
		dict(fieldname=key, label=c.LABELS[key], fieldtype="Currency", width=125) for key in c.COLUMNS] + [
		dict(fieldname="total", label="Total in", fieldtype="Currency", width=130)]
	rows = c.summarise(receipts, by_date=by_date)
	total = sum(r["total"] for r in rows)
	summary = [dict(label="Total in", value=total, datatype="Currency", indicator="Green")] + [
		dict(label=c.LABELS[key], value=sum(r[key] for r in rows), datatype="Currency", indicator="Blue")
		for key in ("deposits", "repayments") ]
	return columns, rows, None, None, summary
