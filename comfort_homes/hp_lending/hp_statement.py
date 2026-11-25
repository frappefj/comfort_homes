"""Statement of Account for Flexi HP loans, new and converted (dev27, F21).

What the customer owes = the gross HP balance (contract total, less payments, plus default charges,
less rebates and reversals) less any payment held as customer credit (2017).  Instalments paid from
that credit are internal and not listed again; the receipt is listed when it was received.

* ``statement_data`` builds the figures; the "HP Statement of Account" print format renders them.
* ``download_statement`` gives the PDF for a requested period (Loan form, HP > Statement).
* ``quarterly_statements`` (1st of Jan, Apr, Jul, Oct) attaches each open loan's statement for the
  quarter just ended to the Loan, and emails it where the customer has an email address; the
  HP Statement Register report lists them for the team to print and send individually.
"""

from datetime import date

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

PRINT_FORMAT = "HP Statement of Account"
ROLES = ("Accounts Manager", "Accounts Officer", "Finance Manager", "Loan Manager", "Loan Officer", "System Manager")


def quarter_before(run_date):
	"""Pure: (first day, last day, label) of the quarter that ended before ``run_date``."""
	run_date = getdate(run_date)
	q = (run_date.month - 1) // 3  # quarter of the run date, 0-based
	year = run_date.year if q else run_date.year - 1
	q = q - 1 if q else 3
	start = date(year, q * 3 + 1, 1)
	end = add_days(date(year + (q == 3), (q * 3 + 3) % 12 + 1, 1), -1)
	return start, getdate(end), f"{year}-Q{q + 1}"


def build_lines(events, from_date, to_date):
	"""Pure: opening balance before ``from_date`` and the dated lines in the period with a running balance.

	``events`` = [(date, description, debit, credit)]; the order within a day is preserved."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	ordered = sorted(enumerate(events), key=lambda e: (getdate(e[1][0]), e[0]))
	opening, lines = m.d(0), []
	for _i, (when, text, debit, credit) in ordered:
		when = getdate(when)
		movement = m.money(debit) - m.money(credit)
		if when < from_date:
			opening += movement
		elif when <= to_date:
			lines.append({"date": when, "description": text, "debit": m.money(debit), "credit": m.money(credit)})
	balance = m.money(opening)
	for line in lines:
		balance = m.money(balance + line["debit"] - line["credit"])
		line["balance"] = balance
	return {"opening": m.money(opening), "lines": lines, "closing": balance}


def _events(loan):
	from comfort_homes.hp_lending.hp_credit import credit_account
	credit = credit_account(required=False)
	events = []
	for d in frappe.get_all("Loan Disbursement", filters={"against_loan": loan.name, "docstatus": 1},
			fields=["disbursement_date", "disbursed_amount"], order_by="disbursement_date"):
		label = _("Contract balance transferred from MYOB") if loan.get("is_imported") else _("Hire purchase contract (balance after deposit, including charges)")
		events.append((d.disbursement_date, label, d.disbursed_amount, 0))
	for r in frappe.get_all("Loan Repayment", filters={"against_loan": loan.name, "docstatus": 1},
			fields=["value_date", "amount_paid", "is_imported", "payment_account", "reference_number"], order_by="value_date"):
		if credit and r.payment_account == credit:
			continue  # paid from credit: the receipt is already listed when it came in
		label = _("Payments made before migration") if r.is_imported else (
			_("Early settlement") if str(r.reference_number or "").startswith("HP-SETTLE") else _("Payment received"))
		events.append((r.value_date, label, 0, r.amount_paid))
	for a in frappe.get_all("Loan Balance Adjustment", filters={"loan": loan.name, "docstatus": 1},
			fields=["posting_date", "adjustment_type", "amount", "reference_number"], order_by="posting_date"):
		ref = str(a.reference_number or "")
		if a.adjustment_type == "Debit Adjustment":
			label = _("Default charges brought forward from MYOB") if ref.endswith("-OPEN") else _("Default charge")
			events.append((a.posting_date, label, a.amount, 0))
		else:
			label = _("Default charge reversed") if ref.startswith("HP-PEN-REV") else (
				_("Early settlement rebate (Rule of 78)") if ref.startswith("HP-REBATE") else _("Balance adjustment"))
			events.append((a.posting_date, label, 0, a.amount))
	for j in frappe.db.sql(
			"""SELECT je.posting_date, je.total_credit FROM `tabJournal Entry` je WHERE je.docstatus = 1
			   AND je.custom_hp_loan = %s AND je.cheque_no LIKE %s""", (loan.name, "HP-REBATE-REFUND-%"), as_dict=True):
		events.append((j.posting_date, _("Early settlement rebate (Rule of 78), refund due to you"), 0, j.total_credit))
	if credit:
		rows = frappe.db.sql(
			"""SELECT je.posting_date, je.cheque_no, jea.credit, jea.debit FROM `tabJournal Entry` je
			   JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
			   WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND jea.account = %s ORDER BY je.posting_date""",
			(loan.name, credit), as_dict=True)
		for j in rows:
			key = str(j.cheque_no or "")
			if j.credit:
				label = _("Paid ahead at migration from MYOB") if key.startswith("HP-OPEN-CREDIT") else _("Payment received (ahead of instalments)")
				events.append((j.posting_date, label, 0, j.credit))
			if j.debit:
				events.append((j.posting_date, _("Credit transferred for refund to you"), j.debit, 0))
	return events


def statement_data(loan_name, from_date=None, to_date=None):
	loan = frappe.get_doc("Loan", loan_name)
	app = frappe.get_doc("Loan Application", loan.loan_application) if loan.get("loan_application") else None
	to_date = getdate(to_date or today())
	from_date = getdate(from_date or loan.posting_date or to_date)
	figures = build_lines(_events(loan), from_date, to_date)
	overdue = m.money(frappe.db.sql(
		"""SELECT COALESCE(SUM(outstanding_amount), 0) FROM `tabLoan Demand`
		   WHERE loan = %s AND docstatus = 1 AND outstanding_amount > 0 AND demand_date <= %s""",
		(loan.name, f"{to_date} 23:59:59"))[0][0])
	upcoming = frappe.db.sql(
		"""SELECT rs.payment_date, rs.total_payment FROM `tabRepayment Schedule` rs
		   JOIN `tabLoan Repayment Schedule` lrs ON lrs.name = rs.parent
		   WHERE lrs.loan = %s AND lrs.docstatus = 1 AND lrs.status = 'Active' AND rs.payment_date > %s
		   ORDER BY rs.payment_date LIMIT 1""", (loan.name, to_date), as_dict=True)
	fees = m.money(frappe.db.sql(
		"""SELECT COALESCE(SUM(outstanding_amount), 0) FROM `tabSales Invoice` WHERE docstatus = 1
		   AND custom_hp_independent_fee = 1 AND custom_hp_reference_loan = %s AND outstanding_amount > 0""",
		(loan.name,))[0][0]) if frappe.db.has_column("Sales Invoice", "custom_hp_reference_loan") else m.d(0)
	customer = frappe.db.get_value("Customer", loan.applicant, ["customer_name", "customer_primary_address", "email_id", "mobile_no"], as_dict=True) or {}
	address = frappe.db.get_value("Address", customer.get("customer_primary_address"),
		["address_line1", "address_line2", "city", "state", "country"], as_dict=True) if customer.get("customer_primary_address") else None
	return {
		"loan": loan.name, "application": loan.get("loan_application"), "status": loan.status,
		"customer": customer.get("customer_name") or loan.applicant_name or loan.applicant,
		"address": [x for x in ([address.address_line1, address.address_line2, address.city, address.state] if address else []) if x],
		"card_id": app.get("custom_card_id") if app else None,
		"hp_account": (app.get("custom_flexi_hp_account_number") if app else None),
		"contract_date": loan.posting_date, "term_months": app.get("custom_flexi_term_months") if app else None,
		"frequency": app.get("custom_flexi_payment_period") if app else loan.repayment_frequency,
		"from_date": from_date, "to_date": to_date, "issued": getdate(today()),
		"opening": figures["opening"], "lines": figures["lines"], "closing": figures["closing"],
		"overdue": overdue, "fees_outstanding": fees,
		"next_due_date": upcoming[0].payment_date if upcoming else None,
		"next_due_amount": m.money(upcoming[0].total_payment) if upcoming else None,
		"default_rate": app.get("custom_hp_default_rate_monthly") if app and app.get("custom_hp_default_rate_monthly") else 2.5,
		"email": customer.get("email_id"),
	}


def render_pdf(loan_name, from_date=None, to_date=None):
	doc = frappe.get_doc("Loan", loan_name)
	doc.statement_from, doc.statement_to = from_date, to_date
	return frappe.get_print("Loan", loan_name, PRINT_FORMAT, doc=doc, as_pdf=True, no_letterhead=0)


@frappe.whitelist()
def download_statement(loan, from_date=None, to_date=None):
	"""On request: the PDF for a period (default: contract date to today)."""
	frappe.only_for(ROLES)
	frappe.get_doc("Loan", loan).check_permission("read")
	frappe.local.response.filename = f"Statement-{loan}-{getdate(to_date or today())}.pdf"
	frappe.local.response.filecontent = render_pdf(loan, from_date, to_date)
	frappe.local.response.type = "download"


def _statement_loans(start):
	return frappe.get_all("Loan", filters={"company": s.COMPANY, "docstatus": 1, "loan_application": ["!=", ""],
		"status": ["in", ["Disbursed", "Partially Disbursed", "Closed"]]},
		or_filters=[["status", "!=", "Closed"], ["closure_date", ">=", start]], pluck="name", limit_page_length=0)


def quarterly_statements(run_date=None):
	"""Scheduled 06:30 on the 1st of Jan, Apr, Jul and Oct: enqueue the quarter's statements in batches."""
	start, end, label = quarter_before(run_date or today())
	loans = _statement_loans(start)
	for i in range(0, len(loans), 50):
		frappe.enqueue("comfort_homes.hp_lending.hp_statement.issue_statements", queue="long", timeout=3000,
			loans=loans[i:i + 50], from_date=str(start), to_date=str(end), label=label)
	return {"period": label, "loans": len(loans)}


def file_name(loan, label):
	return f"Statement-{loan}-{label}.pdf"


def issue_statements(loans, from_date, to_date, label):
	for loan in loans:
		name = file_name(loan, label)
		if frappe.db.exists("File", {"attached_to_doctype": "Loan", "attached_to_name": loan, "file_name": name}):
			continue
		try:
			pdf = render_pdf(loan, from_date, to_date)
			frappe.get_doc({"doctype": "File", "file_name": name, "attached_to_doctype": "Loan", "attached_to_name": loan,
				"is_private": 1, "content": pdf}).insert(ignore_permissions=True)
			data = statement_data(loan, from_date, to_date)
			if data["email"]:
				frappe.sendmail(recipients=[data["email"]], subject=_("Your Flexible Purchase statement {0} ({1})").format(label, loan),
					message=_("Dear {0},<br><br>Please find attached your hire purchase statement for {1} to {2}.<br><br>"
						"Comfort Home Furnishing Pte Limited T/A Flexible Purchase").format(data["customer"], from_date, to_date),
					attachments=[{"fname": name, "fcontent": pdf}], reference_doctype="Loan", reference_name=loan, delayed=True)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"HP statement {label} {loan}", reference_doctype="Loan", reference_name=loan)


@frappe.whitelist(methods=["POST"])
def run_quarterly_now(run_date=None):
	"""System Manager: issue the statements for the quarter before ``run_date`` now (normally scheduled)."""
	frappe.only_for("System Manager")
	return quarterly_statements(run_date)
