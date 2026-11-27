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

	``events`` = [(date, description, debit, credit[, method[, kind]])]; the order within a day is preserved.
	7.1.4: ``method`` (how a payment was made) and ``kind`` ("payment" for money received) are optional."""
	from_date, to_date = getdate(from_date), getdate(to_date)
	ordered = sorted(enumerate(events), key=lambda e: (getdate(e[1][0]), e[0]))
	opening, lines = m.d(0), []
	for _i, event in ordered:
		when, text, debit, credit = event[:4]
		method = event[4] if len(event) > 4 else None
		when = getdate(when)
		movement = m.money(debit) - m.money(credit)
		if when < from_date:
			opening += movement
		elif when <= to_date:
			lines.append({"date": when, "description": text, "debit": m.money(debit), "credit": m.money(credit),
				"method": method})
	balance = m.money(opening)
	for line in lines:
		balance = m.money(balance + line["debit"] - line["credit"])
		line["balance"] = balance
	return {"opening": m.money(opening), "lines": lines, "closing": balance}


def payment_method(account, mode=None):
	"""Pure: how the customer paid, from the receiving ledger account (7.1.4).

	Store tills are "Cash in Hand - <store>", the M-PAiSA wallet is 1206, everything else in Bank is a transfer."""
	name = str(account or "")
	if "M-PAiSA" in name or "MPAISA" in name.upper().replace("-", ""):
		return _("M-PAiSA")
	if "Cash in Hand" in name:
		store = name.split("Cash in Hand", 1)[1].strip(" -").rsplit(" - ", 1)[0].strip()
		return _("Cash ({0})").format(store) if store else _("Cash")
	if "Undeposited" in name:
		return _("Cash / cheque")
	if name:
		return _("Bank transfer")
	return mode or ""


def contract_summary(app, contract_total):
	"""Pure: the contract breakdown printed on the statement (7.1.4).

	Cash price less deposit, plus document fee, admin (establishment) fee and term charges = total payable.
	MYOB-converted contracts do not always add up from the application fields; then only the cash price,
	deposit and the contract total as transferred are shown (``ties`` False)."""
	app = app or {}
	cash = m.money(app.get("custom_flexi_cash_price"))
	deposit = m.money(app.get("custom_flexi_deposit_amount"))
	doc_fee = m.money(m.d(app.get("custom_flexi_doc_fee_net")) + m.d(app.get("custom_flexi_doc_fee_vat")))
	admin = m.money(app.get("custom_flexi_admin_fee"))
	charges = m.money(app.get("custom_flexi_term_charges"))
	financed = m.money(cash - deposit)
	total = m.money(financed + doc_fee + admin + charges)
	contract_total = m.money(contract_total)
	ties = bool(cash) and (not contract_total or abs(total - contract_total) <= m.d("0.05"))
	return {"cash_price": cash, "deposit": deposit, "financed": financed, "doc_fee": doc_fee, "admin_fee": admin,
		"term_charges": charges, "total_payable": total if ties else contract_total, "ties": ties}


def account_status(loan_status, overdue, days_past_due=0, settled=False):
	"""Pure: (label, arrears) for the statement header (7.1.4)."""
	if loan_status == "Closed":
		return (_("Settled early") if settled else _("Repaid")), m.d(0)
	overdue = m.money(overdue)
	if overdue > 0:
		days = int(days_past_due or 0)
		return (_("Past due ({0} days)").format(days) if days else _("Past due")), overdue
	return _("Current"), m.d(0)


def payment_history(events, deposit=None, to_date=None):
	"""Pure: every payment from the start of the contract to ``to_date``, deposit first, and the total paid
	including the deposit (7.1.4). ``deposit`` = {"date", "amount", "method"} or None."""
	to_date = getdate(to_date) if to_date else None
	rows = []
	if deposit and m.money(deposit.get("amount")) > 0:
		rows.append({"date": getdate(deposit.get("date")) if deposit.get("date") else None, "description": _("Deposit"),
			"method": deposit.get("method") or "", "amount": m.money(deposit.get("amount"))})
	ordered = sorted(enumerate(events), key=lambda e: (getdate(e[1][0]), e[0]))
	by_key = {}
	for _i, event in ordered:
		if len(event) < 6 or event[5] != "payment":
			continue
		when = getdate(event[0])
		if to_date and when > to_date:
			continue
		# One bank receipt split between instalments (repayment) and credit (journal) shares a reference: show it once.
		key = event[6] if len(event) > 6 and event[6] and not str(event[6]).startswith("HP-OPEN") else None
		if key and key in by_key:
			by_key[key]["amount"] = m.money(by_key[key]["amount"] + m.money(event[3]))
			by_key[key]["description"] = _("Payment received")
			continue
		row = {"date": when, "description": event[1], "method": event[4] or "", "amount": m.money(event[3])}
		rows.append(row)
		if key:
			by_key[key] = row
	return {"rows": rows, "total": m.money(sum((r["amount"] for r in rows), m.d(0)))}


def _events(loan):
	from comfort_homes.hp_lending.hp_credit import credit_account
	credit = credit_account(required=False)
	events = []
	for d in frappe.get_all("Loan Disbursement", filters={"against_loan": loan.name, "docstatus": 1},
			fields=["disbursement_date", "disbursed_amount"], order_by="disbursement_date"):
		label = _("Contract balance transferred from MYOB") if loan.get("is_imported") else _("Hire purchase contract (balance after deposit, including charges)")
		events.append((d.disbursement_date, label, d.disbursed_amount, 0, None, "contract"))
	for r in frappe.get_all("Loan Repayment", filters={"against_loan": loan.name, "docstatus": 1},
			fields=["value_date", "amount_paid", "is_imported", "payment_account", "reference_number", "mode_of_payment"],
			order_by="value_date"):
		if credit and r.payment_account == credit:
			continue  # paid from credit: the receipt is already listed when it came in
		ref = str(r.reference_number or "")
		before = r.is_imported or ref.startswith("HP-OPEN-PAID")
		label = _("Payments made before migration") if before else (
			_("Early settlement") if ref.startswith("HP-SETTLE") else _("Payment received"))
		method = _("Before migration (MYOB)") if before else payment_method(r.payment_account, r.mode_of_payment)
		events.append((r.value_date, label, 0, r.amount_paid, method, "payment", ref))
	for a in frappe.get_all("Loan Balance Adjustment", filters={"loan": loan.name, "docstatus": 1},
			fields=["posting_date", "adjustment_type", "amount", "reference_number"], order_by="posting_date"):
		ref = str(a.reference_number or "")
		if a.adjustment_type == "Debit Adjustment":
			label = _("Default charges brought forward from MYOB") if ref.endswith("-OPEN") else _("Default charge")
			events.append((a.posting_date, label, a.amount, 0, None, "charge"))
		else:
			label = _("Default charge reversed") if ref.startswith("HP-PEN-REV") else (
				_("Early settlement rebate (Rule of 78)") if ref.startswith("HP-REBATE") else _("Balance adjustment"))
			events.append((a.posting_date, label, 0, a.amount, None, "adjustment"))
	for j in frappe.db.sql(
			"""SELECT je.posting_date, je.total_credit FROM `tabJournal Entry` je WHERE je.docstatus = 1
			   AND je.custom_hp_loan = %s AND je.cheque_no LIKE %s""", (loan.name, "HP-REBATE-REFUND-%"), as_dict=True):
		events.append((j.posting_date, _("Early settlement rebate (Rule of 78), refund due to you"), 0, j.total_credit, None, "adjustment"))
	if credit:
		rows = frappe.db.sql(
			"""SELECT je.name, je.posting_date, je.cheque_no, jea.credit, jea.debit,
			          (SELECT o.account FROM `tabJournal Entry Account` o WHERE o.parent = je.name AND o.account != jea.account
			           AND o.debit > 0 ORDER BY o.idx LIMIT 1) AS received_into
			   FROM `tabJournal Entry` je
			   JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
			   WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND jea.account = %s ORDER BY je.posting_date""",
			(loan.name, credit), as_dict=True)
		for j in rows:
			key = str(j.cheque_no or "")
			if j.credit:
				opening = key.startswith("HP-OPEN-CREDIT")
				label = _("Paid ahead at migration from MYOB") if opening else _("Payment received (ahead of instalments)")
				method = _("Before migration (MYOB)") if opening else payment_method(j.received_into)
				events.append((j.posting_date, label, 0, j.credit, method, "payment", key.removesuffix("-CR")))
			if j.debit:
				events.append((j.posting_date, _("Credit transferred for refund to you"), j.debit, 0, None, "refund"))
	return events


def _schedule_facts(loan_name):
	rows = frappe.db.sql(
		"""SELECT rs.payment_date, rs.total_payment FROM `tabRepayment Schedule` rs
		   JOIN `tabLoan Repayment Schedule` lrs ON lrs.name = rs.parent
		   WHERE lrs.loan = %s AND lrs.docstatus = 1 AND lrs.status = 'Active' ORDER BY rs.payment_date""",
		(loan_name,), as_dict=True)
	if not rows:
		return {"instalment": None, "count": 0, "maturity": None}
	return {"instalment": m.money(rows[0].total_payment), "count": len(rows), "maturity": rows[-1].payment_date}


def _deposit(app):
	if not app or m.money(app.get("custom_flexi_deposit_amount")) <= 0:
		return None
	deposit = {"date": app.get("posting_date"), "amount": app.get("custom_flexi_deposit_amount"), "method": _("Paid at sale")}
	entry = app.get("custom_hp_deposit_payment_entry")
	if entry:
		pe = frappe.db.get_value("Payment Entry", entry, ["posting_date", "paid_amount", "paid_to", "mode_of_payment", "docstatus"], as_dict=True)
		if pe and pe.docstatus == 1:
			deposit.update(date=pe.posting_date, amount=pe.paid_amount, method=payment_method(pe.paid_to, pe.mode_of_payment))
	return deposit


def _flat_rate(loan, app):
	for value in ((app.get("custom_flexi_interest_rate") if app else None), loan.get("custom_hp_contract_rate"), 22):
		try:
			if value not in (None, "") and float(value):
				return float(value)
		except (TypeError, ValueError):
			continue
	return None


def statement_data(loan_name, from_date=None, to_date=None):
	loan = frappe.get_doc("Loan", loan_name)
	app = frappe.get_doc("Loan Application", loan.loan_application) if loan.get("loan_application") else None
	to_date = getdate(to_date or today())
	from_date = getdate(from_date or loan.posting_date or to_date)
	events = _events(loan)
	figures = build_lines(events, from_date, to_date)
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
	customer = frappe.db.get_value("Customer", loan.applicant, ["customer_name", "customer_primary_address", "email_id", "mobile_no", "tax_id"], as_dict=True) or {}
	address = frappe.db.get_value("Address", customer.get("customer_primary_address"),
		["address_line1", "address_line2", "city", "state", "country"], as_dict=True) if customer.get("customer_primary_address") else None
	schedule = _schedule_facts(loan.name)
	contract_total = sum((m.d(e[2]) for e in events if len(e) > 5 and e[5] == "contract"), m.d(0))
	settled = frappe.db.exists("Loan Repayment", {"against_loan": loan.name, "docstatus": 1, "reference_number": ["like", "HP-SETTLE%"]})
	status_label, arrears = account_status(loan.status, overdue, loan.get("days_past_due"), bool(settled))
	contract_date = getdate(loan.posting_date) if loan.posting_date else None
	from comfort_homes.hp_lending.hp_credit import credit_balance
	return {
		"loan": loan.name, "application": loan.get("loan_application"), "status": loan.status,
		# 7.1.4: contract breakdown, account status, full payment history, total paid including the deposit
		"status_label": status_label, "arrears": arrears, "days_past_due": int(loan.get("days_past_due") or 0),
		"contract": contract_summary(app.as_dict() if app else {}, contract_total),
		"flat_rate": _flat_rate(loan, app), "instalment": schedule["instalment"], "instalments": schedule["count"],
		"maturity_date": schedule["maturity"], "credit_held": credit_balance(loan.name),
		"history": payment_history(events, _deposit(app), to_date),
		"show_opening": not (contract_date and from_date <= contract_date),
		"tin": customer.get("tax_id"),
		"converted": bool(app.get("custom_hp_converted")) if app else bool(loan.get("custom_hp_converted")),
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
