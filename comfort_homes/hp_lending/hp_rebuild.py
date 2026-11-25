"""7.1: rebuild the schedule of a submitted converted MYOB loan in place, keeping the loan's name.

Why: a converted loan loaded with the wrong frequency, number of instalments or first instalment date cannot
be edited once submitted (those fields are not "allow on submit"), and cancel-and-amend would rename it.

How an imported loan is held (Lending 16.8.2): the schedule belongs to an imported Loan Disbursement; the Loan's
own opening GL (Dr 1101 / Cr 1910) is one total, the principal still owed, which a new instalment pattern does
not change. Imported disbursements, imported demands and imported repayments post no GL, and principal demands
never do. So the rebuild touches the ledger only where the new schedule changes the MYOB paid-ahead amount
(customer credit, Dr 1910 / Cr 2017).

Steps, one transaction per loan (a preview runs every step and rolls back):
  1. refuse a loan with activity since cut-over (receipts, credit used, charges, adjustments other than the
     MYOB opening ones, paid demands): those need a person first;
  2. cancel the unpaid principal demands, the paid-to-date import record, the opening credit journal and the
     imported disbursement (Lending cancels its schedule);
  3. set the new frequency, number of instalments and first instalment date on the Loan;
  4. import a new disbursement with the same amount and date (Lending builds the schedule), mark history
     before the migration date as Lending does, and work out from MYOB paid-to-date on the NEW schedule the
     overdue at cut-over, the paid ahead, the oldest unpaid instalment and DPD;
  5. post the opening arrears demand, the paid-to-date record, the paid-ahead credit and date the arrears, as
     on first load (hp_conversion), then verify the loan against MYOB; any difference rolls everything back.

MYOB money (contract total, balance, unearned interest, default interest, fees) is never changed.
"""
import json

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, now_datetime

from comfort_homes.hp_lending import hp_conversion as conv
from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

FREQUENCIES = {"Weekly": "Weekly", "Fortnightly": "Bi-Weekly", "Bi-Weekly": "Bi-Weekly", "Monthly": "Monthly"}
LIVE_STATUSES = ("Disbursed", "Partially Disbursed")


# ---------------------------------------------------------------------------
# Pure: the opening position on a schedule
# ---------------------------------------------------------------------------

def opening_position(loan_amount, myob_balance, rows, migration_date):
	"""MYOB paid-to-date laid over a schedule.

	``rows``: (payment_date, principal) in date order. History is every row dated before the migration date,
	as Lending marks it. Returns overdue at cut-over (Lending's opening principal), paid ahead, the oldest
	unpaid instalment date, DPD at migration and the principal paid against instalments already due."""
	migration_date = getdate(migration_date)
	paid = m.money(m.d(loan_amount) - m.money(myob_balance))
	due = m.money(sum((m.d(p) for d, p in rows if getdate(d) < migration_date), m.d(0)))
	if paid >= due:
		return dict(overdue=m.d(0), advance=m.money(paid - due), oldest=None, dpd=0, paid_against_due=due)
	cumulative, oldest = m.d(0), None
	for d, p in rows:
		cumulative += m.d(p)
		if cumulative > paid:
			oldest = getdate(d)
			break
	return dict(overdue=m.money(due - paid), advance=m.d(0), oldest=oldest,
		dpd=(migration_date - oldest).days if oldest else 0, paid_against_due=paid)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def blockers(doc):
	"""Reasons this loan cannot be rebuilt automatically (empty list = OK)."""
	name, out = doc.name, []
	if doc.company != s.COMPANY or not doc.get("custom_hp_converted") or not doc.get("is_imported"):
		return ["not a converted MYOB loan"]
	if doc.docstatus != 1:
		return ["not submitted (edit the draft instead)"]
	if doc.status not in LIVE_STATUSES:
		out.append(f"status is {doc.status}")
	if not frappe.has_permission("Loan Repayment Schedule", "cancel"):
		out.append("you need cancel rights on Loan Repayment Schedule (role HP Rebuild (Temporary))")
	allowed = {f"HP-OPEN-PAID-{name}"}
	for r in frappe.get_all("Loan Repayment", filters={"against_loan": name, "docstatus": 1},
			fields=["name", "reference_number", "repayment_type"], limit_page_length=0):
		if r.reference_number not in allowed:
			out.append(f"repayment {r.name} ({r.repayment_type}, {r.reference_number or 'no reference'})")
	for r in frappe.get_all("Loan Balance Adjustment", filters={"loan": name, "docstatus": 1},
			fields=["name", "reference_number"], limit_page_length=0):
		if r.reference_number != f"HP-PEN-{name}-OPEN":
			out.append(f"balance adjustment {r.name}")
	for r in frappe.get_all("Loan Demand", filters={"loan": name, "docstatus": 1, "paid_amount": [">", 0]},
			fields=["name"], limit_page_length=0):
		out.append(f"demand {r.name} is partly paid")
	for r in frappe.get_all("Loan Demand", filters={"loan": name, "docstatus": 1, "demand_subtype": ["!=", "Principal"]},
			fields=["name", "demand_type"], limit_page_length=0):
		out.append(f"{r.demand_type} demand {r.name}")
	credit_je = _opening_credit_entry(name)
	from comfort_homes.hp_lending.hp_credit import credit_account, credit_balance
	account = credit_account(required=False)
	if account:
		held = m.money(credit_balance(name, account))
		opening = m.money(frappe.db.get_value("Journal Entry", credit_je, "total_credit") if credit_je else 0)
		if held != opening:
			out.append(f"customer credit moved since cut-over (held {held}, opening {opening})")
	for j in frappe.get_all("Journal Entry", filters={"custom_hp_loan": name, "docstatus": 1},
			fields=["name", "custom_hp_entry_type"], limit_page_length=0):
		if j.custom_hp_entry_type not in (conv.OPENING, "Customer Credit"):
			out.append(f"journal {j.name} ({j.custom_hp_entry_type or 'no type'}) since cut-over")
	if frappe.db.count("Loan Disbursement", {"against_loan": name, "docstatus": 1}) != 1:
		out.append("more than one disbursement")
	return out


def _opening_credit_entry(loan):
	return frappe.db.get_value("Journal Entry", {"custom_hp_loan": loan, "docstatus": 1,
		"cheque_no": f"HP-OPEN-CREDIT-{loan}"}, "name")


def _cancel(doctype, name):
	doc = frappe.get_doc(doctype, name)
	doc.flags.ignore_links = True
	doc.flags.ignore_permissions = True  # gated: System Manager with cancel rights on the schedule (blockers)
	doc.cancel()
	return name


def _schedule_rows(loan, disbursement):
	schedule = frappe.db.get_value("Loan Repayment Schedule", {"loan": loan, "loan_disbursement": disbursement,
		"docstatus": 1, "status": "Active"}, "name")
	if not schedule:
		frappe.throw(_("Lending built no active schedule for {0}").format(loan))
	return [(r.payment_date, r.principal_amount) for r in frappe.get_all("Repayment Schedule",
		filters={"parent": schedule}, fields=["payment_date", "principal_amount"], order_by="payment_date asc, idx asc",
		limit_page_length=0)]


def _snapshot(doc):
	return dict(frequency=doc.repayment_frequency, periods=doc.repayment_periods,
		start=str(doc.repayment_start_date), advance=float(m.money(doc.get("custom_hp_opening_advance"))),
		oldest=str(doc.get("custom_hp_oldest_unpaid_date") or ""), dpd=doc.get("custom_hp_dpd_at_migration"),
		overdue=float(m.money(sum(m.d(r.opening_principal_outstanding) for r in doc.get("loan_import_details") or []))))


# ---------------------------------------------------------------------------
# One loan
# ---------------------------------------------------------------------------

def _rebuild(name, frequency, periods, start_date, source):
	doc = frappe.get_doc("Loan", name)
	problems = blockers(doc)
	if problems:
		return dict(loan=name, ok=False, done=False, problems=problems)
	if len(doc.get("loan_import_details") or []) != 1:
		return dict(loan=name, ok=False, done=False, problems=["expected one Loan Import Details row"])
	frequency = FREQUENCIES.get(frequency)
	periods, start_date = cint(periods), getdate(start_date)
	if not frequency or periods <= 0:
		return dict(loan=name, ok=False, done=False, problems=["frequency must be Weekly, Fortnightly or Monthly; periods > 0"])
	before = _snapshot(doc)
	migration = getdate(doc.migration_date)
	old_disbursement = frappe.db.get_value("Loan Disbursement", {"against_loan": name, "docstatus": 1, "is_imported": 1},
		["name", "disbursement_date", "disbursed_amount"], as_dict=True)
	if not old_disbursement:
		return dict(loan=name, ok=False, done=False, problems=["no imported disbursement"])
	cancelled = []

	# 2. cancel: unpaid demands (imported arrears and those raised since), paid-to-date record, opening credit, disbursement
	for d in frappe.get_all("Loan Demand", filters={"loan": name, "docstatus": 1}, pluck="name", order_by="demand_date desc"):
		cancelled.append(_cancel("Loan Demand", d))
	paid = frappe.db.get_value("Loan Repayment", {"against_loan": name, "docstatus": 1, "reference_number": f"HP-OPEN-PAID-{name}"}, "name")
	if paid:
		cancelled.append(_cancel("Loan Repayment", paid))
	credit = _opening_credit_entry(name)
	if credit:
		cancelled.append(_cancel("Journal Entry", credit))
	cancelled.append(_cancel("Loan Disbursement", old_disbursement.name))

	# 3. new terms
	row = doc.loan_import_details[0]
	frappe.db.set_value("Loan", name, {"repayment_frequency": frequency, "repayment_periods": periods,
		"repayment_start_date": start_date}, update_modified=False)
	frappe.db.set_value(row.doctype, row.name, {"loan_disbursement_id": None, "opening_principal_outstanding": 0},
		update_modified=False)
	doc = frappe.get_doc("Loan", name)

	# 4. new imported disbursement and schedule; history as Lending marks it; MYOB position on the new schedule
	from lending.loan_management.doctype.loan_demand.loan_demand import create_loan_demand
	from lending.loan_management.utils import update_repayment_schedule_demand_generated
	disbursement = doc.import_loan_disbursement(None, old_disbursement.disbursement_date, old_disbursement.disbursed_amount,
		doc.repayment_method, frequency, periods, start_date, False)
	update_repayment_schedule_demand_generated(loan=name, loan_disbursement=disbursement, to_date=migration, demand_generated=1)
	position = opening_position(doc.loan_amount, doc.custom_hp_opening_outstanding, _schedule_rows(name, disbursement), migration)
	frappe.db.set_value(row.doctype, row.name, "opening_principal_outstanding", float(position["overdue"]), update_modified=False)
	doc.validate_opening_principal(disbursement, float(position["overdue"]))
	if position["overdue"] > 0:
		create_loan_demand(loan=name, demand_date=migration, demand_type="EMI", demand_subtype="Principal",
			amount=float(position["overdue"]), loan_disbursement=disbursement, posting_date=migration, paid_amount=0, is_imported=1)
	frappe.db.set_value("Loan", name, {"custom_hp_opening_advance": float(position["advance"]),
		"custom_hp_oldest_unpaid_date": position["oldest"], "custom_hp_dpd_at_migration": position["dpd"]}, update_modified=False)

	# 5. the schedule-dependent opening postings, as on first load, then verify against MYOB
	doc = frappe.get_doc("Loan", name)
	previous = getattr(frappe.flags, "hp_conversion", None)
	frappe.flags.hp_conversion = True
	try:
		conv._post_opening_paid(doc)
		conv._post_opening_advance(doc)
		conv._date_opening_arrears(doc)
		if not doc.get("custom_hp_oldest_unpaid_date"):  # nothing overdue at cut-over: DPD back to 0
			from lending.loan_management.doctype.loan.loan import update_days_past_due_in_loans
			update_days_past_due_in_loans(loan_name=name, posting_date=getdate(), loan_product=doc.loan_product,
				force_update_dpd_in_loan=True)
	finally:
		frappe.flags.hp_conversion = previous
	check = conv.verify_loan(name)
	if not check["ok"]:
		frappe.throw(_("Rebuilt {0} does not agree with MYOB: {1}").format(name, "; ".join(check["problems"])))
	after = _snapshot(frappe.get_doc("Loan", name))
	return dict(loan=name, ok=True, done=False, before=before, after=after, cancelled=cancelled,
		disbursement=disbursement, source=source, check=check)


def _comment(result):
	b, a = result["before"], result["after"]
	text = (f"Schedule rebuilt in place (7.1 hp_rebuild, {now_datetime():%d-%m-%Y}). "
		f"Was: {b['frequency']} x{b['periods']} from {b['start']}, overdue at cut-over {b['overdue']:.2f}, "
		f"paid ahead {b['advance']:.2f}, oldest unpaid {b['oldest'] or '-'}. "
		f"Now: {a['frequency']} x{a['periods']} from {a['start']}, overdue at cut-over {a['overdue']:.2f}, "
		f"paid ahead {a['advance']:.2f}, oldest unpaid {a['oldest'] or '-'} ({a['dpd']} days). "
		f"Source: {result.get('source') or 'not given'}. Arrears and paid ahead recalculated from MYOB paid-to-date "
		f"on the new schedule; MYOB contract total and balance unchanged. Cancelled: {', '.join(result['cancelled'])}.")
	frappe.get_doc({"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "Loan",
		"reference_name": result["loan"], "content": text}).insert(ignore_permissions=True)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

@frappe.whitelist(methods=["POST"])
def rebuild_schedules(rows, dry_run=1):
	"""System Manager. ``rows``: JSON list of {loan, frequency (Weekly | Fortnightly | Monthly), periods,
	start_date, source}. Each loan is its own transaction: a preview (dry_run=1, default) runs every step and
	rolls back; a live run commits each loan that verifies and rolls back any that does not."""
	frappe.only_for("System Manager")
	rows = json.loads(rows) if isinstance(rows, str) else rows
	live = not cint(dry_run)
	frappe.db.commit()  # start clean: each loan below is committed or fully rolled back on its own
	results = []
	for r in rows or []:
		name = r.get("loan")
		try:
			result = _rebuild(name, r.get("frequency"), r.get("periods"), r.get("start_date"), r.get("source"))
			if result["ok"] and live:
				_comment(result)
				frappe.db.commit()
				result["done"] = True
			else:
				# Full rollback (not a savepoint): also drops jobs Lending queued for after commit.
				frappe.db.rollback()
		except Exception as e:
			frappe.db.rollback()
			result = dict(loan=name, ok=False, done=False, problems=[str(e)])
		results.append(result)
	return dict(dry_run=not live, loans=len(results), rebuilt=sum(1 for r in results if r["done"]),
		ok=sum(1 for r in results if r["ok"]), results=results)
