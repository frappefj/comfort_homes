"""7.1.6: branch HP numbers, Bank PPT Authority, income and charges, reconciliation IDs and table."""
import ast
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- branch numbers

@pytest.fixture
def br(env):
	f, load = env
	return f, load("hp_lending/hp_branch_number.py")


@pytest.mark.parametrize("location,prefix", [
	("Grantham - Flexi", "GRA"), ("Namaka - Flexi", "NAM"), ("Lautoka - Flexi", "LTK"), ("Nausori - Flexi", "NAU"),
	("Denarau - Flexi", "DEN"), ("Nakasi - Flexi", "NAK"), ("HQ - Flexi", "HQ"),
	("Web - Flexi", "HQ"), ("Nadi - Flexi", None), ("Suva - Flexi", None), ("Others - Flexi", None), ("", None),
])
def test_prefix_by_branch(br, location, prefix):
	_f, m = br
	assert m.prefix_for(location) == prefix


def test_next_number_continues_from_the_highest_used(br):
	_f, m = br
	existing = ["GRA2026526", "GRA2026099", "GRA2025278", "GRA202627", "GRA2026187", "NAM2026220", None, "2026-00314"]
	assert m.next_number("GRA", 2026, existing) == "GRA2026527"
	assert m.next_number("NAM", 2026, existing) == "NAM2026221"
	assert m.next_number("NAK", 2026, existing) == "NAK2026001"
	assert m.next_number("GRA", 2027, existing) == "GRA2027001"  # restarts each year


def test_store_prefixes_are_three_letters_except_hq(br):
	_f, m = br
	assert {p for p in m.PREFIXES if len(p) != 3} == {"HQ"}
	assert "NK" not in m.PREFIXES and "SC" not in m.PREFIXES


@pytest.mark.parametrize("value,expected", [("GRA2026527", True), ("NAK2026001", True), ("NK2026001", False), ("ACC-LOAP-2026-00952", False),
	("2026-00314", False), ("XYZ2026001", False), ("", False)])
def test_is_branch_number(br, value, expected):
	_f, m = br
	assert m.is_branch_number(value) is expected


def _app(state_before, state_after, value="ACC-LOAP-2026-00960", location="Grantham - Flexi"):
	old = Row(workflow_state=state_before)
	doc = Row(name="ACC-LOAP-2026-00960", company="Comfort Home Furnishing PTE Limited", workflow_state=state_after,
		custom_flexi_hp_account_number=value, custom_branchlocation=location, applicant="NEW CUSTOMER")
	doc.get_doc_before_save = lambda: old
	return doc


def test_number_is_given_on_send_to_contract_signing_and_recorded_on_the_customer(br):
	f, m = br
	f.db.sql = Mock(side_effect=lambda q, v=None, **k: [("GRA2026526",)] if "Loan Application" in q else [])
	f.db.has_column = Mock(return_value=True)
	f.db.get_value = Mock(return_value=None)
	f.db.set_value = Mock()
	doc = _app("Approved", "Contract Signing")
	m.assign_on_contract_signing(doc)
	assert doc.custom_flexi_hp_account_number == "GRA2026527"
	f.db.set_value.assert_called_once_with("Customer", "NEW CUSTOMER", "custom_ch_account_reference_no", "GRA2026527",
		update_modified=False)


def test_returning_customer_keeps_their_number_on_the_customer(br):
	f, m = br
	f.db.sql = Mock(return_value=[])
	f.db.has_column = Mock(return_value=True)
	f.db.get_value = Mock(return_value="GRA2025151")
	f.db.set_value = Mock()
	m.assign_on_contract_signing(_app("Approved", "Contract Signing"))
	f.db.set_value.assert_not_called()


def test_other_transitions_and_existing_numbers_are_left_alone(br):
	f, m = br
	f.db.sql = Mock(return_value=[])
	doc = _app("Pending Approval", "Approved")
	m.assign_on_contract_signing(doc)
	assert doc.custom_flexi_hp_account_number == "ACC-LOAP-2026-00960"
	doc = _app("Approved", "Contract Signing", value="GRA2026187")
	m.assign_on_contract_signing(doc)
	assert doc.custom_flexi_hp_account_number == "GRA2026187"


def test_branch_without_prefix_stops_with_a_clear_message(br):
	f, m = br
	f.db.sql = Mock(return_value=[])
	with pytest.raises(ValueError, match="no HP account number prefix"):
		m.assign_on_contract_signing(_app("Approved", "Contract Signing", location="Others - Flexi"))


def test_assigned_number_cannot_be_changed_except_by_system_manager(env):
	f, load = env
	m = load("hp_lending/hp_account_number.py")
	f.get_all = Mock(return_value=[])
	f.get_roles = Mock(return_value=["Loan Officer"])

	class App(Row):
		def is_new(self): return False
		def has_value_changed(self, field): return True
	doc = App(name="ACC-LOAP-2026-00960", custom_flexi_hp_account_number="GRA2026999", applicant="C1")
	doc.get_doc_before_save = lambda: Row(custom_flexi_hp_account_number="GRA2026527")
	with pytest.raises(ValueError, match="cannot be changed"):
		m.validate(doc)
	f.get_roles = Mock(return_value=["System Manager"])
	m.validate(doc)


def test_branch_number_is_assigned_before_validation():
	scope = {}
	exec(compile(ast.parse((ROOT / "hooks.py").read_text()), "<hooks>", "exec"), scope)
	hooks = scope["doc_events"]["Loan Application"]["before_validate"]
	assert hooks.index("comfort_homes.hp_lending.hp_branch_number.assign_on_contract_signing") > \
		hooks.index("comfort_homes.hp_lending.hp_workflow.require_deposit_before_contract")


# ---------------------------------------------------------------- matching: contract number and payer account

@pytest.fixture
def api(env):
	f, load = env
	m = load("api.py")
	f.db.has_column = Mock(return_value=True)
	m._customer_name_from_description = Mock(return_value="")
	return f, m


def test_contract_number_on_application_finds_that_exact_loan(api):
	f, m = api
	def get_all(dt, filters=None, fields=None, **k):
		if dt == "Loan Application" and "custom_flexi_hp_account_number" in (filters or {}):
			return [Row(name="ACC-LOAP-2026-00960")]
		if dt == "Loan" and (filters or {}).get("loan_application") == "ACC-LOAP-2026-00960":
			return [Row(name="ACC-LOAN-2026-00950", applicant="Returning Customer")]
		return []
	f.get_all = Mock(side_effect=get_all)
	text = m.normalise_narration("DIRECT TRANSFER FROM 85000001 GRA2026527")
	assert m._customer_and_loan(text, m.extract_card_id(text), detail=True) == \
		("Returning Customer", "ACC-LOAN-2026-00950", 1, "identifier")


def test_payer_account_identifies_a_single_customer(api):
	f, m = api
	def get_all(dt, filters=None, fields=None, **k):
		if dt == "Customer" and "custom_hp_payer_accounts" in (filters or {}):
			return [Row(name="Sonam Sanjana Kumar", custom_hp_payer_accounts="83060892")]
		if dt == "Loan" and (filters or {}).get("applicant") == "Sonam Sanjana Kumar":
			return [Row(name="ACC-LOAN-2026-00939")]
		return []
	f.get_all = Mock(side_effect=get_all)
	assert m.payer_accounts("DIRECT TRANSFER FROM 83060892 FLEXI") == ["83060892"]
	assert m._customer_and_loan("DIRECT TRANSFER FROM 83060892 FLEXI", "", detail=True) == \
		("Sonam Sanjana Kumar", "ACC-LOAN-2026-00939", 1, "payer account")


def test_shared_payer_account_is_not_guessed(api):
	f, m = api
	def get_all(dt, filters=None, fields=None, **k):
		if dt == "Customer" and "custom_hp_payer_accounts" in (filters or {}):
			return [Row(name="A", custom_hp_payer_accounts="83060892"), Row(name="B", custom_hp_payer_accounts="83060892, 1")]
		return []
	f.get_all = Mock(side_effect=get_all)
	assert m._customer_and_loan("DIRECT TRANSFER FROM 83060892", "")[:2] == ("", "")


# ---------------------------------------------------------------- Bank PPT Authority

@pytest.fixture
def ppt(env):
	f, load = env
	return f, load("hp_lending/hp_ppt.py")


@pytest.mark.parametrize("name,key", [("BSP", "BSP"), ("Westpac Banking Corporation", "Westpac"), ("anz", "ANZ"),
	("Bred Bank", "BRED"), ("HFC Bank", "HFC"), ("Bank of the South Pacific", "BSP"), ("Unknown", "")])
def test_bank_key(ppt, name, key):
	_f, m = ppt
	assert m.bank_key(name) == key


def test_proposal_uses_the_same_bank_and_the_contract_number(ppt):
	_f, m = ppt
	p = m.proposal(Row(repayment_frequency="Monthly"),
		{"custom_customer_bank_name": "BSP", "custom_bank_account_number": " 83060892 ", "custom_flexi_hp_account_number": "GRA2026527"},
		{"instalment": 336.01, "next_due": date(2026, 12, 4), "maturity": date(2027, 11, 4)})
	assert p == {"custom_hp_ppt_customer_bank": "BSP", "custom_hp_ppt_customer_account": "83060892",
		"custom_hp_ppt_credit_account": "BSP Bank - 87349722", "custom_hp_ppt_frequency": "Monthly", "custom_hp_ppt_amount": 336.01,
		"custom_hp_ppt_start_date": date(2026, 12, 4), "custom_hp_ppt_end_date": date(2027, 11, 4),
		"custom_hp_ppt_reference": "GRA2026527"}


@pytest.mark.parametrize("loan_freq,period,expected", [("Bi-Weekly", "", "Fortnightly"), ("Weekly", None, "Weekly"),
	("Bi-Weekly", "Bi-Monthly", "Bi-Monthly"), ("Monthly", "Monthly", "Monthly")])
def test_frequency(ppt, loan_freq, period, expected):
	_f, m = ppt
	assert m.frequency(loan_freq, period) == expected


def _loan(**extra):
	values = dict(name="ACC-LOAN-2026-00939", docstatus=1, applicant="Sonam", loan_application="APP",
		custom_hp_ppt_status="Prepared", custom_hp_ppt_prepared_by="officer@x", custom_hp_ppt_customer_bank="BSP",
		custom_hp_ppt_customer_account="83060892", custom_hp_ppt_credit_account="BSP Bank - 87349722",
		custom_hp_ppt_frequency="Monthly", custom_hp_ppt_amount=336.01, custom_hp_ppt_start_date="2026-12-04",
		custom_hp_ppt_end_date="2027-11-04", custom_hp_ppt_reference="GRA2026527")
	values.update(extra)
	doc = Row(values)
	doc.flags = SimpleNamespace(get=lambda k, d=None: getattr(doc.flags, k, d))
	doc.as_dict = lambda: dict(doc)
	doc.check_permission = Mock()
	doc.save = Mock()
	doc.meta = SimpleNamespace(get_label=lambda f: f)
	return doc


def test_preparer_cannot_verify(ppt):
	f, m = ppt
	f.get_roles = Mock(return_value=["Accounts Officer"])
	f.session = SimpleNamespace(user="officer@x")
	f.get_doc = Mock(return_value=_loan())
	with pytest.raises(ValueError, match="cannot verify"):
		m.verify("ACC-LOAN-2026-00939")


def test_second_person_verifies_and_the_account_is_remembered(ppt):
	f, m = ppt
	f.get_roles = Mock(return_value=["Disbursement Officer"])
	f.session = SimpleNamespace(user="checker@x")
	doc = _loan()
	f.get_doc = Mock(return_value=doc)
	f.db.has_column = Mock(return_value=True)
	f.db.get_value = Mock(return_value="")
	f.db.set_value = Mock()
	m.now_datetime = lambda: "2026-11-28 14:00:00"
	assert m.verify("ACC-LOAN-2026-00939") == {"status": "Verified"}
	assert doc.custom_hp_ppt_verified_by == "checker@x" and doc.custom_hp_ppt_fingerprint == m.fingerprint(dict(doc))
	f.db.set_value.assert_called_once_with("Customer", "Sonam", "custom_hp_payer_accounts", "83060892", update_modified=False)


def test_changed_detail_after_verification_needs_verifying_again(ppt):
	f, m = ppt
	f.msgprint = Mock()
	doc = _loan(custom_hp_ppt_status="Verified")
	doc.custom_hp_ppt_fingerprint = m.fingerprint(dict(doc))
	doc.custom_hp_ppt_amount = 300.00
	m.on_loan_update(doc)
	assert doc.custom_hp_ppt_status == "Prepared" and doc.custom_hp_ppt_verified_by is None


def test_stamped_copy_confirms_a_verified_authority(ppt):
	_f, m = ppt
	doc = _loan(custom_hp_ppt_status="Verified")
	doc.custom_hp_ppt_fingerprint = m.fingerprint(dict(doc))
	doc.custom_hp_ppt_stamped_copy = "/private/files/ppt.pdf"
	m.on_loan_update(doc)
	assert doc.custom_hp_ppt_status == "Bank Confirmed"


def test_print_is_draft_until_verified(ppt):
	f, m = ppt
	f.db.get_value = Mock(return_value="Officer")
	assert m.print_data(dict(_loan()))["verified"] is False
	verified = dict(_loan(custom_hp_ppt_status="Verified"))
	verified["custom_hp_ppt_fingerprint"] = m.fingerprint(verified)
	assert m.print_data(verified)["verified"] is True


def test_ppt_print_format_renders_the_narration_and_draft():
	import jinja2
	html = next(x for x in json.loads((ROOT / "fixtures" / "print_format.json").read_text()) if x["name"] == "HP Bank PPT Authority")["html"]
	doc = dict(_loan())
	frappe_ns = SimpleNamespace(format=lambda v, df: str(v))
	data = {"verified": False, "banks": ["BSP", "Westpac", "ANZ", "BRED", "HFC"],
		"credit_accounts": ["BSP Bank - 87349722", "Westpac Bank - 9808559661"], "frequencies": ["Weekly", "Monthly"],
		"prepared_by": "Officer", "verified_by": "", "customer": "SONAM SANJANA KUMAR"}
	out = jinja2.Environment().from_string(html).render(doc=SimpleNamespace(**doc), hp_ppt=lambda d: data, frappe=frappe_ns)
	assert "GRA2026527" in out and "DRAFT" in out and "336.01" in out and "One authority per contract" in out
	data["verified"] = True
	assert "DRAFT" not in jinja2.Environment().from_string(html).render(doc=SimpleNamespace(**doc), hp_ppt=lambda d: data, frappe=frappe_ns)


def test_ppt_fields_allow_change_after_submit():
	fields = [c for c in json.loads((ROOT / "fixtures" / "custom_field.json").read_text()) if c["fieldname"].startswith("custom_hp_ppt_")]
	assert len(fields) == 17 and all(c["allow_on_submit"] == 1 and c["dt"] == "Loan" for c in fields)


# ---------------------------------------------------------------- income and charges

@pytest.fixture
def inc(env):
	f, load = env
	return f, load("hp_lending/hp_income.py")


def test_income_by_loan_ties_to_the_ledger(inc):
	_f, m = inc
	acc = {"4001": "interest", "2013": "unearned", "4002": "admin_fee", "4003": "doc_fee", "2015": "charges", "4004": "charges_income"}
	gl = [
		dict(loan="L1", account="2013", posting_date="2026-11-19", debit=0, credit=727.10),  # gross booking
		dict(loan="L1", account="4002", posting_date="2026-11-19", debit=0, credit=80),
		dict(loan="L1", account="4003", posting_date="2026-11-19", debit=0, credit=22.22),
		dict(loan="L1", account="2013", posting_date="2026-11-30", debit=60.59, credit=0),  # month-end release
		dict(loan="L1", account="4001", posting_date="2026-11-30", debit=0, credit=60.59),
		dict(loan="L2", account="2015", posting_date="2026-10-15", debit=0, credit=5.00),  # charge before the period
		dict(loan="L2", account="2015", posting_date="2026-11-15", debit=0, credit=4.00),
		dict(loan="L2", account="2015", posting_date="2026-11-20", debit=4.00, credit=0),  # reversed by re-test
		dict(loan=None, account="4001", posting_date="2026-11-30", debit=0, credit=10),  # MYOB journal, no loan
		dict(loan="L1", account="4001", posting_date="2026-12-31", debit=0, credit=60.59),  # after the period
	]
	fees = [dict(loan="L2", posting_date="2026-11-10", billed=2.50, outstanding=0), dict(loan="L2", posting_date="2026-11-11", billed=25, outstanding=25)]
	rows = {r["loan"]: r for r in m.combine(gl, fees, acc, "2026-11-01", "2026-11-30")}
	assert rows["L1"]["interest_earned"] == 60.59 and rows["L1"]["unearned_interest"] == 666.51
	assert rows["L1"]["admin_fee"] == 80 and rows["L1"]["doc_fee"] == 22.22
	assert rows["L2"]["charges_raised"] == 4.00 and rows["L2"]["charges_cleared"] == 4.00 and rows["L2"]["charges_held"] == 5.00
	assert rows["L2"]["fees_billed"] == 27.50 and rows["L2"]["fees_paid"] == 2.50
	assert rows[m.UNLINKED]["interest_earned"] == 10
	assert m.totals(list(rows.values()))["interest_earned"] == 70.59  # equals the 4001 credits in November


def test_cards_and_report_are_registered():
	cards = {p.parent.name: json.loads(p.read_text()) for p in (ROOT / "comfort_homes" / "number_card").glob("hp_*/hp_*.json")}
	assert {"hp_interest_earned", "hp_unearned_interest", "hp_charges_held", "hp_fees_billed", "hp_ppt_outstanding"} <= set(cards)
	w = json.loads((ROOT / "comfort_homes" / "workspace" / "portfolio_control" / "portfolio_control.json").read_text())
	names = {c["number_card_name"] for c in w["number_cards"]}
	assert {"HP Interest Earned", "HP Unearned Interest", "HP Charges Held", "HP Fees Billed", "HP PPT Copies Outstanding"} <= names
	placed = {b["data"].get("number_card_name") for b in json.loads(w["content"]) if b["type"] == "number_card"}
	assert names <= placed
	report = json.loads((ROOT / "comfort_homes" / "report" / "hp_income_and_charges" / "hp_income_and_charges.json").read_text())
	assert report["report_type"] == "Script Report"


# ---------------------------------------------------------------- reconciliation IDs and table

@pytest.fixture
def rec(env):
	f, load = env
	return f, load("hp_lending/hp_reconciliation_setup.py")


def test_reconciliation_id_format(rec):
	_f, m = rec
	assert m.series_prefix("BSP", 2026) == "BREC-BSP-2026-.####"
	assert m.series_prefix("m-paisa", 2026) == "BREC-MPAISA-2026-.####"
	assert m.series_prefix("", 2027) == "BREC-BANK-2027-.####"


def test_autoname_uses_the_bank_code(rec, monkeypatch):
	f, m = rec
	f.db.has_column = Mock(return_value=True)
	f.db.get_value = Mock(return_value="WBC")
	made = Mock(return_value="BREC-WBC-2026-0001")
	monkeypatch.setattr(m, "make_autoname", made)
	doc = Row(bank_account="Westpac Flexi OD 9808559661 - Westpac")
	m.autoname(doc)
	assert doc.name == "BREC-WBC-2026-0001" and made.call_args.args[0].startswith("BREC-WBC-")


def test_bank_codes_are_filled_by_ledger_account(rec):
	f, m = rec
	f.db.has_column = Mock(return_value=True)
	f.get_all = Mock(return_value=[Row(name="BSP Flexi", account="1201 - BSP", custom_hp_bank_code=""),
		Row(name="Done", account="1008 - WBC", custom_hp_bank_code="WBCC")])
	f.db.get_value = Mock(return_value="1201")
	f.db.set_value = Mock()
	assert m.ensure_bank_codes() == [("BSP Flexi", "BSP")]


def test_reconciliation_hooks_and_table_layout():
	scope = {}
	exec(compile(ast.parse((ROOT / "hooks.py").read_text()), "<hooks>", "exec"), scope)
	assert scope["doc_events"]["Loan Bank Reconciliation"]["autoname"].endswith("hp_reconciliation_setup.autoname")
	item = next(d for d in json.loads((ROOT / "fixtures" / "doctype.json").read_text()) if d["name"] == "Loan Bank Reconciliation Item")
	shown = {f["fieldname"]: f.get("columns") for f in item["fields"] if f.get("in_list_view")}
	assert shown == {"transaction_date": 1, "description": 3, "amount": 1, "customer": 1, "loan": 2, "status": 1, "allocation": 1}
	assert sum(shown.values()) == 10


# ---------------------------------------------------------------- renumbering where branch numbering stopped

def _plan():
	return [
		{"application": "ACC-LOAP-2026-00848", "current": "2026-00146", "action": "Renumber", "new_number": "GRA2026527"},
		{"application": "ACC-LOAP-2026-00855", "current": "2026-00149", "action": "Renumber", "new_number": "NAM2026221"},
		{"application": "ACC-LOAP-2026-00473", "current": "NAM2026065", "action": "Change Branch/Location", "new_branch": "Namaka"},
		{"application": "ACC-LOAP-2026-00028", "current": "SC202516", "action": "Keep (no change)"},
	]


def test_check_plan_accepts_a_clean_plan(br):
	_f, m = br
	current = {"ACC-LOAP-2026-00848": "2026-00146", "ACC-LOAP-2026-00855": "2026-00149", "ACC-LOAP-2026-00473": "NAM2026065"}
	assert m.check_plan(_plan(), current) == []


def test_check_plan_stops_on_changed_records_duplicates_and_bad_numbers(br):
	_f, m = br
	plan = _plan()
	plan[1]["new_number"] = "GRA2026527"  # used twice
	plan.append({"application": "ACC-LOAP-2026-00860", "current": "2026-00159", "action": "Renumber", "new_number": "NK2026001"})
	current = {"ACC-LOAP-2026-00848": "GRA2026600", "ACC-LOAP-2026-00855": "2026-00149", "ACC-LOAP-2026-00473": "NAM2026065",
		"ACC-LOAP-2026-00860": "2026-00159"}
	problems = " | ".join(m.check_plan(plan, current))
	assert "now GRA2026600" in problems
	assert "used twice" in problems
	assert "NK2026001 is not a branch number" in problems


def test_check_plan_reports_missing_applications(br):
	_f, m = br
	assert m.check_plan(_plan()[:1], {}) == ["ACC-LOAP-2026-00848: not found"]


def test_previous_number_field_and_matcher():
	fields = {(f["dt"], f["fieldname"]): f for f in json.loads((ROOT / "fixtures/custom_field.json").read_text())}
	for dt in ("Loan Application", "Customer"):
		f = fields[(dt, "custom_hp_previous_account_number")]
		assert f["read_only"] == 1 and f["no_copy"] == 1
	api = (ROOT / "api.py").read_text()
	assert '"custom_hp_previous_account_number")' in api  # customer identifiers
	assert 'filters={"custom_hp_previous_account_number": ["in", numbers]}' in api  # application fallback


def test_apply_plan_is_dry_run_by_default_and_role_gated():
	src = (ROOT / "hp_lending/hp_branch_number.py").read_text()
	tree = ast.parse(src)
	fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "apply_plan")
	assert [a.arg for a in fn.args.args] == ["rows", "dry_run"]
	assert ast.literal_eval(fn.args.defaults[0]) == 1
	assert "PLAN_ROLES" in ast.get_source_segment(src, fn)
