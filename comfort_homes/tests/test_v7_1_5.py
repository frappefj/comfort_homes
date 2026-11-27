"""7.1.5: HP account number before application number; receipts before cut-over (Finance Manager)."""
import json
from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]

# devcom 27-11-2026: the customer quotes her UAT application number ACC-LOAP-2026-00314. Devcom holds it as her
# HP account 2026-00314, while devcom application ACC-LOAP-2026-00314 is Viliame Ketewai's converted MYOB contract.
CUSTOMERS = {("custom_ch_account_reference_no", "2026-00314"): ["Marisha Marie"],
             ("custom_ch_account_reference_no", "2026-00322"): ["Vandhna Lata"],
             ("custom_ch_account_reference_no", "2026-00221"): ["Shayal Simran Ali"]}
LOANS = {"Marisha Marie": ["ACC-LOAN-2026-00922"], "Vandhna Lata": ["ACC-LOAN-2026-00920"], "Shayal Simran Ali": ["ACC-LOAN-2026-00866"],
         "Sonam Sanjana Kumar": ["ACC-LOAN-2026-00939"]}
BY_APPLICATION = {"ACC-LOAP-2026-00314": ("Viliame Ketewai", "ACC-LOAN-2026-00308", 1),
                  "ACC-LOAP-2026-00206": ("Kathleen Anette Mohammed", "ACC-LOAN-2026-00200", 1),
                  "ACC-LOAP-2026-00952": ("Sonam Sanjana Kumar", "ACC-LOAN-2026-00939", 0)}


@pytest.fixture
def api(env):
	f, load = env
	module = load("api.py")
	f.db.has_column = Mock(return_value=True)

	def get_all(doctype, filters=None, fields=None, **kwargs):
		filters = filters or {}
		if doctype == "Customer":
			(field, value), = list(filters.items())
			return [Row(name=n) for n in CUSTOMERS.get((field, value), [])]
		if doctype == "Loan" and "loan_application" in filters:
			hit = BY_APPLICATION.get(filters["loan_application"])
			return [Row(name=hit[1], applicant=hit[0])] if hit else []
		if doctype == "Loan" and "applicant" in filters:
			return [Row(name=n) for n in LOANS.get(filters["applicant"], [])]
		return []
	f.get_all = Mock(side_effect=get_all)
	f.db.get_value = Mock(side_effect=lambda dt, name, field=None, *a, **k: BY_APPLICATION.get(name, (0, 0, 0))[2]
		if dt == "Loan Application" else None)
	module._customer_name_from_description = Mock(return_value="")
	return f, module


def _match(module, narration):
	text = module.normalise_narration(narration)
	return module._customer_and_loan(text, module.extract_card_id(text), detail=True)


def test_quoted_uat_application_number_goes_to_the_hp_account_holder(api):
	_f, module = api
	assert _match(module, "DIRECT TRANSFER FROM 84672362 ACC - LOAP-2026-00314") == \
		("Marisha Marie", "ACC-LOAN-2026-00922", 1, "identifier")


def test_converted_application_number_alone_is_only_a_check(api):
	_f, module = api
	assert _match(module, "DIRECT TRANSFER FROM 80615181 ACC- LOAP-2026-00206")[3] == "check"


def test_new_contract_application_number_still_matches(api):
	_f, module = api
	assert _match(module, "IB FUNDS TRANSFER ACC-LOAP-2026-00952") == \
		("Sonam Sanjana Kumar", "ACC-LOAN-2026-00939", 1, "application")


def test_three_value_form_is_unchanged_for_existing_callers(api):
	_f, module = api
	text = module.normalise_narration("ACC-LOAP-2026-00314")
	assert module._customer_and_loan(text, "") == ("Marisha Marie", "ACC-LOAN-2026-00922", 1)


@pytest.mark.parametrize("narration,expected", [
	("SCH FUND TRANSFER FROM 80327759 2026 00322", "SCH FUND TRANSFER FROM 80327759 2026-00322"),
	("IB FUNDS TRANSFER 202600221 R#894126", "IB FUNDS TRANSFER 2026-00221 R#894126"),
	("DIRECT TRANSFER FROM 82127158 2026-00366", "DIRECT TRANSFER FROM 82127158 2026-00366"),
	("NAM2026 00051", "NAM2026 00051"),
	("FROM 82026 00322", "FROM 82026 00322"),
	("DIRECT TRANSFER FROM 10618708 2026040", "DIRECT TRANSFER FROM 10618708 2026040"),
])
def test_loosely_typed_hp_account_numbers(api, narration, expected):
	_f, module = api
	assert module.normalise_narration(narration) == expected


def test_spaced_and_run_together_hp_numbers_now_match(api):
	_f, module = api
	assert _match(module, "SCH FUND TRANSFER FROM 80327759 2026 00322")[:2] == ("Vandhna Lata", "ACC-LOAN-2026-00920")
	assert _match(module, "IB FUNDS TRANSFER 202600221 R#894126")[:2] == ("Shayal Simran Ali", "ACC-LOAN-2026-00866")


# ---------------------------------------------------------------- receipts before cut-over

def _create(api, monkeypatch, include, roles=("Finance Manager",)):
	f, module = api
	row = Row(name="ROW", selected=1, loan="L1", suggested_loan="L1", loan_repayment=None, customer="C1",
		transaction_date="2026-11-05", amount=50.0, description="DIRECT TRANSFER FROM 1 GRA2026001",
		statement_transaction_key="HP-BANK-K", customer_card_id="GRA2026001")
	doc = Row(name="R", company="Comfort Home Furnishing PTE Limited", docstatus=0, bank_account="BSP", transactions=[row],
		include_pre_cutover=int(include), check_permission=Mock(), reload=Mock(), save=Mock())
	f.get_doc = Mock(return_value=Row(applicant="C1", company="Comfort Home Furnishing PTE Limited", check_permission=Mock()))
	f.get_roles = Mock(return_value=list(roles))
	f.db.sql = Mock(return_value=[])
	f.db.get_value = Mock(return_value=None)
	monkeypatch.setattr(module, "_payment_account", lambda *a: "1201")
	monkeypatch.setattr(module, "_cost_center_for_loan", lambda *a: "HQ")
	import comfort_homes.hp_lending.hp_collections as collections
	monkeypatch.setattr(collections, "cutover_date", lambda: date(2026, 11, 21))
	monkeypatch.setattr(collections, "wallet_account", lambda: "1206")
	import comfort_homes.hp_lending.hp_credit as credit
	allocate = Mock(return_value={"loan_repayment": "LM-REP-1", "fee_payment": None, "credit_entry": None,
		"allocation": "instalments 50.00", "status": "Loan Repayment Created"})
	monkeypatch.setattr(credit, "allocate_receipt", allocate)
	import comfort_homes.hp_lending.hp_mpaisa as mpaisa
	monkeypatch.setattr(mpaisa, "remember_mobile", lambda *a: None)
	return module, doc, row, allocate


def test_pre_cutover_row_is_still_skipped_by_default(api, monkeypatch):
	module, doc, row, allocate = _create(api, monkeypatch, include=False)
	assert module.create_repayments(doc)["created"] == 0 and row.status == "Before cut-over (MYOB)" and not allocate.called


def test_pre_cutover_row_posts_on_the_cutover_date_when_included(api, monkeypatch):
	module, doc, row, allocate = _create(api, monkeypatch, include=True)
	assert module.create_repayments(doc)["created"] == 1
	assert allocate.call_args.args[3] == date(2026, 11, 21)  # value date = cut-over, not 5 Nov
	assert "received 2026-11-05, value-dated at cut-over" in row.allocation


def test_only_a_finance_manager_may_use_it(api, monkeypatch):
	module, doc, row, allocate = _create(api, monkeypatch, include=True, roles=("Accounts Officer",))
	f, _m = api
	f.get_doc = Mock(return_value=doc)
	with pytest.raises(ValueError, match="Only a Finance Manager"):
		module.loan_reconciliation("R", "create_repayments")


def test_field_is_on_the_reconciliation_and_mpaisa_honours_it():
	d = next(x for x in json.loads((ROOT / "fixtures" / "doctype.json").read_text()) if x["name"] == "Loan Bank Reconciliation")
	field = next(x for x in d["fields"] if x["fieldname"] == "include_pre_cutover")
	assert field["fieldtype"] == "Check" and "Finance Manager" in field["description"]
	src = (ROOT / "hp_lending" / "hp_mpaisa.py").read_text()
	assert "api.include_pre_cutover(doc)" in src and "api.CHECK_CUSTOMER" in src


# ---------------------------------------------------------------- Westpac narration

def test_westpac_reference_column_is_read_with_the_type(api):
	_f, module = api
	headers = ["Posted Date", "Effective Date", "Transaction Description", "Transaction Code Description", "Check Number",
		"Debit Amount", "Credit Amount"]
	table = module._table([headers, ["02 Nov 2026", "01 Nov 2026", "WIRE TRANSFER CREDIT", "NAM2026194", " ", "0", "120.00"]], "S")
	row = dict(zip(table["headers"], table["rows"][0]))
	assert table["mapping"]["detail"] == "Transaction Code Description"
	assert module.statement_description(row, table["mapping"]) == "WIRE TRANSFER CREDIT | NAM2026194"
	assert module._date(row[table["mapping"]["date"]]) == date(2026, 11, 1)


def test_bank_without_a_detail_column_is_unchanged(api):
	_f, module = api
	table = module._table([["Date", "Description", "Credit (FJD)"], ["01/11/2026", "DEP TRF GRA2025198", "160.00"]], "S")
	row = dict(zip(table["headers"], table["rows"][0]))
	assert module.statement_description(row, table["mapping"]) == "DEP TRF GRA2025198"
