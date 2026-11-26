"""7.1.3: collections by location, M-PAiSA reconciliation, payment-mode labels, web form fixes."""
import ast
import json
from datetime import datetime
from pathlib import Path

from comfort_homes.tests.test_gross_controls import Row, env

ROOT = Path(__file__).resolve().parents[1]


def _hooks():
	tree = ast.parse((ROOT / "hooks.py").read_text())
	out = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
			try:
				out[node.targets[0].id] = ast.literal_eval(node.value)
			except ValueError:
				pass
	return out


def _web_form():
	return next(w for w in json.loads((ROOT / "fixtures" / "web_form.json").read_text()) if w["name"] == "loan-application-new")


# ---------------------------------------------------------------- configuration

def test_store_accounts_are_unique_free_numbers_and_hq_is_new():
	cfg = json.loads((ROOT / "setup_data" / "collection_locations.json").read_text())
	numbers = [s["number"] for s in cfg["stores"]] + [cfg["mpaisa"]["wallet_number"], cfg["mpaisa"]["charges_number"]]
	assert len(numbers) == len(set(numbers))
	stores = {s["store"]: s for s in cfg["stores"]}
	assert stores["Suva"]["number"] == "1111" and stores["Suva"]["existing"] and not stores["Suva"]["cost_center"]
	assert stores["HQ"]["number"] not in ("1111", "1112") and stores["HQ"]["cost_center"] == "HQ"
	assert {"Grantham", "Lautoka", "Nadi", "Nakasi", "Namaka", "Nausori", "Denarau", "HQ"} <= set(stores)
	assert "Web" not in stores
	csv_numbers = {line.split(",")[2] for line in (ROOT / "setup_data" / "chart_of_accounts_flexi.csv").read_text().splitlines()[1:]}
	clash = {s["number"] for s in cfg["stores"] if not s.get("existing")} & csv_numbers
	assert not clash, clash
	assert cfg["mpaisa"]["settlement_account_number"] == "1008"
	assert cfg["mpaisa"]["merchants"]["27287"] == "M-PAiSA App"


# ---------------------------------------------------------------- daily collections

def test_classification_counts_money_in_and_drops_transfers(env):  # noqa: F811
	_f, load = env
	c = load("hp_lending/hp_collections.py")
	cash_bank = frozenset({"1201", "1008", "1206"})
	assert c.classify("Loan Repayment") == "repayments"
	assert c.classify("Payment Entry", paid_from="2010", deposit_account="2010") == "deposits"
	assert c.classify("Payment Entry", paid_from="1120", references=["Sales Invoice"], deposit_account="2010") == "fees"
	assert c.classify("Journal Entry", credit_accounts=["2017"], credit_account="2017", unidentified_account="2007", cash_bank=cash_bank) == "credit"
	assert c.classify("Journal Entry", credit_accounts=["2007"], credit_account="2017", unidentified_account="2007", cash_bank=cash_bank) == "unidentified"
	# M-PAiSA settlement Dr 1008 + Dr charges / Cr wallet: a transfer, not money in
	assert c.classify("Journal Entry", credit_accounts=["1206"], credit_account="2017", unidentified_account="2007", cash_bank=cash_bank) is None


def test_summary_totals_per_day_and_location(env):  # noqa: F811
	_f, load = env
	c = load("hp_lending/hp_collections.py")
	receipts = [
		dict(date="2026-11-24", location="HQ", kind="repayments", amount=73.30),
		dict(date="2026-11-24", location="HQ", kind="deposits", amount=170.00),
		dict(date="2026-11-24", location="M-PAiSA", kind="repayments", amount=56.00),
		dict(date="2026-11-25", location="HQ", kind="unidentified", amount=60.00),
	]
	rows = c.summarise(receipts)
	hq = next(r for r in rows if r["date"] == "2026-11-24" and r["location"] == "HQ")
	assert hq["repayments"] == 73.30 and hq["deposits"] == 170.00 and hq["total"] == 243.30
	assert len(rows) == 3
	period = c.summarise(receipts, by_date=False)
	assert next(r for r in period if r["location"] == "HQ")["total"] == 303.30


def test_store_lookup_uses_the_cost_centre_name(env):  # noqa: F811
	_f, load = env
	c = load("hp_lending/hp_collections.py")
	assert c.store_for_cost_center("Grantham - Flexi")["number"] == "1114"
	assert c.store_for_cost_center("HQ - Flexi")["store"] == "HQ"
	assert c.store_for_cost_center("Web - Flexi") is None and c.store_for_cost_center(None) is None


def test_counter_receipt_is_booked_to_the_store_and_reconciliation_is_left_alone(env, monkeypatch):  # noqa: F811
	_f, load = env
	c = load("hp_lending/hp_collections.py")
	monkeypatch.setattr(c, "store_cash_account", lambda cc: "1114 - Cash in Hand - Grantham - Flexi" if cc == "Grantham - Flexi" else None)
	doc = Row(docstatus=0, custom_hp_received_at="Grantham - Flexi", payment_account="1201 - BSP")
	c.counter_receipt_account(doc)
	assert doc.payment_account == "1114 - Cash in Hand - Grantham - Flexi"
	reconciled = Row(docstatus=0, custom_hp_received_at="Grantham - Flexi", bank_account="BSP Flexi", payment_account="1201 - BSP")
	c.counter_receipt_account(reconciled)
	assert reconciled.payment_account == "1201 - BSP"


def test_wiring_report_card_and_fields():
	hooks = _hooks()
	validate = hooks["doc_events"]["Loan Repayment"]["validate"]
	assert validate[0] == "comfort_homes.hp_lending.hp_collections.counter_receipt_account"
	assert hooks["doctype_js"]["Loan Repayment"] == "public/js/loan_repayment_store.js"
	assert (ROOT / "public" / "js" / "loan_repayment_store.js").exists()
	fixture_dts = next(x for x in hooks["fixtures"] if x["dt"] == "Custom Field")["filters"][0][2]
	assert "Loan Repayment" in fixture_dts
	fields = {f["name"]: f for f in json.loads((ROOT / "fixtures" / "custom_field.json").read_text())}
	assert fields["Loan Repayment-custom_hp_received_at"]["options"] == "Cost Center"
	assert fields["Customer-custom_hp_payer_mobiles"]["read_only"] == 1
	assert "M-PAiSA Settlement" in fields["Journal Entry-custom_hp_entry_type"]["options"].split("\n")
	report = json.loads((ROOT / "comfort_homes" / "report" / "hp_daily_collections" / "hp_daily_collections.json").read_text())
	assert report["report_type"] == "Script Report" and report["add_total_row"] == 1
	card = json.loads((ROOT / "comfort_homes" / "number_card" / "collected_today" / "collected_today.json").read_text())
	assert card["method"] == "comfort_homes.hp_lending.hp_collections.card_collected_today"
	workspace = json.loads((ROOT / "comfort_homes" / "workspace" / "portfolio_control" / "portfolio_control.json").read_text())
	assert "Collected Today" in workspace["content"] and "HP Daily Collections" in workspace["content"]


# ---------------------------------------------------------------- M-PAiSA

HEADERS = ["TransactionType", "Access", "Logtime", "Aparty", "Bparty", "BusinessID", "Assistant_IN", "Receipt_No",
	"BusinessReferenceNo", "Amount", "TransactionFees", "NetAmount", "BusinessEndBalance", "Status", "Transaction_Type"]


def _pay(tid, when, mobile, ref, amount, merchant=27287, kind="Payment"):
	return (tid, "App", when, mobile, None, merchant, None, "R" + tid, ref, amount, 0.4, round(amount - 0.4, 2), 0, "SUCCESS", kind)


def _settle(tid, when, amount, merchant=27287):
	return (tid, "WEB", when, None, None, merchant, None, None, None, amount, None, amount, 0, "SUCCESS", "Bank Settlement")


def _mp(env):  # noqa: F811
	f, load = env
	f._dict = Row
	return load("hp_lending/hp_mpaisa.py")


def test_mpaisa_export_is_detected_and_read(env):  # noqa: F811
	mp = _mp(env)
	rows = [_pay("2", datetime(2026, 11, 24, 9, 0), "8077721", "Ltk202515", 40),
		_settle("1", datetime(2026, 11, 24, 7, 43), 374.0),
		_pay("3", datetime(2026, 11, 24, 9, 5), "048322300029", "counter 1", 75.36, merchant=27301, kind="External QR Pay")]
	tables = [{"sheet": "MPAISA APP", "headers": HEADERS, "rows": rows}]
	assert mp.is_statement(tables)
	assert not mp.is_statement([{"sheet": "BSP", "headers": ["Date", "Description", "Credit"], "rows": []}])
	read = mp.statement_rows(tables)
	assert [r.kind for r in read if r.merchant == "27287"] == ["settlement", "payment"]
	qr = next(r for r in read if r.merchant == "27301")
	assert qr.merchant_label == "Grantham QR" and qr.amount == 75.36 and qr.net == 74.96
	assert mp.mobile_digits("8077721") == "8077721" and mp.mobile_digits("+679-807 7721") == "8077721"
	assert mp.mobile_digits("048322300029") == ""
	app = next(r for r in read if r.merchant == "27287" and r.kind == "payment")
	assert mp.description(app) == "Ltk202515 | M-PAiSA App | from 8077721"
	assert mp.statement_key(app) == "HP-MPAISA-2"


def test_settlements_match_oldest_payments_first_and_respect_cutover(env):  # noqa: F811
	from datetime import date

	mp = _mp(env)
	rows = [
		# settles the previous day's balance; the two morning payments wait for the next settlement
		_pay("1", datetime(2026, 11, 20, 7, 5), "1", "a", 60), _pay("2", datetime(2026, 11, 20, 8, 6), "2", "b", 100),
		_settle("3", datetime(2026, 11, 20, 8, 36), 655.2),
		# 59.60 + 99.60 (from the 20th, MYOB) + 49.60 (22nd) = 208.80: mixed batch, by hand
		_pay("4", datetime(2026, 11, 22, 9, 0), "3", "c", 50), _settle("5", datetime(2026, 11, 23, 7, 18), 208.8),
		# clean post cut-over batch, settled at 07:43 next day: 64.60 + 49.60 = 114.20, the 09:00 payment waits
		_pay("6", datetime(2026, 11, 23, 15, 0), "4", "d", 65), _pay("7", datetime(2026, 11, 23, 19, 0), "5", "e", 50),
		_pay("8", datetime(2026, 11, 24, 7, 0), "6", "f", 20), _settle("9", datetime(2026, 11, 24, 7, 43), 114.2),
	]
	read = mp.statement_rows([{"sheet": "APP", "headers": HEADERS, "rows": rows}])
	postable, skipped = mp.settlement_plan(read, date(2026, 11, 21))
	assert [e["row"].id for e in postable] == ["9"]
	assert postable[0]["fees"] == 0.8 and postable[0]["net"] == 114.2 and postable[0]["payments"] == 2
	reasons = {e["row"].id: e["reason"] for e in skipped}
	assert "not all in this file" in reasons["3"]
	assert "before cut-over (159.20 net, MYOB) with 1 after" in reasons["5"]


def test_mpaisa_wired_into_import_and_posting():
	api = (ROOT / "api.py").read_text()
	assert "hp_mpaisa.is_statement(tables)" in api and "hp_mpaisa.import_rows(doc, tables, statement_hash)" in api
	assert "hp_mpaisa.post_settlements(doc)" in api and "hp_mpaisa.remember_mobile(row.customer, row.description)" in api
	assert '"Before cut-over (MYOB)"' in api
	script = next(c for c in json.loads((ROOT / "fixtures" / "client_script.json").read_text()) if c["name"] == "Flexi Loan Bank Reconciliation")["script"]
	assert script == (ROOT / "public" / "js" / "loan_bank_reconciliation.js").read_text()
	assert "settlements_skipped" in script and 'analysis.format === "M-PAiSA"' in script


# ---------------------------------------------------------------- payment modes, deposit, booking branch

def test_every_agreed_payment_method_maps_to_a_label_mode():
	source = (ROOT / "loan_mapping.py").read_text()
	mapping = ast.literal_eval(source.split("MODE_OF_PAYMENT_MAP = ", 1)[1].split("\n}\n", 1)[0] + "\n}")
	options = next(f for f in json.loads((ROOT / "fixtures" / "custom_field.json").read_text())
		if f["fieldname"] == "custom_flexi_payment_method")["options"].split("\n")
	assert not [o for o in options if o and o not in mapping]
	assert set(mapping.values()) == {"Bank PPT", "Payroll Deduction", "Cash", "Other"}
	events = (ROOT / "flexi_events.py").read_text()
	assert "PAYMENT_MODES" not in events and "agreed_mode_of_payment" in events
	setup = (ROOT / "hp_lending" / "hp_setup.py").read_text()
	assert "_modes()" in setup and '"Mode of Payment"' in setup


def test_deposit_defaults_to_the_store_and_booking_keeps_the_branch():
	api = (ROOT / "api.py").read_text()
	assert "store_cash_account(branch) or frappe.db.get_value(\"Company\", company, \"default_bank_account\")" in api
	assert '"cost_center": branch or None' in api
	lifecycle = (ROOT / "hp_lending" / "hp_lifecycle.py").read_text()
	assert '**party, **branch}' in lifecycle and 'side: float(abs(clearing)), **branch}' in lifecycle


# ---------------------------------------------------------------- online application form

def test_web_form_employer_number_and_typing_guard():
	form = _web_form()
	field = next(f for f in form["web_form_fields"] if f["fieldname"] == "custom_flexi_employer_contact")
	assert field["fieldtype"] == "Data"
	script = form["client_script"]
	assert "return `+679-${digits}`;" in script and "return `+679 ${digits}`;" not in script
	assert script.startswith("/* comfort_homes: grid typing guard (7.1.3) */")
	assert "flexiEditing" in script and "__flexiRefreshPending" in script
