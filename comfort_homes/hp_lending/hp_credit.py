"""Customer credit, receipt allocation and early settlement (dev26, batch 3).

Customer credit
---------------
Money received beyond what is due is never turned into a shorter term or an
unscheduled principal reduction.  It is held per loan in **2017 HP Customer
Credit** (a liability): Dr Bank / Cr 2017, a Journal Entry tagged with the loan.
Every morning, after Lending has raised the day's demands, ``apply_credit`` pays
each due instalment from the credit with a native Normal Repayment whose payment
account is 2017, value-dated on the instalment's due date, so a customer who is
ahead never goes overdue and is never charged a default charge.

Credit balance of a loan = 2017 journal lines tagged to it (credit - debit)
minus Loan Repayments paid from 2017.

Bank receipts (``allocate_receipt``, called by the bank reconciliation)
----------------------------------------------------------------------
1. Instalments due on the value date (Loan Repayment from the bank).
2. Then open servicing-fee invoices linked to the loan, oldest first (Payment
   Entry).  Fee invoices never change the loan balance.
3. Anything left is held as customer credit.
A receipt that would clear the contract, after the Rule of 78 rebate and any
credit already held, is held in full as credit and flagged for the Finance
Manager to confirm the settlement (``confirm_settlement``).

Early settlement
----------------
``confirm_settlement`` (Finance Manager): books the rebate, pays the balance
from credit (Lending closes the loan) and moves any surplus to 2008 HP Refund
Payable for the refund.  ``refund_rebate_after_closure`` (Finance Manager): for
a loan already closed without a rebate, the rebate becomes a refund owed
(Dr 2013 / Cr 2008); the closure job then releases what is left as income.

Converted MYOB loans
--------------------
``move_opening_advances_to_credit`` (System Manager, preview first) cancels the
conversion's paid-ahead "Advance Payment" (Lending restores the original
schedule with it) and holds the same amount as customer credit.
"""

import json
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s

CREDIT = dict(account_number="2017", account_name="HP Customer Credit", parent_number="2100-2400")
REFUND_NUMBER = "2008"
ENTRY = "Customer Credit"
TOLERANCE = m.d("0.01")
FINANCE = ("Finance Manager", "System Manager")


def _account_by_number(number, required=True):
	name = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": number, "is_group": 0, "disabled": 0}, "name")
	if not name and required:
		frappe.throw(_("Account {0} is missing from the Chart of Accounts. Run HP Customer Credit setup first.").format(number))
	return name


def credit_account(required=True):
	return _account_by_number(CREDIT["account_number"], required)


def refund_account():
	return _account_by_number(REFUND_NUMBER)


@frappe.whitelist(methods=["POST"])
def setup_credit_account(dry_run=1):
	"""Create 2017 HP Customer Credit (liability, no account type) under Current Liabilities when missing."""
	frappe.only_for("System Manager")
	if credit_account(required=False):
		return {"exists": credit_account()}
	parent = frappe.db.get_value("Account", {"company": s.COMPANY, "account_number": CREDIT["parent_number"], "is_group": 1}, "name")
	if not parent:
		frappe.throw(_("Parent group {0} not found.").format(CREDIT["parent_number"]))
	plan = {"create": f"{CREDIT['account_number']} - {CREDIT['account_name']}", "parent": parent}
	if int(dry_run):
		return plan
	frappe.get_doc({"doctype": "Account", "company": s.COMPANY, "account_name": CREDIT["account_name"],
		"account_number": CREDIT["account_number"], "parent_account": parent, "is_group": 0,
		"root_type": "Liability", "report_type": "Balance Sheet", "account_currency": "FJD"}).insert()
	return {**plan, "created": credit_account()}


# ---------------------------------------------------------------- balances

def credit_balance(loan, account=None):
	account = account or credit_account(required=False)
	if not account:
		return m.d(0)
	held = frappe.db.sql(
		"""SELECT COALESCE(SUM(jea.credit - jea.debit), 0) FROM `tabJournal Entry Account` jea
		   JOIN `tabJournal Entry` je ON je.name = jea.parent
		   WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND jea.account = %s""", (loan, account))[0][0]
	used = frappe.db.sql(
		"""SELECT COALESCE(SUM(amount_paid), 0) FROM `tabLoan Repayment`
		   WHERE docstatus = 1 AND against_loan = %s AND payment_account = %s""", (loan, account))[0][0]
	return m.money(m.d(held) - m.d(used))


def due_outstanding(loan, on_date):
	"""Unpaid demands due on or before the end of ``on_date``."""
	return m.money(frappe.db.sql(
		"""SELECT COALESCE(SUM(outstanding_amount), 0) FROM `tabLoan Demand`
		   WHERE loan = %s AND docstatus = 1 AND outstanding_amount > 0 AND demand_date <= %s""",
		(loan, f"{getdate(on_date)} 23:59:59"))[0][0])


def pending_balance(loan):
	row = frappe.db.get_value("Loan", loan, ["total_payment", "total_principal_paid", "debit_adjustment_amount",
		"credit_adjustment_amount", "loan_amount"], as_dict=True)
	total = row.total_payment or row.loan_amount
	return m.money(m.d(total) - m.d(row.total_principal_paid) + m.d(row.debit_adjustment_amount) - m.d(row.credit_adjustment_amount))


def _last_value_date(loan):
	return frappe.db.sql("""SELECT MAX(value_date) FROM `tabLoan Repayment` WHERE against_loan = %s AND docstatus = 1""", (loan,))[0][0]


def get_datetime(value):
	if isinstance(value, datetime):
		return value
	if isinstance(value, date):
		return datetime.combine(value, datetime.min.time())
	text = str(value).strip()
	return datetime.fromisoformat(text[:19]) if len(text) > 10 else datetime.combine(date.fromisoformat(text), datetime.min.time())


def _not_before_last(loan, value):
	last = _last_value_date(loan)
	value = get_datetime(value)
	return max(value, get_datetime(last)) if last else value


# ---------------------------------------------------------------- postings

def hold_credit(loan, amount, debit_account, posting_date, key, remark, party=None):
	"""Dr ``debit_account`` / Cr 2017, tagged to the loan.  Idempotent on ``key``."""
	existing = frappe.db.get_value("Journal Entry", {"custom_hp_loan": loan.name, "cheque_no": key, "docstatus": 1}, "name")
	if existing:
		return existing
	bank = frappe.db.get_value("Account", debit_account, "account_type") in ("Bank", "Cash")
	je = frappe.get_doc({
		"doctype": "Journal Entry", "voucher_type": "Bank Entry" if bank else "Journal Entry", "company": s.COMPANY,
		"posting_date": getdate(posting_date), "cheque_no": key, "cheque_date": getdate(posting_date),
		"custom_hp_loan": loan.name, "custom_hp_entry_type": ENTRY, "user_remark": f"{remark} [{key}]",
		"accounts": [
			{"account": debit_account, "debit_in_account_currency": float(amount)},
			{"account": credit_account(), "credit_in_account_currency": float(amount)},
		],
	})
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


def _repayment(loan, amount, payment_account, value_date, key, bank_account=None, posting_date=None, cost_center=None):
	values = {
		"doctype": "Loan Repayment", "against_loan": loan.name, "company": loan.company,
		"posting_date": posting_date or value_date, "value_date": value_date, "amount_paid": float(amount),
		"repayment_type": "Normal Repayment", "reference_number": key, "reference_date": getdate(value_date),
		"payment_account": payment_account,
	}
	if bank_account:
		values["bank_account"] = bank_account
	if cost_center or loan.get("cost_center"):
		values["cost_center"] = cost_center or loan.cost_center
	repayment = frappe.get_doc(values)
	repayment.insert()
	repayment.submit()
	return repayment.name


def _open_fee_invoices(loan, customer):
	return frappe.get_all("Sales Invoice", filters={"docstatus": 1, "customer": customer, "custom_hp_independent_fee": 1,
		"custom_hp_reference_loan": loan, "outstanding_amount": [">", 0], "is_return": 0},
		fields=["name", "outstanding_amount", "debit_to"], order_by="posting_date asc, name asc", limit_page_length=0)


def _pay_fees(loan, customer, available, bank_ledger, posting_date, key, bank_account=None):
	invoices = _open_fee_invoices(loan.name, customer)
	references, total, debit_to = [], m.d(0), None
	for inv in invoices:
		if available - total <= 0:
			break
		part = min(m.money(inv.outstanding_amount), available - total)
		references.append({"reference_doctype": "Sales Invoice", "reference_name": inv.name, "allocated_amount": float(part)})
		total += part
		debit_to = debit_to or inv.debit_to
	if not references:
		return None, m.d(0)
	existing = frappe.db.get_value("Payment Entry", {"reference_no": key, "docstatus": 1}, "name")
	if existing:
		return existing, total
	pe = frappe.get_doc({
		"doctype": "Payment Entry", "payment_type": "Receive", "company": s.COMPANY, "posting_date": getdate(posting_date),
		"party_type": "Customer", "party": customer, "paid_from": debit_to, "paid_to": bank_ledger,
		"paid_amount": float(total), "received_amount": float(total), "reference_no": key,
		"reference_date": getdate(posting_date), "bank_account": bank_account, "references": references,
		"remarks": f"Servicing fees paid from bank receipt for {loan.name} [{key}]",
	})
	pe.insert()
	pe.submit()
	return pe.name, total


def payoff_figures(loan, as_of):
	"""Net amount that would settle the contract on ``as_of``: balance - rebate - credit held."""
	from comfort_homes.hp_lending.hp_settlement import settlement_figures
	balance = pending_balance(loan)
	try:
		rebate = m.d(settlement_figures(loan, as_of)["rebate"])
	except Exception:
		rebate = m.d(0)
	credit = credit_balance(loan)
	return {"balance": balance, "rebate": rebate, "credit": credit, "net": m.money(balance - rebate - credit)}


def allocate_receipt(loan_name, customer, amount, value_date, bank_ledger, key, bank_account=None, cost_center=None):
	"""Split one bank receipt: due instalments, then fees, then customer credit.  Returns what was posted."""
	loan = frappe.get_doc("Loan", loan_name)
	amount = m.money(amount)
	out = {"loan_repayment": None, "fee_payment": None, "credit_entry": None, "status": "", "allocation": ""}
	pay = payoff_figures(loan.name, value_date)
	if pay["balance"] > 0 and amount >= pay["net"] - TOLERANCE:
		out["credit_entry"] = hold_credit(loan, amount, bank_ledger, value_date, key,
			f"Payoff received for {loan.name}: held until the Finance Manager confirms the settlement")
		out["status"] = "Payoff - confirm settlement"
		out["allocation"] = f"{amount} held as credit; settlement {pay['net']} after rebate {pay['rebate']}"
		return out
	due = due_outstanding(loan.name, value_date)
	to_loan = m.money(min(amount, due))
	parts = []
	if to_loan > 0:
		out["loan_repayment"] = _repayment(loan, to_loan, bank_ledger, value_date, key, bank_account, cost_center=cost_center)
		parts.append(f"instalments {to_loan:.2f}")
	left = amount - to_loan
	if left > 0:
		out["fee_payment"], fees = _pay_fees(loan, customer, left, bank_ledger, value_date, key + "-FEE", bank_account)
		fees = m.money(fees)
		if fees:
			parts.append(f"fees {fees:.2f}")
		left = m.money(left - fees)
	if left > 0:
		out["credit_entry"] = hold_credit(loan, left, bank_ledger, value_date, key + "-CR",
			f"Received ahead of instalments for {loan.name}")
		parts.append(f"credit {left:.2f}")
	out["status"] = "Loan Repayment Created" if out["loan_repayment"] else ("Fees Paid" if out["fee_payment"] and not out["credit_entry"] else "Customer Credit Held")
	out["allocation"] = ", ".join(parts)
	return out


# ---------------------------------------------------------------- daily: pay instalments from credit

def _credit_loans(account):
	return frappe.db.sql_list(
		"""SELECT DISTINCT je.custom_hp_loan FROM `tabJournal Entry` je
		   JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
		   JOIN `tabLoan` l ON l.name = je.custom_hp_loan
		   WHERE je.docstatus = 1 AND jea.account = %s AND l.docstatus = 1
		     AND l.status IN ('Disbursed', 'Partially Disbursed')""", (account,))


def apply_credit(as_of=None):
	"""Scheduled 01:30 daily, after Lending's demand run.  Pays due instalments from customer credit."""
	account = credit_account(required=False)
	if not account:
		return []
	as_of = getdate(as_of or today())
	applied = []
	for name in _credit_loans(account):
		frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (name,))
		credit = credit_balance(name, account)
		if credit <= 0:
			continue
		demands = frappe.get_all("Loan Demand", filters={"loan": name, "docstatus": 1, "outstanding_amount": [">", 0],
			"demand_date": ["<=", f"{as_of} 23:59:59"]}, fields=["demand_date", "outstanding_amount"], order_by="demand_date asc")
		due = m.money(sum((m.d(d.outstanding_amount) for d in demands), m.d(0)))
		amount = min(credit, due)
		if amount <= 0:
			continue
		loan = frappe.get_doc("Loan", name)
		last_credit = frappe.db.sql("""SELECT MAX(je.posting_date) FROM `tabJournal Entry` je
			JOIN `tabJournal Entry Account` jea ON jea.parent = je.name
			WHERE je.docstatus = 1 AND je.custom_hp_loan = %s AND jea.account = %s AND jea.credit > 0""", (name, account))[0][0]
		value = get_datetime(demands[0].demand_date)
		if last_credit and get_datetime(f"{last_credit} 00:00:00") > value:
			value = get_datetime(f"{last_credit} 23:59:59")
		value = _not_before_last(name, value)
		count = frappe.db.count("Loan Repayment", {"against_loan": name, "payment_account": account})
		_repayment(loan, amount, account, value, f"HP-CREDIT-{name}-{count + 1}", posting_date=as_of)
		applied.append(name)
	return applied


# ---------------------------------------------------------------- settlement (Finance Manager)

def _transfer_to_refund(loan, amount, posting_date, key, remark, debit_account):
	existing = frappe.db.get_value("Journal Entry", {"custom_hp_loan": loan.name, "cheque_no": key, "docstatus": 1}, "name")
	if existing:
		return existing
	je = frappe.get_doc({
		"doctype": "Journal Entry", "voucher_type": "Journal Entry", "company": s.COMPANY,
		"posting_date": getdate(posting_date), "cheque_no": key, "cheque_date": getdate(posting_date),
		"custom_hp_loan": loan.name, "custom_hp_entry_type": ENTRY if debit_account == credit_account(False) else "Rebate",
		"user_remark": f"{remark} [{key}]",
		"accounts": [
			{"account": debit_account, "debit_in_account_currency": float(amount)},
			{"account": refund_account(), "credit_in_account_currency": float(amount),
				"party_type": "Customer", "party": loan.applicant},
		],
	})
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


@frappe.whitelist()
def settlement_position(loan, as_of=None):
	"""Read: balance, rebate, credit held and what the customer still owes (or is owed) to settle."""
	frappe.only_for(("Accounts Manager", "Accounts Officer", "Finance Manager", "Loan Manager", "System Manager"))
	frappe.get_doc("Loan", loan).check_permission("read")
	as_of = getdate(as_of or today())
	pay = payoff_figures(loan, as_of)
	status = frappe.db.get_value("Loan", loan, "status")
	return {"loan": loan, "as_of": str(as_of), "status": status, "balance": float(pay["balance"]),
		"rebate": float(pay["rebate"]), "credit": float(pay["credit"]), "to_pay": float(max(pay["net"], m.d(0))),
		"refund_after_settlement": float(max(-pay["net"], m.d(0)))}


@frappe.whitelist(methods=["POST"])
def confirm_settlement(loan, as_of=None):
	"""Finance Manager: book the Rule of 78 rebate, settle from credit, move any surplus to HP Refund Payable."""
	frappe.only_for(FINANCE)
	from comfort_homes.hp_lending.hp_settlement import book_rebate, settlement_figures
	frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan,))
	doc = frappe.get_doc("Loan", loan)
	if doc.status not in ("Disbursed", "Partially Disbursed"):
		frappe.throw(_("{0} is {1}; only an open loan can be settled here.").format(loan, doc.status))
	account = credit_account()
	as_of = getdate(as_of or frappe.db.sql("""SELECT MAX(je.posting_date) FROM `tabJournal Entry` je
		JOIN `tabJournal Entry Account` jea ON jea.parent = je.name WHERE je.docstatus = 1 AND je.custom_hp_loan = %s
		AND jea.account = %s AND jea.credit > 0""", (loan, account))[0][0] or today())
	figures = settlement_figures(loan, as_of)
	credit = credit_balance(loan, account)
	payable = m.money(pending_balance(loan) - m.d(figures["rebate"]))
	if credit + TOLERANCE < payable:
		frappe.throw(_("Credit held {0} is short of the settlement amount {1} (rebate {2}) by {3}.").format(
			credit, payable, figures["rebate"], payable - credit), title=_("Not Enough To Settle"))
	book_rebate(loan, figures)
	payable = pending_balance(loan)
	value = _not_before_last(loan, f"{as_of} 23:59:59")
	repayment = _repayment(doc, payable, account, value, f"HP-SETTLE-PAY-{loan}", posting_date=today()) if payable > 0 else None
	surplus = credit_balance(loan, account)
	refund = _transfer_to_refund(doc, surplus, today(), f"HP-CREDIT-REFUND-{loan}",
		f"Credit left after settlement of {loan}: refund owed to the customer", account) if surplus > 0 else None
	return {"loan": loan, "as_of": str(as_of), "rebate": figures["rebate"], "settled": float(payable),
		"loan_repayment": repayment, "refund_owed": float(surplus), "refund_entry": refund,
		"status": frappe.db.get_value("Loan", loan, "status")}


@frappe.whitelist(methods=["POST"])
def refund_rebate_after_closure(loan):
	"""Finance Manager: a loan closed early without a rebate.  Dr 2013 / Cr 2008 for the rebate owed."""
	frappe.only_for(FINANCE)
	from comfort_homes.hp_lending.hp_closure import contract_maturity
	from comfort_homes.hp_lending.hp_settlement import settlement_figures
	frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan,))
	doc = frappe.get_doc("Loan", loan)
	if doc.status != "Closed" or not doc.closure_date:
		frappe.throw(_("{0} is not closed.").format(loan))
	if s.entry_exists("Rebate", loan, f"HP-REBATE-REFUND-{loan}"):
		frappe.throw(_("The rebate refund for {0} is already booked.").format(loan))
	maturity = contract_maturity(loan)
	if maturity and getdate(doc.closure_date) >= maturity:
		frappe.throw(_("{0} closed at or after contract maturity; no rebate is owed.").format(loan))
	figures = settlement_figures(loan, doc.closure_date)
	if figures["rebate"] <= 0:
		frappe.throw(_("No rebate is owed for {0}.").format(loan))
	name = _transfer_to_refund(doc, m.money(figures["rebate"]), doc.closure_date, f"HP-REBATE-REFUND-{loan}",
		f"Rule of 78 rebate owed on early payoff of {loan} [HP-REBATE-REFUND-{loan}]: {figures['periods_paid']} of {figures['periods']} months",
		s.ACC_DEF_INTEREST)
	return {"loan": loan, "rebate": figures["rebate"], "entry": name}


# ---------------------------------------------------------------- converted loans

@frappe.whitelist(methods=["POST"])
def move_opening_advances_to_credit(loans=None, dry_run=1):
	"""System Manager: replace the conversion's paid-ahead Advance Payment with customer credit."""
	frappe.only_for("System Manager")
	account = credit_account()
	opening = _account_by_number("1910")
	names = json.loads(loans) if isinstance(loans, str) and loans.startswith("[") else ([loans] if loans else None)
	filters = {"repayment_type": "Advance Payment", "docstatus": 1, "reference_number": ["like", "HP-OPEN-ADV-%"]}
	if names:
		filters["against_loan"] = ["in", names]
	out = []
	for adv in frappe.get_all("Loan Repayment", filters=filters, fields=["name", "against_loan", "amount_paid", "value_date"], limit_page_length=0):
		loan = frappe.get_doc("Loan", adv.against_loan)
		later = frappe.get_all("Loan Repayment", filters={"against_loan": loan.name, "docstatus": 1, "is_imported": 0,
			"name": ["!=", adv.name], "value_date": [">", adv.value_date]}, pluck="name")
		row = {"loan": loan.name, "advance": adv.name, "amount": adv.amount_paid, "later_repayments": later, "actions": []}
		if int(dry_run):
			row["actions"] = [f"cancel {adv.name} (restores the original schedule)", f"hold {adv.amount_paid} as customer credit"]
			out.append(row)
			continue
		frappe.db.savepoint("hp_move_adv")
		user = frappe.session.user
		try:
			frappe.set_user("Administrator")  # Lending cancels the reschedule with the repayment
			frappe.flags.hp_conversion = True
			frappe.get_doc("Loan Repayment", adv.name).cancel()
			row["actions"].append(f"cancelled {adv.name}")
			# The ad-hoc demand the advance raised (and paid) is open again after the cancel: it is not an instalment.
			day = getdate(adv.value_date)
			for demand in frappe.get_all("Loan Demand", filters={"loan": loan.name, "docstatus": 1, "is_imported": 0,
					"outstanding_amount": [">", 0], "demand_amount": adv.amount_paid,
					"demand_date": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]]}, pluck="name"):
				frappe.get_doc("Loan Demand", demand).cancel()
				row["actions"].append(f"cancelled demand {demand}")
			key = f"HP-OPEN-CREDIT-{loan.name}"
			row["credit_entry"] = hold_credit(loan, m.money(adv.amount_paid), opening, add_days(getdate(loan.migration_date), -1), key,
				f"MYOB paid-ahead balance of {loan.name} held as customer credit")
			row["actions"].append("credit held")
		except Exception as error:
			frappe.db.rollback(save_point="hp_move_adv")
			row["error"] = str(error)[:200]
		finally:
			frappe.flags.hp_conversion = None
			frappe.set_user(user)
		out.append(row)
	if int(dry_run):
		frappe.db.rollback()
	return out
