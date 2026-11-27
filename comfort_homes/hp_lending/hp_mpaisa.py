"""7.1.3 M-PAiSA reconciliation, on the existing Loan Bank Reconciliation screen.

The M-PAiSA merchant export (one sheet per merchant ID: the app and each store's QR code) is imported as it
is.  Each successful payment becomes a statement row matched to a customer by, in order, the reference the
customer typed (card number, application number, name) and then the paying mobile number, including numbers
remembered from earlier confirmed receipts.  Posting a row is the normal reconciliation posting, into the
M-PAiSA wallet account.

The wallet then clears itself: each "Bank Settlement" line settles the payments taken since the previous
settlement on that merchant ID (oldest first: M-PAiSA settles at a daily cut-off), net of the $0.40 fee per payment, into 1008.  One journal per settlement:
Dr 1008 (net settled) + Dr M-PAiSA Charges (fees) / Cr Wallet (gross).  A settlement is posted only when the
payments in the file add up to it exactly and all of them are on or after cut-over; anything else is listed
for Accounts to post by hand.  A payment left unmatched keeps the wallet short until it is posted, so the
wallet balance is the M-PAiSA money not yet booked to a customer.
"""

import re
from collections import defaultdict
from datetime import date, datetime

import frappe
from frappe import _
from frappe.utils import flt, getdate

from comfort_homes.hp_lending import hp_collections as c
from comfort_homes.hp_lending import hp_settings as s

REQUIRED_HEADERS = {"businessreferenceno", "aparty", "transaction_type", "amount", "logtime"}
PAYMENT_TYPES = {"payment", "qr pay", "external qr pay"}
SETTLEMENT_TYPES = {"bank settlement"}
SETTLEMENT_ENTRY = "M-PAiSA Settlement"
MOBILE_PATTERN = re.compile(r"\|\s*from\s+(\d{7,12})\s*$")


def _h(value):
	return re.sub(r"\s+", " ", str(value or "").strip().lower())


def is_statement(tables):
	return any(REQUIRED_HEADERS <= {_h(h) for h in table["headers"]} for table in tables)


def _when(value):
	if isinstance(value, datetime):
		return value
	if isinstance(value, date):
		return datetime(value.year, value.month, value.day)
	text = str(value or "").strip()
	for pattern in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y", "%Y-%m-%d"):
		try:
			return datetime.strptime(text, pattern)
		except ValueError:
			pass
	return None


def mobile_digits(value):
	"""The 7-digit Fiji mobile number, or "" (bank QR payers carry an account number instead)."""
	digits = re.sub(r"\D", "", str(value or ""))
	if digits.startswith("679") and len(digits) == 10:
		digits = digits[3:]
	return digits if len(digits) == 7 else ""


def statement_rows(tables):
	"""Every successful payment and settlement in the export, oldest first per merchant."""
	merchants = c.config()["mpaisa"]["merchants"]
	out = []
	for table in tables:
		index = {_h(h): i for i, h in enumerate(table["headers"])}
		if not REQUIRED_HEADERS <= set(index):
			continue
		for row_index, values in enumerate(table["rows"]):
			def get(key, values=values):
				i = index.get(key)
				return values[i] if i is not None and i < len(values) else None
			status = str(get("status") or "SUCCESS").strip().upper()
			kind = _h(get("transaction_type"))
			when = _when(get("logtime"))
			amount = flt(get("amount"), 2)
			if status != "SUCCESS" or not when or amount <= 0 or kind not in PAYMENT_TYPES | SETTLEMENT_TYPES:
				continue
			merchant = str(get("businessid") or "").strip()
			if merchant.endswith(".0"):
				merchant = merchant[:-2]
			fee = flt(get("transactionfees"), 2)
			out.append(frappe._dict(
				sheet=table["sheet"], row_index=row_index, id=str(get("transactiontype") or "").strip(),
				kind="settlement" if kind in SETTLEMENT_TYPES else "payment", when=when, amount=amount, fee=fee,
				net=flt(get("netamount"), 2) or flt(amount - fee, 2), mobile=str(get("aparty") or "").strip(),
				reference=str(get("businessreferenceno") or "").strip(), merchant=merchant,
				merchant_label=merchants.get(merchant) or (f"M-PAiSA {merchant}" if merchant else "M-PAiSA")))
	out.sort(key=lambda r: (r.merchant, r.when, r.kind != "payment"))
	return out


def description(row):
	mobile = re.sub(r"\D", "", row.mobile)
	return f"{row.reference or 'No reference'} | {row.merchant_label} | from {mobile}" if mobile else \
		f"{row.reference or 'No reference'} | {row.merchant_label}"


def customer_by_mobile(mobile):
	"""A unique customer for the mobile number; with several, the one with an active loan."""
	digits = mobile_digits(mobile)
	if not digits:
		return ""
	names = {r.name for r in frappe.get_all("Customer", filters={"mobile_no": ["like", f"%{digits}"]},
		fields=["name", "mobile_no"], limit_page_length=50) if mobile_digits(r.mobile_no) == digits}
	if frappe.db.has_column("Customer", "custom_hp_payer_mobiles"):
		names |= set(frappe.get_all("Customer", filters={"custom_hp_payer_mobiles": ["like", f"%{digits}%"]}, pluck="name", limit_page_length=50))
	if len(names) > 1:
		active = {r.applicant for r in frappe.get_all("Loan", filters={"applicant": ["in", list(names)], "docstatus": 1,
			"status": ["in", ["Disbursed", "Partially Disbursed"]]}, fields=["applicant"], limit_page_length=50)}
		names = active if active else names
	return next(iter(names)) if len(names) == 1 else ""


def match(row):
	"""(customer, loan, number of active loans) for one payment."""
	from comfort_homes import api

	customer, loan, count = "", "", 0
	reference = row.reference or ""
	if reference and not re.fullmatch(r"counter\s*\d*", reference.strip(), re.IGNORECASE):
		text = api.normalise_narration(reference)
		customer, loan, count, how = api._customer_and_loan(text, api.extract_card_id(text), detail=True)
		if how == "check":
			return customer, loan, -1  # 7.1.5: converted application number only: the row is shown for checking
	if not customer:
		customer = customer_by_mobile(row.mobile)
		if customer:
			loans = api._loan_matches(customer)
			loan, count = (loans[0].name if len(loans) == 1 else ""), len(loans)
	return customer, loan, count


def statement_key(row):
	"""M-PAiSA transaction IDs are unique, so an overlapping export never posts a payment twice."""
	return "HP-MPAISA-" + row.id if row.id else ""


def _require_wallet(doc):
	from comfort_homes import api

	wallet = c.wallet_account()
	if not wallet or api._payment_account(doc.bank_account, doc.company or s.COMPANY) != wallet:
		frappe.throw(_("This is an M-PAiSA statement: choose the M-PAiSA wallet bank account on the reconciliation "
			"(run collections setup first if it does not exist)."), title=_("M-PAiSA statement"))
	return wallet


def import_rows(doc, tables, statement_hash):
	"""import_statement for an M-PAiSA export."""
	from comfort_homes import api

	_require_wallet(doc)
	cutover = c.cutover_date()
	doc.transactions = []
	imported = ready = review = 0
	seen = {}
	for row in statement_rows(tables):
		if row.kind != "payment":
			continue
		key = statement_key(row) or api._statement_key(doc.bank_account, statement_hash, row.sheet, row.row_index)
		values = {"statement_transaction_key": key, "transaction_date": row.when.date(), "description": description(row),
			"amount": row.amount, "customer_card_id": api.extract_card_id(api.normalise_narration(row.reference))}
		if row.when.date() < cutover and not api.include_pre_cutover(doc):
			values.update(status="Before cut-over (MYOB)", selected=0)
			review += 1
		else:
			customer, loan, count = match(row)
			status = "Ready" if loan else "Choose Loan" if customer and count > 1 else "No Disbursed Loan" if customer else "Unmatched"
			if loan and count == -1:
				status = api.CHECK_CUSTOMER
			# 7.1.4: a payment already dealt with in an earlier (overlapping) export is never ticked again
			ident = (str(row.when.date()), round(float(row.amount), 2), api.line_identity(values["description"]))
			occurrence = seen.get(ident, 0)
			seen[ident] = occurrence + 1
			done = api.already_reconciled(doc.name, doc.bank_account, row.when.date(), row.amount, values["description"], occurrence)
			duplicate = "" if done else (api._possible_duplicate(loan, row.when.date(), row.amount, key) if loan else "")
			if done:
				status = done
			elif duplicate:
				status = f"Possible Duplicate of {duplicate}"
			selected = int(bool(loan) and not duplicate and not done and status != api.CHECK_CUSTOMER)
			ready += selected
			review += int(not selected)
			values.update(customer=customer, suggested_loan=loan, loan=loan, selected=selected, status=status)
		doc.append("transactions", values)
		imported += 1
	doc.update({"status": "Imported", "imported_transactions": imported, "ready_transactions": ready, "review_transactions": review})
	doc.save()
	return {"imported": imported, "ready": ready, "review": review, "format": "M-PAiSA"}


def remember_mobile(customer, row_description):
	"""After a confirmed receipt, keep the payer's number on the customer for the next statement."""
	found = MOBILE_PATTERN.search(row_description or "")
	digits = mobile_digits(found.group(1)) if found else ""
	if not digits or not customer or not frappe.db.has_column("Customer", "custom_hp_payer_mobiles"):
		return
	current, known = frappe.db.get_value("Customer", customer, ["mobile_no", "custom_hp_payer_mobiles"]) or ("", "")
	if mobile_digits(current) == digits or digits in (known or ""):
		return
	frappe.db.set_value("Customer", customer, "custom_hp_payer_mobiles",
		", ".join(filter(None, [(known or "").strip(", "), digits])), update_modified=False)


def _oldest_run(queue, amount):
	"""(start, size, net) of the earliest run of consecutive payments whose net adds up to ``amount``."""
	for start in range(len(queue)):
		running = 0.0
		for size, payment in enumerate(queue[start:], 1):
			running = flt(running + payment.net, 2)
			if abs(running - amount) <= 0.005:
				return start, size, running
			if running > amount + 0.005:
				break
	return None


def settlement_plan(rows, cutover):
	"""Pure: which settlements can be posted.  Returns (postable, skipped) with the batch totals.

	M-PAiSA settles the wallet balance at a daily cut-off, so a settlement pays out the OLDEST unsettled
	payments (net of fees) and payments made earlier that morning wait for the next one.  Each settlement is
	matched first-in first-out to the payments in the file; it is postable only if a run of the oldest
	unsettled payments adds up to it exactly and none of them is from before cut-over."""
	postable, skipped = [], []
	by_merchant = defaultdict(list)
	for row in rows:
		by_merchant[row.merchant].append(row)
	for items in by_merchant.values():
		queue = []
		for row in items:
			if row.kind == "payment":
				queue.append(row)
				continue
			found = _oldest_run(queue, row.amount)
			if not found:
				skipped.append(dict(row=row, net=0, fees=0, payments=0,
					reason="its payments are not all in this file (earlier export?); post it by hand"))
				continue  # the queue belongs to later settlements
			start, size, running = found
			# payments before ``start`` were settled before this file began: nothing left to match them to
			batch, queue = queue[start:start + size], queue[start + size:]
			entry = dict(row=row, net=running, fees=flt(sum(b.fee for b in batch), 2), payments=len(batch))
			before = [b for b in batch if b.when.date() < cutover]
			if before:
				entry["reason"] = (f"it settles {len(before)} payment(s) from before cut-over "
					f"({flt(sum(b.net for b in before), 2):.2f} net, MYOB) with {len(batch) - len(before)} after; post it by hand")
				skipped.append(entry)
			else:
				postable.append(entry)
	return postable, skipped


def post_settlements(doc):
	"""Called after creating repayments on an M-PAiSA reconciliation.  Idempotent per settlement."""
	from comfort_homes import api

	wallet = _require_wallet(doc)
	content, filename = api._file(doc)
	rows = statement_rows(api._tables(content, filename))
	postable, skipped = settlement_plan(rows, c.cutover_date())
	cfg = c.config()["mpaisa"]
	bank = c.account_by_number(cfg["settlement_account_number"])
	charges = c.account_by_number(cfg["charges_number"])
	if not bank or not charges:
		frappe.throw(_("Run collections setup: the M-PAiSA settlement or charges account is missing."))
	cost_center = frappe.get_cached_value("Company", s.COMPANY, "cost_center")
	posted = []
	for entry in postable:
		row = entry["row"]
		key = "HP-MPAISA-SET-" + (row.id or f"{row.merchant}-{row.when:%Y%m%d%H%M%S}")
		existing = frappe.db.get_value("Journal Entry", {"cheque_no": key, "docstatus": 1}, "name")
		if existing:
			continue
		accounts = [{"account": bank, "debit_in_account_currency": row.amount}]
		if entry["fees"]:
			accounts.append({"account": charges, "debit_in_account_currency": entry["fees"], "cost_center": cost_center})
		accounts.append({"account": wallet, "credit_in_account_currency": flt(row.amount + entry["fees"], 2)})
		je = frappe.get_doc({"doctype": "Journal Entry", "voucher_type": "Bank Entry", "company": s.COMPANY,
			"posting_date": row.when.date(), "cheque_no": key, "cheque_date": row.when.date(),
			"custom_hp_entry_type": SETTLEMENT_ENTRY,
			"user_remark": f"M-PAiSA settlement {row.id} ({row.merchant_label}): {entry['payments']} payments, "
				f"fees {entry['fees']:.2f}, settled {row.amount:.2f} to {bank} [{key}]",
			"accounts": accounts})
		je.insert(ignore_permissions=True)
		je.submit()
		posted.append(je.name)
	cutover = c.cutover_date()
	return {"settlements_posted": posted, "settlements_skipped": [  # settlements before cut-over are MYOB's
		f"{e['row'].when:%d/%m/%Y} {e['row'].merchant_label} {e['row'].amount:.2f}: {e['reason']}"
		for e in skipped if e["row"].when.date() >= cutover]}
