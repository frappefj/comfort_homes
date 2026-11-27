"""7.1.4: detailed statement, HP account number guard, Lending shortcut, cross-reconciliation duplicate check."""
import ast
import json
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
D = Decimal


# ---------------------------------------------------------------- statement

@pytest.fixture
def stmt(env):
	_f, load = env
	return load("hp_lending/hp_statement.py")


@pytest.mark.parametrize("account,expected", [
	("1206 - M-PAiSA Wallet - Flexi", "M-PAiSA"),
	("1117 - Cash in Hand - Nadi - Flexi", "Cash (Nadi)"),
	("1201 - BSP - Flexi - 87349722 - Flexi", "Bank transfer"),
	("2016 - Westpac Bank - OD - Flexi - 9808559661 - Flexi", "Bank transfer"),
	("1170 - Undeposited Funds - Flexi", "Cash / cheque"),
	(None, ""),
])
def test_payment_method_from_receiving_account(stmt, account, expected):
	assert stmt.payment_method(account) == expected


def test_contract_summary_adds_up_for_new_contract(stmt):
	# ACC-LOAP-2026-00952 on devcom: 4,700 cash, 1,500 deposit, 25 doc fee, 80 admin, 727.10 term charges = 4,032.10
	app = {"custom_flexi_cash_price": 4700, "custom_flexi_deposit_amount": 1500, "custom_flexi_doc_fee_net": 22.22,
		"custom_flexi_doc_fee_vat": 2.78, "custom_flexi_admin_fee": 80, "custom_flexi_term_charges": 727.10}
	c = stmt.contract_summary(app, D("4032.10"))
	assert c["ties"] and c["financed"] == D("3200.00") and c["doc_fee"] == D("25.00") and c["total_payable"] == D("4032.10")


def test_contract_summary_falls_back_to_myob_total_when_fields_do_not_add_up(stmt):
	# ACC-LOAP-2026-00314 (converted): application fields give 1,323.98, MYOB contract is 1,382.43
	app = {"custom_flexi_cash_price": 1169, "custom_flexi_deposit_amount": 150, "custom_flexi_doc_fee_net": 22.22,
		"custom_flexi_doc_fee_vat": 2.78, "custom_flexi_admin_fee": 30.69, "custom_flexi_term_charges": 249.29}
	c = stmt.contract_summary(app, D("1382.43"))
	assert not c["ties"] and c["total_payable"] == D("1382.43") and c["cash_price"] == D("1169.00")


@pytest.mark.parametrize("status,overdue,dpd,settled,label,arrears", [
	("Disbursed", 0, 0, False, "Current", D(0)),
	("Disbursed", 113.05, 35, False, "Past due (35 days)", D("113.05")),
	("Closed", 0, 0, False, "Repaid", D(0)),
	("Closed", 0, 0, True, "Settled early", D(0)),
])
def test_account_status(stmt, status, overdue, dpd, settled, label, arrears):
	assert stmt.account_status(status, overdue, dpd, settled) == (label, arrears)


def test_payment_history_lists_deposit_first_merges_split_receipts_and_totals(stmt):
	events = [
		(date(2026, 11, 19), "Hire purchase contract", D("4032.10"), 0, None, "contract"),
		(date(2026, 12, 4), "Payment received", 0, D("113.05"), "Bank transfer", "payment", "HP-BANK-1"),
		(date(2026, 12, 4), "Payment received (ahead of instalments)", 0, D("26.95"), "Bank transfer", "payment", "HP-BANK-1"),
		(date(2026, 12, 10), "Default charge reversed", 0, D("5.00"), None, "adjustment"),
		(date(2027, 1, 4), "Payment received", 0, D("336.01"), "M-PAiSA", "payment", "HP-BANK-2"),
	]
	deposit = {"date": date(2026, 11, 26), "amount": 1500, "method": "Bank transfer"}
	h = stmt.payment_history(events, deposit, "2026-12-31")
	assert [r["description"] for r in h["rows"]] == ["Deposit", "Payment received"]
	assert h["rows"][1]["amount"] == D("140.00")  # one bank receipt, shown once
	assert h["total"] == D("1640.00")  # rebates/reversals are not payments; January is after the statement date


def test_build_lines_keeps_method_and_old_four_value_events(stmt):
	events = [(date(2026, 11, 19), "Contract", 100, 0), (date(2026, 11, 20), "Payment received", 0, 40, "Cash (Nadi)", "payment")]
	out = stmt.build_lines(events, "2026-11-19", "2026-11-30")
	assert [l["method"] for l in out["lines"]] == [None, "Cash (Nadi)"] and out["closing"] == D("60.00")


def _render(st):
	import jinja2
	html = [x for x in json.loads((ROOT / "fixtures" / "print_format.json").read_text()) if x["name"] == "HP Statement of Account"][0]["html"]
	frappe_ns = SimpleNamespace(format=lambda v, df: v.strftime("%d-%m-%Y") if hasattr(v, "strftime") else v)
	return jinja2.Environment().from_string(html).render(doc=Row(name="L"), hp_statement=lambda doc: st, frappe=frappe_ns)


def _st(**extra):
	st = dict(loan="ACC-LOAN-2026-00939", application="ACC-LOAP-2026-00952", customer="SONAM SANJANA KUMAR", address=["Nasinu"],
		card_id=None, hp_account=None, contract_date=date(2026, 11, 19), term_months=12, frequency="Monthly",
		from_date=date(2026, 11, 19), to_date=date(2026, 11, 27), issued=date(2026, 11, 27), opening=D(0),
		lines=[dict(date=date(2026, 11, 19), description="Hire purchase contract", debit=D("4032.10"), credit=D(0), balance=D("4032.10"), method=None)],
		closing=D("4032.10"), overdue=D(0), fees_outstanding=D(0), next_due_date=date(2026, 12, 4), next_due_amount=D("336.01"),
		default_rate=2.5, email=None, status_label="Current", arrears=D(0), days_past_due=0,
		contract=dict(cash_price=D(4700), deposit=D(1500), financed=D(3200), doc_fee=D(25), admin_fee=D(80), term_charges=D("727.10"),
			total_payable=D("4032.10"), ties=True),
		flat_rate=22.0, instalment=D("336.01"), instalments=12, maturity_date=date(2027, 11, 4), credit_held=D(0),
		history=dict(rows=[dict(date=date(2026, 11, 26), description="Deposit", method="Bank transfer", amount=D(1500))], total=D(1500)),
		show_opening=False, tin="123456789", converted=False)
	st.update(extra)
	return st


def test_statement_from_contract_date_has_no_zero_opening_line_and_shows_contract_and_history():
	out = _render(_st())
	assert "Opening balance" not in out
	for text in ("Your contract", "Purchase (cash) price", "4,700.00", "Add document fee", "Add establishment (admin) fee",
			"Add term charges (interest)", "4,032.10", "22.00%", "336.01 monthly", "&times; 12", "04-11-2027", "Account status",
			"Current", "Payment history since the start of your contract", "How paid", "Total payments including deposit",
			"1,500.00", "TIN: 123456789"):
		assert text in out, text


def test_statement_for_a_later_period_keeps_opening_balance_and_flags_arrears():
	out = _render(_st(show_opening=True, from_date=date(2026, 12, 1), status_label="Past due (35 days)", arrears=D("113.05"),
		contract=dict(cash_price=D(1169), deposit=D(150), total_payable=D("1382.43"), ties=False), converted=True))
	assert "Opening balance" in out and "Past due (35 days)" in out and "113.05" in out
	assert "paid immediately" in out and "as transferred from MYOB" in out and "Add document fee" not in out


# ---------------------------------------------------------------- HP account number

@pytest.fixture
def acct(env):
	f, load = env
	return f, load("hp_lending/hp_account_number.py")


class App(Row):
	def is_new(self):
		return bool(self.get("_new"))

	def has_value_changed(self, field):
		return self.get("_changed", True)


@pytest.mark.parametrize("value,own,expected", [
	("ACC-LOAP-2026-00371", "ACC-LOAP-2026-00952", True),
	("acc-loap-2026-00952", "ACC-LOAP-2026-00952", False),
	("GRA2026117", "ACC-LOAP-2026-00952", False),
	("", "ACC-LOAP-2026-00952", False),
])
def test_other_application_id(acct, value, own, expected):
	_f, module = acct
	assert module.is_other_application_id(value, own) is expected


def test_another_applications_id_is_refused_even_when_unchanged(acct):
	f, module = acct
	f.db.get_value = Mock(return_value="Priyashna Priya Kumar")
	doc = App(name="ACC-LOAP-2026-00952", custom_flexi_hp_account_number="ACC-LOAP-2026-00371", applicant="C1", _changed=False)
	with pytest.raises(ValueError, match="ID of another application"):
		module.validate(doc)


def test_own_id_and_branch_number_pass(acct):
	f, module = acct
	f.get_all = Mock(return_value=[])
	for value in ("ACC-LOAP-2026-00952", " GRA2026118 "):
		doc = App(name="ACC-LOAP-2026-00952", custom_flexi_hp_account_number=value, applicant="C1")
		module.validate(doc)
	assert doc.custom_flexi_hp_account_number == "GRA2026118"


def test_number_used_by_another_customer_is_refused_only_when_entered_or_changed(acct):
	f, module = acct
	f.get_all = Mock(return_value=[Row(name="ACC-LOAP-2026-00369", applicant="C2", applicant_name="Mohammed Ashaf Ali")])
	with pytest.raises(ValueError, match="already used by ACC-LOAP-2026-00369"):
		module.validate(App(name="ACC-LOAP-2026-00960", custom_flexi_hp_account_number="GRA2026117", applicant="C1"))
	f.get_all.reset_mock()
	module.validate(App(name="ACC-LOAP-2026-00960", custom_flexi_hp_account_number="GRA2026117", applicant="C1", _changed=False))
	f.get_all.assert_not_called()


def test_same_customer_may_share_a_number(acct):
	f, module = acct
	f.get_all = Mock(return_value=[Row(name="ACC-LOAP-2026-00306", applicant="MALO", applicant_name="Malo Cafe")])
	module.validate(App(name="ACC-LOAP-2026-00336", custom_flexi_hp_account_number="GRA2026087", applicant="MALO"))


def test_guard_runs_on_save_and_after_submit():
	scope = {}
	exec(compile(ast.parse((ROOT / "hooks.py").read_text()), "<hooks>", "exec"), scope)
	events = scope["doc_events"]["Loan Application"]
	assert "comfort_homes.hp_lending.hp_account_number.validate" in events["validate"]
	assert "comfort_homes.hp_lending.hp_account_number.validate" in events["before_update_after_submit"]


# ---------------------------------------------------------------- Lending shortcut

@pytest.fixture
def ws(env):
	f, load = env
	return f, load("hp_lending/hp_workspace.py")


def test_shortcut_block_goes_after_the_last_shortcut_once(ws):
	_f, module = ws
	blocks = [{"type": "chart"}, {"type": "header"}, {"type": "shortcut", "data": {"shortcut_name": "Loan"}},
		{"type": "shortcut", "data": {"shortcut_name": "Collection Case"}}, {"type": "spacer"}]
	out, placed = module.place_shortcut(blocks, "Loan Bank Reconciliation")
	assert placed and out[4]["data"]["shortcut_name"] == "Loan Bank Reconciliation" and out[5]["type"] == "spacer"
	again, placed_again = module.place_shortcut(out, "Loan Bank Reconciliation")
	assert not placed_again and again == out


def test_ensure_lending_shortcuts_adds_once(ws):
	f, module = ws
	doc = Row(content=json.dumps([{"type": "shortcut", "data": {"shortcut_name": "Loan"}}]), shortcuts=[Row(label="Loan")],
		flags=SimpleNamespace(), save=Mock())
	doc.append = lambda table, values: doc[table].append(Row(values))
	f.db.exists = Mock(return_value=True)
	f.get_doc = Mock(return_value=doc)
	assert module.ensure_lending_shortcuts() == ["Loan Bank Reconciliation"]
	assert [s.label for s in doc.shortcuts] == ["Loan", "Loan Bank Reconciliation"]
	doc.save.assert_called_once_with(ignore_permissions=True)
	doc.save.reset_mock()
	assert module.ensure_lending_shortcuts() == [] and not doc.save.called


def test_shortcut_is_added_after_every_migrate():
	src = (ROOT / "webform_setup.py").read_text()
	assert "ensure_lending_shortcuts()" in src.split("def setup_after_sync")[1].split("def setup_web_form_and_print_formats")[0]


# ---------------------------------------------------------------- duplicate statement lines

@pytest.fixture
def api(env):
	f, load = env
	return f, load("api.py")


def test_line_identity_ignores_case_and_spacing(api):
	_f, module = api
	assert module.line_identity(" DIRECT TRANSFER  FROM 84672362\nACC - LOAP ") == "DIRECT TRANSFER FROM 84672362 ACC - LOAP"


def test_occurrence_index_counts_identical_lines(api):
	_f, module = api
	a = Row(transaction_date="2026-11-24", amount=50, description="SCH FUND TRANSFER X")
	b = Row(transaction_date="2026-11-24", amount=50, description="SCH FUND  TRANSFER X")
	c = Row(transaction_date="2026-11-24", amount=51, description="SCH FUND TRANSFER X")
	assert [module.occurrence_index([a, c, b], r) for r in (a, c, b)] == [0, 0, 1]


def _sql(rows, journals=()):
	def run(query, values=None, as_dict=False):
		if "tabLoan Bank Reconciliation Item" in query:
			return [Row(r) for r in rows]
		if "tabJournal Entry" in query:
			return [(j,) for j in journals]
		return []
	return Mock(side_effect=run)


def test_line_held_as_credit_in_an_earlier_reconciliation_is_recognised(api):
	# devcom 27-11: row 149 ($137, 24-11) had been held as credit on 00577 in reconciliation 8ensd7flh0
	f, module = api
	f.db.sql = _sql([dict(parent="8ensd7flh0", idx=7, description="DIRECT TRANSFER FROM 10436705 NAM2026125",
		loan_repayment=None, credit_entry="ACC-JV-2026-01002", fee_payment=None)])
	out = module.already_reconciled("8uqepvhf1o", "BSP", "2026-11-24", 137, "DIRECT TRANSFER FROM 10436705 NAM2026125")
	assert out == "Already held as credit (8ensd7flh0): ACC-JV-2026-01002"


def test_line_posted_to_a_different_loan_is_still_recognised(api):
	# row 153: Marisha's $46.34 went to 00922 by hand; the new import matched 00308 from the narration
	f, module = api
	f.db.sql = _sql([dict(parent="8ensd7flh0", idx=11, description="DIRECT TRANSFER FROM 84672362 ACC - LOAP-2026-00314",
		loan_repayment="LM-REP-1135", credit_entry=None, fee_payment=None)])
	out = module.already_reconciled("8uqepvhf1o", "BSP", "2026-11-23", 46.34, "DIRECT TRANSFER FROM 84672362 ACC - LOAP-2026-00314")
	assert out.startswith("Already reconciled") and "LM-REP-1135" in out


def test_unidentified_receipt_journalled_by_hand_is_recognised(api):
	f, module = api
	f.db.sql = _sql([dict(parent="8ensd7flh0", idx=9, description="IB FUNDS TRANSFER GRA2025246 R#49134",
		loan_repayment=None, credit_entry=None, fee_payment=None)], journals=["ACC-JV-2026-01145"])
	assert module.already_reconciled("8uqepvhf1o", "BSP", "2026-11-24", 143, "IB FUNDS TRANSFER GRA2025246 R#49134") == \
		"Already journalled: ACC-JV-2026-01145"


def test_second_identical_payment_on_the_same_day_is_not_a_duplicate(api):
	f, module = api
	f.db.sql = _sql([dict(parent="OLD", idx=1, description="SCH FUND TRANSFER X", loan_repayment="LM-REP-1", credit_entry=None, fee_payment=None)])
	assert module.already_reconciled("NEW", "BSP", "2026-11-24", 50, "SCH FUND TRANSFER X", 0)
	assert module.already_reconciled("NEW", "BSP", "2026-11-24", 50, "SCH FUND TRANSFER X", 1) == ""


def test_new_line_is_not_flagged(api):
	f, module = api
	f.db.sql = _sql([])
	assert module.already_reconciled("NEW", "BSP", "2026-11-27", 140, "IB FUNDS TRANSFER GRA2026277 Oct26 R#923078") == ""


def test_create_repayments_skips_a_line_already_dealt_with_even_if_ticked(api, monkeypatch):
	f, module = api
	row = Row(name="ROW", selected=1, loan="ACC-LOAN-2026-00577", suggested_loan="ACC-LOAN-2026-00577", loan_repayment=None,
		customer="CUST", transaction_date="2026-11-24", amount=137, description="DIRECT TRANSFER FROM 10436705 NAM2026125",
		statement_transaction_key="HP-BANK-NEW", customer_card_id="NAM2026125")
	doc = Row(name="8uqepvhf1o", company="Comfort Home Furnishing PTE Limited", docstatus=0, bank_account="BSP", transactions=[row],
		check_permission=Mock(), reload=Mock(), save=Mock())
	f.get_doc = Mock(return_value=Row(applicant="CUST", company="Comfort Home Furnishing PTE Limited", check_permission=Mock()))
	f.db.sql = _sql([dict(parent="8ensd7flh0", idx=7, description=row.description, loan_repayment=None,
		credit_entry="ACC-JV-2026-01002", fee_payment=None)])
	monkeypatch.setattr(module, "_payment_account", lambda *a: "1201")
	import comfort_homes.hp_lending.hp_collections as collections
	monkeypatch.setattr(collections, "cutover_date", lambda: date(2026, 11, 21))
	monkeypatch.setattr(collections, "wallet_account", lambda: "1206")
	import comfort_homes.hp_lending.hp_credit as credit
	allocate = Mock()
	monkeypatch.setattr(credit, "allocate_receipt", allocate)
	result = module.create_repayments(doc)
	assert result["created"] == 0 and result["skipped"] == 1 and not allocate.called
	assert row.selected == 0 and row.status.startswith("Already held as credit")


def test_both_bank_and_mpaisa_imports_use_the_check():
	api_src = (ROOT / "api.py").read_text()
	mpaisa_src = (ROOT / "hp_lending" / "hp_mpaisa.py").read_text()
	assert "already_reconciled(doc.name, doc.bank_account, transaction_date" in api_src
	assert "api.already_reconciled(doc.name, doc.bank_account" in mpaisa_src
