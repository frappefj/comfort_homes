"""Bank-statement import and Loan Repayment actions for Comfort Homes."""

import csv
import hashlib
import re
from datetime import date, datetime
from io import BytesIO, StringIO

import frappe
from frappe import _
from frappe.utils import flt, getdate, today


DEFAULT_COMPANY = "Comfort Home Furnishing PTE Limited"
CARD_PREFIXES = ("GRA", "NAM", "LTK", "NAU", "DEN", "NOU", "SC")
LOAN_APPLICATION_PATTERN = re.compile(r"\bACC-LOAP-\d{4}-\d+\b", re.IGNORECASE)
# HP account numbers in the year-sequence form used since 2026, e.g. 2026-00366.
HP_ACCOUNT_PATTERN = re.compile(r"\b\d{4}-\d{3,6}\b")
# Every prefixed HP account / Card ID in a narration (extract_card_id returns only the first).
CARD_ID_PATTERN = re.compile(r"\b(?:" + "|".join(CARD_PREFIXES) + r")[A-Z0-9]{4,}\b")
# dev20: narrations typed by customers - "LTK 2026078", "ACC- LOAP 2026-00310", "ACCLOAP202600264".
SPACED_CARD_ID_PATTERN = re.compile(r"\b(" + "|".join(CARD_PREFIXES) + r")[ \-]+(\d{6,})\b", re.IGNORECASE)
LOOSE_APPLICATION_PATTERN = re.compile(r"\bACC[\s\-]*LOA[PD][\s\-]*(\d{4})[\s\-]*(\d{3,6})\b", re.IGNORECASE)


def normalise_narration(description):
	"""Join a Card ID / HP account written with a space or dash after its prefix, and rewrite
	loosely typed application references to ACC-LOAP-YYYY-NNNNN.  Used for matching only;
	the statement row keeps the bank's original description."""
	text = str(description or "")
	text = LOOSE_APPLICATION_PATTERN.sub(lambda m: f"ACC-LOAP-{m.group(1)}-{m.group(2)}", text)
	return SPACED_CARD_ID_PATTERN.sub(lambda m: m.group(1).upper() + m.group(2), text)


def _possible_duplicate(loan, transaction_date, amount, key):
	"""A submitted repayment already on this loan for the same day and amount from a different
	statement row (for example an overlapping statement export)."""
	rows = frappe.db.sql(
		"""SELECT name FROM `tabLoan Repayment`
		   WHERE against_loan = %s AND docstatus = 1 AND DATE(value_date) = %s
		   AND ABS(amount_paid - %s) < 0.005 AND IFNULL(reference_number, '') != %s
		   LIMIT 1""",
		(loan, transaction_date, amount, key or ""),
	)
	return rows[0][0] if rows else ""


def line_identity(description):
	"""Pure: a statement narration compared without case or spacing differences (7.1.4)."""
	return re.sub(r"\s+", " ", str(description or "")).strip().upper()


def occurrence_index(rows, row):
	"""Pure: 0 for the first statement line with this date, amount and narration in ``rows``, 1 for the
	second identical line, and so on (two genuine identical payments on one day stay distinct)."""
	ident = (str(row.get("transaction_date")), round(float(row.get("amount") or 0), 2), line_identity(row.get("description")))
	count = 0
	for other in rows:
		if other is row:
			return count
		if (str(other.get("transaction_date")), round(float(other.get("amount") or 0), 2), line_identity(other.get("description"))) == ident:
			count += 1
	return count


def already_reconciled(doc_name, bank_account, transaction_date, amount, description, occurrence=0):
	"""7.1.4: where this statement line was already dealt with in another reconciliation, else "".

	The same bank line comes back in every overlapping export with a new row key, so it is recognised by
	bank account, date, amount and narration. A line counts as dealt with when the earlier row created a
	loan repayment, a customer-credit entry or a fee payment, or when Accounts journalled it by hand
	(unidentified receipts: a submitted Journal Entry on that date for that amount quoting the narration)."""
	ident = line_identity(description)
	if not ident or not transaction_date:
		return ""
	rows = frappe.db.sql(
		"""SELECT r.parent, r.idx, r.description, r.loan_repayment, r.credit_entry, r.fee_payment
		   FROM `tabLoan Bank Reconciliation Item` r JOIN `tabLoan Bank Reconciliation` p ON p.name = r.parent
		   WHERE r.parent != %s AND p.bank_account = %s AND r.transaction_date = %s AND ABS(r.amount - %s) < 0.005
		   ORDER BY p.creation, r.idx""",
		(doc_name or "", bank_account, transaction_date, amount), as_dict=True)
	by_parent = {}
	for r in rows:
		if line_identity(r.description) == ident:
			by_parent.setdefault(r.parent, []).append(r)
	for parent, same in by_parent.items():
		if occurrence < len(same):
			r = same[occurrence]
			if r.loan_repayment:
				return f"Already reconciled ({parent}): {r.loan_repayment}"
			if r.credit_entry:
				return f"Already held as credit ({parent}): {r.credit_entry}"
			if r.fee_payment:
				return f"Already paid fees ({parent}): {r.fee_payment}"
	text = str(description or "").strip()
	journals = frappe.db.sql(
		"""SELECT name FROM `tabJournal Entry` WHERE docstatus = 1 AND posting_date = %s
		   AND ABS(total_debit - %s) < 0.005 AND user_remark LIKE %s ORDER BY creation""",
		(transaction_date, amount, "%" + text.replace("%", "").replace("_", "\\_") + "%"))
	if occurrence < len(journals):
		return f"Already journalled: {journals[occurrence][0]}"
	return ""


# Customer fields that each hold an exact, per-customer identifier a bank narration can carry.
CUSTOMER_ID_FIELDS = ("custom_card_id", "custom_ch_account_reference_no", "custom_tin_number")
ALIASES = {
	"date": ("value date", "effective date", "transaction date", "date", "posted date"),
	"description": ("transaction description", "description", "operation", "narration", "details"),
	"credit": ("credit amount", "amount (credit)", "amount credit", "credit (fjd)", "credit", "deposit"),
	"amount": ("amount", "transaction amount"),
}


def _header(value):
	return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _amount(value):
	text = str(value or "").replace(",", "").replace("FJD", "").replace("$", "").strip()
	if not text:
		return 0
	try:
		return float(text.strip("() ")) * (-1 if text.startswith("(") else 1)
	except ValueError:
		return 0


def _date(value):
	if isinstance(value, datetime):
		return value.date()
	if isinstance(value, date):
		return value
	for pattern in ("%d/%m/%Y", "%d/%m/%y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
		try:
			return datetime.strptime(str(value or "").strip(), pattern).date()
		except ValueError:
			pass
	return None


def _mapping(headers):
	lookup = {_header(header): header for header in headers if header}
	return {key: next((lookup[value] for value in names if value in lookup), "") for key, names in ALIASES.items()}


def _table(rows, sheet):
	best = None
	for row_number, row in enumerate(rows[:30]):
		headers = [str(cell).strip() if cell is not None else "" for cell in row]
		non_blank = sum(bool(header) for header in headers)
		if non_blank < 2:
			continue
		mapping = _mapping(headers)
		score = sum(bool(mapping[key]) for key in ("date", "description", "credit", "amount"))
		candidate = (score, non_blank, row_number, headers, mapping)
		if not best or candidate[:2] > best[:2]:
			best = candidate
	if not best:
		return None
	_, _, row_number, headers, mapping = best
	return {"sheet": sheet, "headers": headers, "mapping": mapping, "rows": rows[row_number + 1 :]}


def _tables(content, filename):
	if (filename or "").lower().endswith((".xlsx", ".xlsm")):
		from openpyxl import load_workbook

		book = load_workbook(BytesIO(content), read_only=True, data_only=True)
		tables = [_table(list(sheet.iter_rows(values_only=True)), sheet.title) for sheet in book.worksheets]
	else:
		text = content.decode("utf-8-sig", errors="replace") if isinstance(content, bytes) else content
		try:
			dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
		except csv.Error:
			dialect = csv.excel
		tables = [_table(list(csv.reader(StringIO(text), dialect)), "Statement")]
	tables = [table for table in tables if table]
	if not tables:
		frappe.throw(_("No row with statement columns was found in the attached file."))
	return tables


def _file(doc):
	if not doc.statement_file:
		frappe.throw(_("Attach a CSV or Excel bank statement before importing."))
	name = frappe.db.get_value("File", {"file_url": doc.statement_file}, "name")
	if not name:
		frappe.throw(_("The attached statement file could not be found."))
	file_doc = frappe.get_doc("File", name)
	file_doc.check_permission("read")
	return file_doc.get_content(), file_doc.file_name or doc.statement_file


def extract_card_id(description):
	text = (description or "").upper()
	for prefix in CARD_PREFIXES:
		start = text.find(prefix)
		if start < 0:
			continue
		value = ""
		for character in text[start:]:
			if character.isalnum():
				value += character
			else:
				break
		if len(value) >= 7:
			return value
	return ""


def _normalise_name(value):
	return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def _customer_name_from_description(description):
	"""Return a customer only where the transfer-name match is unambiguous."""
	text = re.split(r"\bFROM\b", str(description or ""), flags=re.IGNORECASE)[-1]
	name = _normalise_name(re.sub(r"\b\d{5,}\b", "", text))
	words = [word for word in name.split() if word not in {"DIRECT", "TRANSFER", "FUND", "SCH"}]
	if len(words) < 2:
		return ""

	# Search by the most specific name word first, then require an exact
	# normalised full-name match. This avoids assigning repayments by a loose
	# partial name match.
	candidates = frappe.get_all(
		"Customer",
		filters={"disabled": 0, "customer_name": ["like", f"%{words[-1]}%"]},
		fields=["name", "customer_name"],
		limit_page_length=100,
	)
	matches = [row.name for row in candidates if _normalise_name(row.customer_name) == " ".join(words)]
	return matches[0] if len(matches) == 1 else ""


def _loan_matches(customer):
	filters = {"applicant": customer, "status": "Disbursed"}
	if frappe.db.has_column("Loan", "applicant_type"):
		filters["applicant_type"] = "Customer"
	return frappe.get_all(
		"Loan",
		filters=filters,
		fields=["name"],
		order_by="disbursement_date desc, modified desc",
		limit_page_length=20,
	)


def _customer_and_loan(description, card_id):
	# Prefer an explicit application reference: it is the most reliable bank
	# narration identifier and avoids any customer-name ambiguity.
	application_reference = next(iter(LOAN_APPLICATION_PATTERN.findall(description or "")), "")
	if application_reference and frappe.db.has_column("Loan", "loan_application"):
		loans = frappe.get_all(
			"Loan",
			filters={"loan_application": application_reference, "status": "Disbursed"},
			fields=["name", "applicant"],
			limit_page_length=2,
		)
		if len(loans) == 1:
			return loans[0].applicant, loans[0].name, 1

	# A statement can contain a customer Card ID, the HP account number (held on
	# converted MYOB customers in custom_ch_account_reference_no) or, as in BSP
	# statements, the customer's TIN. All are optional site customizations.
	identifiers = list(dict.fromkeys([card_id] + CARD_ID_PATTERN.findall((description or "").upper())
		+ HP_ACCOUNT_PATTERN.findall(description or "")
		+ re.findall(r"\b\d{5,}\b", description or "")))
	matched_customers = set()
	for fieldname in CUSTOMER_ID_FIELDS:
		if not frappe.db.has_column("Customer", fieldname):
			continue
		for identifier in identifiers:
			if not identifier:
				continue
			customers = frappe.get_all("Customer", filters={fieldname: identifier}, fields=["name"], limit_page_length=2)
			if len(customers) == 1:
				matched_customers.add(customers[0].name)

	# Two identifiers may appear in either order, for example a bank reference
	# and a TIN. Only use the exact-ID result when it identifies one customer.
	if len(matched_customers) == 1:
		customer = next(iter(matched_customers))
		loans = _loan_matches(customer)
		return customer, loans[0].name if len(loans) == 1 else "", len(loans)
	if len(matched_customers) > 1:
		return "", "", 0

	customer = _customer_name_from_description(description)
	if not customer:
		return "", "", 0
	loans = _loan_matches(customer)
	return customer, loans[0].name if len(loans) == 1 else "", len(loans)


@frappe.whitelist()
def analyse_statement(name):
	doc = frappe.get_doc("Loan Bank Reconciliation", name)
	doc.check_permission("read")
	content, filename = _file(doc)
	tables = _tables(content, filename)
	from comfort_homes.hp_lending import hp_mpaisa

	if hp_mpaisa.is_statement(tables):
		# 7.1.3: the M-PAiSA export has fixed columns; the import reads them itself.
		return {"file_name": filename, "format": "M-PAiSA", "columns": tables[0]["headers"],
			"mapping": {"date": "Logtime", "description": "BusinessReferenceNo", "credit": "", "amount": "Amount"},
			"sheets": [table["sheet"] for table in tables]}
	return {"file_name": filename, "columns": tables[0]["headers"], "mapping": tables[0]["mapping"], "sheets": [table["sheet"] for table in tables]}


def _payment_account(bank_account, company=None):
	bank = frappe.db.get_value("Bank Account", bank_account, ["account", "disabled", "is_company_account", "company"], as_dict=True)
	if not bank or bank.disabled or not bank.is_company_account or not bank.account:
		frappe.throw(_("Select an active company Bank Account with a linked ledger account."))
	if company and bank.company != company:
		frappe.throw("Bank Account belongs to a different company")
	return bank.account


def _customer_with_email(email):
	"""Find a customer by its own or its primary-contact email address."""
	if not email:
		return ""
	if frappe.db.has_column("Customer", "email_id"):
		customers = frappe.get_all("Customer", filters={"email_id": email}, fields=["name"], limit_page_length=1)
		if customers:
			return customers[0].name
	contacts = frappe.get_all("Contact", filters={"email_id": email}, fields=["name"], limit_page_length=20)
	for contact in contacts:
		link = frappe.get_all(
			"Dynamic Link",
			filters={"parent": contact.name, "link_doctype": "Customer"},
			fields=["link_name"],
			limit_page_length=1,
		)
		if link:
			return link[0].link_name
	return ""


@frappe.whitelist(allow_guest=True)
def check_existing_customer(tin_number=None, email=None):
	"""Public Web Form pre-check; never exposes matched personal details."""
	match = _existing_customer_match(tin_number, email)
	# This endpoint is guest-accessible. It tells a public applicant only that
	# staff must handle an existing Co-Applicant; the details remain Desk-only.
	from comfort_homes.co_applicant import find_by_tin

	co_applicant = find_by_tin(tin_number)
	match["co_applicant_exists"] = bool(co_applicant)
	match["exists"] = match["exists"] or bool(co_applicant)
	return match


@frappe.whitelist()
def get_temporary_repayment_periods(loan_application=None, repayment_frequency=None):
	"""Return the app-owned temporary tenure mapping for an eligible Loan form.

	The browser asks the same Python calculation used by the document hooks, so
	it can only display a value the server will later persist.
	"""
	frappe.has_permission("Loan", "write", throw=True)
	if not loan_application:
		return {"enabled": False, "periods": None}
	frappe.has_permission("Loan Application", "read", doc=loan_application, throw=True)

	from comfort_homes.loan_mapping import (
		_application_repayment_periods,
		temporary_tenure_workaround_enabled,
	)

	if not temporary_tenure_workaround_enabled():
		return {"enabled": False, "periods": None}

	application = frappe.get_doc("Loan Application", loan_application)
	return {
		"enabled": True,
		"periods": _application_repayment_periods(application, repayment_frequency),
	}


def _existing_customer_match(tin_number=None, email=None):
	"""Return only whether supplied identity values are already held by a customer."""
	tin_number = str(tin_number or "").strip()
	email = str(email or "").strip().lower()
	matched_by = []
	if tin_number and frappe.db.has_column("Customer", "custom_tin_number"):
		if frappe.db.exists("Customer", {"custom_tin_number": tin_number}):
			matched_by.append("TIN Number")
	if email and _customer_with_email(email):
		matched_by.append("Email Address")
	return {"exists": bool(matched_by), "matched_by": matched_by}


@frappe.whitelist(methods=["POST"])
def log_loan_application_workflow_note(docname, action, from_state, to_state, note):
	"""Record an immutable workflow note in the Loan Application activity timeline."""
	doc = frappe.get_doc("Loan Application", docname)
	if not frappe.has_permission(doc.doctype, "write", doc=doc):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	note = str(note or "").strip()
	if not note:
		frappe.throw(_("A workflow note is required."))
	content = "<p><b>{0}</b> changed workflow: {1} → {2} ({3})</p><p>{4}</p>".format(
		frappe.utils.escape_html(frappe.session.user),
		frappe.utils.escape_html(from_state or ""),
		frappe.utils.escape_html(to_state or ""),
		frappe.utils.escape_html(action or ""),
		frappe.utils.escape_html(note),
	)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Workflow",
			"reference_doctype": "Loan Application",
			"reference_name": doc.name,
			"content": content,
		}
	).insert(ignore_permissions=True)
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def create_deposit_payment_entry(loan_application):
	"""Open (or create) the draft Payment Entry that records the customer's deposit.

	No Sales Invoice is raised for the deposit.  The Payment Entry receives the money
	into the bank account and holds it in Customer HP Deposits until disbursement,
	when the Gross HP booking clears it.  The entry is linked on the application,
	and the contract cannot move on until it is submitted (hp_lifecycle).
	"""
	from comfort_homes.hp_lending import hp_settings as hp

	application = frappe.get_doc("Loan Application", loan_application)
	application.check_permission("write")
	frappe.db.sql("SELECT name FROM `tabLoan Application` WHERE name = %s FOR UPDATE", (application.name,))
	application.reload()  # Re-read the receipt link after acquiring the retry/concurrency lock.
	if application.company != hp.COMPANY or application.applicant_type != "Customer":
		frappe.throw("Deposit action is restricted to Flexi customer applications")
	if not frappe.has_permission("Payment Entry", "create"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	if application.docstatus != 0 or application.get("workflow_state") not in ("Approved", "Contract Signing", "Awaiting Customer Signature"):
		frappe.throw("Record deposits after approval and before compliance submission")
	deposit = flt(application.get("custom_flexi_deposit_amount"))
	if deposit <= 0:
		frappe.throw(_("There is no deposit on this application."))
	if not application.applicant:
		frappe.throw(_("A Customer is required on the Loan Application before recording the deposit."))

	existing = application.get("custom_hp_deposit_payment_entry")
	if existing and frappe.db.get_value("Payment Entry", existing, "docstatus") != 2:
		from comfort_homes.hp_lending.hp_lifecycle import check_deposit
		check_deposit(application, require_submitted=False)
		return {"name": existing, "existing": True}

	company = application.company or hp.COMPANY
	# 7.1.3: a deposit is taken in store, so it defaults to that store's cash account and branch; staff can
	# change the receiving account before submitting (a transfer into the bank, say).
	from comfort_homes.hp_lending.hp_collections import store_cash_account

	branch = application.get("custom_branchlocation")
	bank = store_cash_account(branch) or frappe.db.get_value("Company", company, "default_bank_account")
	if not bank:
		frappe.throw("Configure the Company default bank before recording a deposit")
	entry = frappe.get_doc({
		"doctype": "Payment Entry",
		"payment_type": "Receive",
		"company": company,
		"posting_date": today(),
		"party_type": "Customer",
		"party": application.applicant,
		"paid_from": hp.account("ACC_DEPOSITS", company),
		"paid_to": bank,
		"paid_amount": deposit,
		"received_amount": deposit,
		"reference_no": application.name,
		"reference_date": today(),
		"remarks": _("Hire purchase deposit for {0}").format(application.name),
		"cost_center": branch or None,
	})
	entry.insert()
	application.db_set("custom_hp_deposit_payment_entry", entry.name, update_modified=False)
	return {"name": entry.name, "existing": False}


@frappe.whitelist(methods=["POST"])
def loan_reconciliation(name, action, mapping=None, row_names=None):
	doc = frappe.get_doc("Loan Bank Reconciliation", name)
	doc.check_permission("write")
	if doc.docstatus:
		frappe.throw(_("Only a draft reconciliation can be changed."))
	if action == "import_statement":
		return import_statement(doc, frappe.parse_json(mapping) if mapping else None)
	if action == "create_repayments":
		return create_repayments(doc, frappe.parse_json(row_names) if row_names else None)
	frappe.throw(_("A valid reconciliation action is required."))


def import_statement(doc, mapping):
	doc.check_permission("write")
	if any(row.loan_repayment or row.get("credit_entry") or row.get("fee_payment") for row in doc.transactions):
		frappe.throw(_("Create a new reconciliation for another import after repayments have been created."))
	content, filename = _file(doc)
	tables = _tables(content, filename)
	statement_hash = hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()
	from comfort_homes.hp_lending import hp_mpaisa
	from comfort_homes.hp_lending.hp_collections import cutover_date

	if hp_mpaisa.is_statement(tables):
		return hp_mpaisa.import_rows(doc, tables, statement_hash)
	cutover = cutover_date()
	mapping = mapping or tables[0]["mapping"]
	if not mapping.get("date") or not mapping.get("description") or not (mapping.get("credit") or mapping.get("amount")):
		frappe.throw(_("Choose transaction date, description, and credit or amount columns."))
	doc.transactions = []
	imported = ready = review = 0
	seen = {}  # 7.1.4: identical lines in this file, for already_reconciled
	for table in tables:
		for row_index, values in enumerate(table["rows"]):
			row = {header: values[index] if index < len(values) else "" for index, header in enumerate(table["headers"])}
			transaction_date = _date(row.get(mapping["date"]))
			description = str(row.get(mapping["description"]) or "").strip()
			amount = _amount(row.get(mapping.get("credit"))) if mapping.get("credit") else _amount(row.get(mapping.get("amount")))
			if not transaction_date or not description or amount <= 0:
				continue
			if transaction_date < cutover:
				# 7.1.3: money received before cut-over is in the MYOB balances already.
				doc.append("transactions", {"statement_transaction_key": _statement_key(doc.bank_account, statement_hash, table["sheet"], row_index),
					"transaction_date": transaction_date, "description": description, "amount": amount, "selected": 0,
					"status": "Before cut-over (MYOB)"})
				imported += 1
				review += 1
				continue
			matching_text = normalise_narration(description)
			card_id = extract_card_id(matching_text)
			customer, loan, count = _customer_and_loan(matching_text, card_id)
			status = "Ready" if loan else "Choose Loan" if customer and count > 1 else "No Disbursed Loan" if customer else "Unmatched"
			key = _statement_key(doc.bank_account, statement_hash, table["sheet"], row_index)
			ident = (str(transaction_date), round(amount, 2), line_identity(description))
			occurrence = seen.get(ident, 0)
			seen[ident] = occurrence + 1
			done = already_reconciled(doc.name, doc.bank_account, transaction_date, amount, description, occurrence)
			duplicate = "" if done else (_possible_duplicate(loan, transaction_date, amount, key) if loan else "")
			if done:
				status = done
			elif duplicate:
				status = f"Possible Duplicate of {duplicate}"
			selected = int(bool(loan) and not duplicate and not done)
			ready += selected; review += int(not selected)
			doc.append("transactions", {"statement_transaction_key": key, "transaction_date": transaction_date, "description": description, "amount": amount, "customer_card_id": card_id, "customer": customer, "suggested_loan": loan, "loan": loan, "selected": selected, "status": status})
			imported += 1
	doc.update({"status": "Imported", "imported_transactions": imported, "ready_transactions": ready, "review_transactions": review})
	doc.save()
	return {"imported": imported, "ready": ready, "review": review, "format": filename}


def _cost_center_for_loan(loan):
	"""Use the loan's own dimension, with its latest submitted disbursement as fallback."""
	cost_center = frappe.db.get_value("Loan", loan, "cost_center")
	if cost_center:
		return cost_center
	disbursements = frappe.get_all(
		"Loan Disbursement",
		filters={"against_loan": loan, "docstatus": 1},
		fields=["cost_center"],
		order_by="disbursement_date desc, modified desc",
		limit_page_length=1,
	)
	return disbursements[0].cost_center if disbursements else ""


def create_repayments(doc, row_names=None):
	doc.check_permission("write")
	frappe.has_permission("Loan Repayment", "create", throw=True)
	frappe.has_permission("Loan Repayment", "submit", throw=True)
	if doc.docstatus:
		frappe.throw("Only a draft reconciliation can be changed")
	# Serialise competing clicks before reloading the latest row links.
	frappe.db.sql("SELECT name FROM `tabLoan Bank Reconciliation` WHERE name = %s FOR UPDATE", (doc.name,))
	doc.reload()
	frappe.db.sql("SELECT name FROM `tabBank Account` WHERE name = %s FOR UPDATE", (doc.bank_account,))
	account = _payment_account(doc.bank_account, doc.company or DEFAULT_COMPANY)
	from comfort_homes.hp_lending import hp_collections, hp_mpaisa

	cutover = hp_collections.cutover_date()
	created = skipped = 0
	errors = []
	explicit_rows = set(row_names or [])
	for row in doc.transactions:
		# Ticked grid rows take precedence. Without ticks, retain bulk creation
		# for every automatically-ready row.
		if row.loan_repayment or row.get("credit_entry") or row.get("fee_payment") or (explicit_rows and row.name not in explicit_rows) or (not explicit_rows and not row.selected):
			continue
		if row.transaction_date and getdate(row.transaction_date) < cutover:
			row.update({"selected": 0, "status": "Before cut-over (MYOB)"})
			skipped += 1
			continue
		loan = row.loan or row.suggested_loan
		if not loan:
			row.status = "Choose Loan"; skipped += 1
			continue
		if not row.customer:
			row.update({"loan": None, "selected": 0, "status": "Unmatched"})
			skipped += 1
			continue
		loan_doc = frappe.get_doc("Loan", loan)
		loan_doc.check_permission("read")
		if loan_doc.applicant != row.customer or loan_doc.company != (doc.company or DEFAULT_COMPANY):
			row.update({"loan": None, "selected": 0, "status": "Choose Loan"})
			skipped += 1
			continue
		# 7.1.4: never post a statement line another reconciliation already dealt with, even if re-ticked.
		done = already_reconciled(doc.name, doc.bank_account, row.transaction_date, row.amount, row.description,
			occurrence_index(doc.transactions, row))
		if done:
			row.update({"selected": 0, "status": done})
			skipped += 1
			continue
		# A staff member can manually select a loan for a matched customer. That
		# valid selection is ready to post even if its earlier import status was
		# "Choose Loan".
		row.status = "Ready"
		frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (loan,))
		frappe.db.savepoint("hp_bank_row")
		try:
			key = row.get("statement_transaction_key") or "HP-ROW-" + hashlib.sha256((doc.name + ":" + row.name).encode()).hexdigest()[:40]
			existing = frappe.db.get_value("Loan Repayment", {"reference_number": key, "docstatus": ["!=", 2]}, ["name", "against_loan", "docstatus", "amount_paid", "value_date"], as_dict=True)
			if existing:
				if existing.against_loan != loan or existing.docstatus != 1 or flt(existing.amount_paid, 2) > flt(row.amount, 2) or getdate(existing.value_date) != getdate(row.transaction_date):
					frappe.throw("Bank row already has a different or draft repayment; review the existing record")
				row.update({"loan": loan, "loan_repayment": existing.name, "status": "Loan Repayment Created"})
				continue
			held = frappe.db.get_value("Journal Entry", {"cheque_no": ["in", [key, key + "-CR"]], "docstatus": 1, "custom_hp_loan": loan}, "name")
			if held:
				row.update({"loan": loan, "credit_entry": held, "status": row.status if row.status != "Ready" else "Customer Credit Held"})
				continue
			# dev26: due instalments first, then open servicing-fee invoices, then customer credit; a payoff is held
			# as credit for the Finance Manager to confirm the settlement.
			from comfort_homes.hp_lending.hp_credit import allocate_receipt
			posted = allocate_receipt(loan, row.customer, row.amount, row.transaction_date, account, key, doc.bank_account,
				cost_center=_cost_center_for_loan(loan))
			row.update({"loan": loan, "loan_repayment": posted["loan_repayment"], "fee_payment": posted["fee_payment"],
				"credit_entry": posted["credit_entry"], "allocation": posted["allocation"], "status": posted["status"]})
			hp_mpaisa.remember_mobile(row.customer, row.description)
			created += 1
		except Exception as error:
			frappe.db.rollback(save_point="hp_bank_row")
			row.status = "Error"; errors.append(f"{row.customer_card_id or row.name}: {str(error)[:120]}")
	posted = sum(bool(row.loan_repayment or row.get("credit_entry") or row.get("fee_payment")) for row in doc.transactions)
	open_rows = [row for row in doc.transactions if row.status != "Before cut-over (MYOB)" and not str(row.status or "").startswith("Already ")]
	doc.update({"created_repayments": posted, "status": "Reconciled" if posted and posted == len(open_rows) else "Partially Reconciled"})
	doc.save()
	result = {"created": created, "skipped": skipped, "errors": errors[:10]}
	if account == hp_collections.wallet_account():
		# 7.1.3: M-PAiSA settlements (and their fees) clear the wallet into 1008.
		frappe.db.savepoint("hp_mpaisa_settlements")
		try:
			result.update(hp_mpaisa.post_settlements(doc))
		except Exception as error:
			frappe.db.rollback(save_point="hp_mpaisa_settlements")
			result["errors"].append("M-PAiSA settlements not posted: " + str(error)[:160])
	return result


def _statement_key(bank_account, content_hash, sheet, row_index):
	"""Exact-file re-import protection, not a claim to identify re-exported transactions."""
	value = "|".join((str(bank_account), content_hash, str(sheet), str(row_index)))
	return "HP-BANK-" + hashlib.sha256(value.encode()).hexdigest()[:40]


@frappe.whitelist(methods=["GET", "POST"])
def check_application_access(loan_application, write=0, agreement_row=None, company_signing=0, contract_signing=0):
    """Shared access gate for retained safe-exec API scripts; returns no customer data."""
    company_signing = int(company_signing)
    write = int(write) or company_signing
    if write and getattr(frappe.request, "method", None) != "POST":
        frappe.throw("Document changes require POST")
    if not loan_application:
        frappe.throw("Loan Application is required")
    if write:
        frappe.db.sql("SELECT name FROM `tabLoan Application` WHERE name = %s FOR UPDATE", (loan_application,))
    application = frappe.get_doc("Loan Application", loan_application)
    application.check_permission("write" if write else "read")
    if application.company != DEFAULT_COMPANY:
        frappe.throw("This endpoint is restricted to Flexi applications")
    if write and application.docstatus != 0:
        frappe.throw("Submitted or cancelled application documents cannot be changed by this endpoint")
    if company_signing:
        frappe.only_for(("Director", "Finance Manager"))
    if agreement_row:
        row = frappe.get_doc("Loan Customer Agreement", agreement_row)
        if row.parent != application.name or row.parenttype != "Loan Application" or row.parentfield != "custom_customer_documents":
            frappe.throw("Agreement row does not belong to the selected application")
        if write and row.get("status") == "Completed":
            frappe.throw("Completed agreement evidence cannot be overwritten")
    if int(contract_signing):
        from comfort_homes.hp_lending.document_policy import DIGITAL_CONTRACT_SIGNING_ENABLED
        if not DIGITAL_CONTRACT_SIGNING_ENABLED:
            frappe.throw("Digital HP contract signing is not enabled; print, sign and upload the executed scan")
    return True
