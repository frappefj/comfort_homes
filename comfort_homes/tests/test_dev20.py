"""dev20: repayment-method lock, tolerant bank matching, duplicate-receipt flag, Loan Application layout."""
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env  # noqa: F401  (fixture)
from comfort_homes.tests.test_dev19 import matcher

APP = Path(__file__).resolve().parents[1]
FIXTURES = APP / "fixtures"
LA = "Loan Application"


# ---------------------------------------------------------------- bank matching
@pytest.mark.parametrize("narration,expected", [
	("DIRECT TRANSFER FROM 85696687 LTK 2026078", "DIRECT TRANSFER FROM 85696687 LTK2026078"),
	("DEP TRF GRA 2026125", "DEP TRF GRA2026125"),
	("DIRECT TRANSFER FROM 82370769 NAM 2026063", "DIRECT TRANSFER FROM 82370769 NAM2026063"),
	("DIRECT TRANSFER FROM 81093886 ACC- LOAP 2026-00310", "DIRECT TRANSFER FROM 81093886 ACC-LOAP-2026-00310"),
	("SCH FUND TRANSFER FROM 10618708 ACCLOAP202600264", "SCH FUND TRANSFER FROM 10618708 ACC-LOAP-2026-00264"),
	("DIRECT TRANSFER FROM 87111585 Acc - Loap - 2026 - 00166", "DIRECT TRANSFER FROM 87111585 ACC-LOAP-2026-00166"),
	("DIRECT TRANSFER FROM 85687414 Acc-LOap2026-00197", "DIRECT TRANSFER FROM 85687414 ACC-LOAP-2026-00197"),
])
def test_loosely_typed_references_are_normalised(env, narration, expected):
	module = matcher(env)
	assert module.normalise_narration(narration) == expected


@pytest.mark.parametrize("narration", [
	"SCH FUND TRANSFER FROM 85959309 GRA2026194",   # already clean: unchanged
	"SALES GROSS SETTLMT 20027302 110 BEC COMFORT HOME FURNISHIN Suva",
	"SC 12 BANK FEE",                                # too few digits to be an account
	"2026-11-21 TRANSFER",
])
def test_ordinary_narrations_are_left_alone(env, narration):
	module = matcher(env)
	assert module.normalise_narration(narration) == narration


def test_spaced_hp_account_now_matches_customer_and_loan(env):
	module = matcher(env)
	text = module.normalise_narration("EFTPOS DEP NAM 202505")
	assert module._customer_and_loan(text, module.extract_card_id(text))[:2] == ("Adi Drodro", "LOAN-1")


def _import_env(env, monkeypatch, duplicate):
	f, load = env
	module = load("api.py")
	content = "Date,Description,Credit\n24/11/2026,DIRECT TRANSFER FROM 85129016 DEN2026009,184\n"
	monkeypatch.setattr(module, "_file", lambda doc: (content.encode(), "s.csv"))
	monkeypatch.setattr(module, "_customer_and_loan", lambda text, card: ("Baniua", "LOAN-486", 1))
	monkeypatch.setattr(module, "_possible_duplicate", lambda *a: duplicate)
	doc = Row(name="RECO", bank_account="BANK", transactions=[], check_permission=Mock(), save=Mock())
	doc.append = lambda field, row: doc["transactions"].append(Row(row))
	doc.update = lambda values: dict.update(doc, values)
	return module, doc


def test_receipt_already_posted_is_flagged_and_not_ticked(env, monkeypatch):
	module, doc = _import_env(env, monkeypatch, "LM-REP-0963")
	result = module.import_statement(doc, None)
	row = doc["transactions"][0]
	assert row["status"] == "Possible Duplicate of LM-REP-0963" and row["selected"] == 0 and row["loan"] == "LOAN-486"
	assert result == {"imported": 1, "ready": 0, "review": 1, "format": "s.csv"}


def test_new_receipt_is_ready_as_before(env, monkeypatch):
	module, doc = _import_env(env, monkeypatch, "")
	module.import_statement(doc, None)
	row = doc["transactions"][0]
	assert row["status"] == "Ready" and row["selected"] == 1


def test_duplicate_check_excludes_the_same_statement_row(env):
	f, load = env
	module = load("api.py")
	f.db.sql = Mock(return_value=[("LM-REP-1",)])
	assert module._possible_duplicate("LOAN", "2026-11-24", 184, "HP-BANK-KEY") == "LM-REP-1"
	sql, params = f.db.sql.call_args.args
	assert "docstatus = 1" in sql and "reference_number" in sql and params == ("LOAN", "2026-11-24", 184, "HP-BANK-KEY")


# ---------------------------------------------------------------- repayment method
def _mapping(env):
	f, load = env
	return load("loan_mapping.py")


@pytest.mark.parametrize("values,expected", [
	({"docstatus": 0, "company": "Comfort Home Furnishing PTE Limited", "repayment_method": "Repay Fixed Amount per Period"}, "Repay Over Number of Periods"),
	({"docstatus": 0, "company": "", "repayment_method": ""}, "Repay Over Number of Periods"),
	({"docstatus": 1, "company": "Comfort Home Furnishing PTE Limited", "repayment_method": "Repay Fixed Amount per Period"}, "Repay Fixed Amount per Period"),
	({"docstatus": 0, "company": "Comfort Home Furnishing PTE Limited", "custom_hp_converted": 1, "repayment_method": "Repay Fixed Amount per Period"}, "Repay Fixed Amount per Period"),
	({"docstatus": 0, "company": "Another Company", "repayment_method": "Repay Fixed Amount per Period"}, "Repay Fixed Amount per Period"),
])
def test_flexi_draft_applications_repay_over_number_of_periods(env, monkeypatch, values, expected):
	import sys, types
	settings = types.ModuleType("comfort_homes.hp_lending.hp_settings")
	settings.COMPANY = "Comfort Home Furnishing PTE Limited"
	monkeypatch.setitem(sys.modules, "comfort_homes.hp_lending.hp_settings", settings)
	module = _mapping(env)
	doc = Row(values)
	module.enforce_repayment_method(doc)
	assert doc.get("repayment_method") == expected


def test_repayment_method_rule_runs_first_on_loan_application():
	hooks = (APP / "hooks.py").read_text()
	block = hooks[hooks.index('"Loan Application": {'):]
	before_validate = block[block.index('"before_validate": ['):block.index("]", block.index('"before_validate": ['))]
	assert before_validate.index("enforce_repayment_method") < before_validate.index("calculate_financials")


# ---------------------------------------------------------------- layout fixtures
def _json(name):
	return json.loads((FIXTURES / name).read_text())


def _setters():
	return {p["name"]: p for p in _json("property_setter.json")}


def test_desk_repayment_method_is_read_only_with_default():
	ps = _setters()
	assert ps[f"{LA}-repayment_method-read_only"]["value"] == "1"
	assert ps[f"{LA}-repayment_method-default"]["value"] == "Repay Over Number of Periods"


def test_web_form_locks_method_and_calculated_figures():
	fields = {f.get("fieldname"): f for f in _json("web_form.json")[0]["web_form_fields"]}
	assert fields["repayment_method"]["read_only"] and fields["repayment_method"]["default"] == "Repay Over Number of Periods"
	for name in ("custom_flexi_cash_price", "custom_flexi_net_amount_financed", "custom_flexi_total_receivable",
			"custom_flexi_term_charges", "custom_flexi_weekly_instalment_amount", "custom_flexi_monthly_instalments"):
		assert fields[name]["read_only"], name
	for name in ("custom_flexi_deposit_amount", "custom_flexi_deposit_percent", "custom_flexi_documentation_fee",
			"custom_flexi_stamp_duty_fee", "custom_flexi_other_fee", "custom_flexi_term_months", "custom_flexi_payment_period"):
		assert not fields[name].get("read_only"), name  # applicant inputs stay editable


def test_field_order_places_every_custom_field_once_and_ends_with_connections():
	order = json.loads(_setters()[f"{LA}-main-field_order"]["value"])
	custom = {x["fieldname"] for x in _json("custom_field.json") if x["dt"] == LA}
	assert len(order) == len(set(order))
	assert custom <= set(order), sorted(custom - set(order))
	assert order[-1] == "connections_tab"
	deposit, tab = order.index("custom_flexi_deposit_amount"), order.index("custom_flexi_f4_tab")
	assert tab < deposit < order.index("custom_flexi_approval_tab")


def test_every_custom_field_anchor_exists():
	order = set(json.loads(_setters()[f"{LA}-main-field_order"]["value"]))
	for x in _json("custom_field.json"):
		if x["dt"] == LA and x.get("insert_after"):
			assert x["insert_after"] in order, (x["fieldname"], x["insert_after"])


def test_every_shipped_property_setter_is_registered_in_hooks():
	hooks = (APP / "hooks.py").read_text()
	for name in _setters():
		assert f'"{name}"' in hooks, name
