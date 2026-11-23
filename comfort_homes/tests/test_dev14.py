"""dev14: MYOB conversion on Lending's native import route (hp_conversion)."""
import ast
import csv
import hashlib
import json
import sys
from datetime import date
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import COMPANY, Row, env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
PINNED = ("hp_lending/hp_interest.py", "hp_lending/hp_penalties.py", "hp_lending/hp_lifecycle.py", "hp_lending/hp_settlement.py")


def _hooks():
	tree = ast.parse((ROOT / "hooks.py").read_text())
	values = {}
	for node in tree.body:
		if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
			try:
				values[node.targets[0].id] = ast.literal_eval(node.value)
			except ValueError:
				pass
	return values


def _fields():
	return {x["name"]: x for x in json.loads((ROOT / "fixtures" / "custom_field.json").read_text())}


class LoanDoc(Row):
	def db_set(self, field, value, update_modified=True):
		self[field] = value


def _conversion(env, monkeypatch):
	f, load = env
	module = load("hp_lending/hp_conversion.py")
	monkeypatch.setattr(module, "s", SimpleNamespace(
		COMPANY=COMPANY, account=lambda key: {"ACC_DEF_INTEREST": "2013 - DI", "ACC_DEF_PENALTY": "2015 - DP"}[key],
		ACC_DEF_INTEREST="2013 - DI", ACC_DEF_PENALTY="2015 - DP", ACC_INTEREST_INCOME="4001 - II",
		entry_exists=Mock(return_value=False)))

	def get_value(doctype, filters=None, fieldname=None, *a, **k):
		if doctype == "Account":
			return filters["account_number"] + " - ACC"
		if doctype == "Loan Application":
			return Row(company=COMPANY, applicant="CUST", custom_hp_converted=1)
		return None
	f.db.get_value = Mock(side_effect=get_value)
	f.db.set_value = Mock()
	created = []

	def get_doc(values, *a):
		doc = Mock()
		doc.values = values
		created.append(values)
		return doc
	f.get_doc = Mock(side_effect=get_doc)
	return f, module, created


def _loan(**extra):
	values = dict(name="LOAN-1", company=COMPANY, custom_hp_converted=1, is_imported=1, migration_date="2026-11-20",
		applicant="CUST", cost_center="Namaka - Flexi", loan_product="HP-CONV", custom_hp_application="APP-1",
		custom_hp_opening_unearned=120.50, custom_hp_opening_penalty=12.30, custom_hp_opening_fees=25.00,
		custom_hp_opening_advance=0, custom_hp_oldest_unpaid_date=None)
	values.update(extra)
	return LoanDoc(values)


def test_pinned_files_are_unchanged_from_dev13():
	manifest = json.loads((ROOT.parent / "RELEASE_MANIFEST.json").read_text())
	baseline = json.loads((ROOT / "tests" / "reference" / "dev13_pinned_hashes.json").read_text())
	for rel in PINNED:
		digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
		assert digest == baseline[rel], rel
		assert manifest["files"]["comfort_homes/" + rel] == digest


def test_hooks_wire_conversion_without_touching_pinned_paths():
	hooks = _hooks()
	events = hooks["doc_events"]
	assert events["Loan"]["on_submit"] == "comfort_homes.hp_lending.hp_conversion.on_loan_submit"
	assert events["Loan Repayment"]["validate"] == "comfort_homes.hp_lending.hp_conversion.guard_repayment_account"
	assert events["Loan Disbursement"]["on_submit"] == "comfort_homes.hp_lending.hp_lifecycle.on_disbursement_submit"
	schedule = hooks["scheduler_events"]
	assert "comfort_homes.hp_lending.hp_penalties.assess_penalties" in schedule["daily_long"]
	assert "comfort_homes.hp_lending.hp_conversion.assess_converted_penalties" in schedule["daily_long"]
	assert schedule["cron"]["30 2 1 * *"] == [
		"comfort_homes.hp_lending.hp_interest.release_deferred_interest",
		"comfort_homes.hp_lending.hp_conversion.release_converted_interest",
	]


def test_fixture_fields_and_opening_entry_type():
	fields = _fields()
	for name in ("Loan-custom_hp_converted", "Loan-custom_hp_application", "Loan-custom_hp_myob_invoice",
			"Loan-custom_hp_charges_active", "Loan-custom_hp_opening_outstanding", "Loan-custom_hp_opening_unearned",
			"Loan-custom_hp_opening_penalty", "Loan-custom_hp_opening_fees", "Loan-custom_hp_opening_advance",
			"Loan-custom_hp_oldest_unpaid_date", "Loan-custom_hp_dpd_at_migration", "Loan-custom_hp_review_reason",
			"Loan Application-custom_hp_converted", "Loan Application-custom_hp_myob_invoice",
			"Loan Application-custom_hp_opening_outstanding", "Loan Application-custom_hp_opening_unearned"):
		assert name in fields, name
	assert fields["Loan-custom_hp_charges_active"]["allow_on_submit"] == 1
	assert "Opening Balance" in fields["Journal Entry-custom_hp_entry_type"]["options"].split("\n")
	# every insert_after target exists
	names = {(x["dt"], x["fieldname"]) for x in fields.values()}
	for x in fields.values():
		if x["fieldname"].startswith("custom_hp_") and x["dt"] in ("Loan", "Loan Application") and x["name"].split("-")[1] in (
				"custom_hp_converted", "custom_hp_application"):
			assert (x["dt"], x["insert_after"]) in names or x["insert_after"] in ("rate_of_interest",)


def test_conversion_setup_creates_2016_not_the_approved_chart(env, monkeypatch):
	f, module, created = _conversion(env, monkeypatch)
	rows = {r["Account Number"] for r in csv.DictReader((ROOT / "setup_data" / "chart_of_accounts_flexi.csv").open(newline=""))}
	assert "2016" not in rows  # approved chart untouched
	(acc,) = module.CONVERSION_ACCOUNTS
	assert acc["account_number"] == "2016" and acc["account_type"] == "Bank" and "9808559661" in acc["account_name"]
	base = f.db.get_value.side_effect
	f.db.get_value = Mock(side_effect=lambda dt, flt=None, fn=None, *a, **k:
		None if dt == "Account" and flt.get("account_number") == "2016"
		else Row(rate_of_interest=0, payment_account="1910 - ACC", penalty_interest_rate=0) if dt == "Loan Product"
		else base(dt, flt, fn))
	f.db.exists = Mock(return_value=True)
	plan = module.setup_conversion(dry_run=1)["plan"]
	assert any("2016" in p for p in plan) and created == []


def test_interest_due_counts_myob_earned_as_released(env, monkeypatch):
	_, module, _ = _conversion(env, monkeypatch)
	# T 1,200 over 12 months; MYOB earned 6 months (600), so 600 unearned at cut-over.
	assert module.interest_due(1200, 600, 12, 6, 0) == 0
	assert module.interest_due(1200, 600, 12, 7, 0) == 100
	assert module.interest_due(1200, 600, 12, 7, 100) == 0
	assert module.interest_due(1200, 600, 12, 12, 400) == 200  # final month takes the remainder
	assert module.interest_due(1200, 600, 12, 30, 600) == 0


def test_non_converted_loan_is_untouched(env, monkeypatch):
	f, module, created = _conversion(env, monkeypatch)
	module.on_loan_submit(_loan(custom_hp_converted=0))
	assert created == [] and not f.db.set_value.called


def test_converted_submit_posts_opening_position_once(env, monkeypatch):
	f, module, created = _conversion(env, monkeypatch)
	f.db.exists = Mock(return_value=False)
	doc = _loan()
	module.on_loan_submit(doc)
	assert doc.loan_application == "APP-1"
	je_int, lba, je_fee = created
	assert je_int["custom_hp_entry_type"] == "Opening Balance" and je_int["custom_hp_loan"] == "LOAN-1"
	assert je_int["accounts"][0] == {"account": "1910 - ACC", "debit_in_account_currency": 120.5, "cost_center": "Namaka - Flexi"}
	assert je_int["accounts"][1] == {"account": "2013 - DI", "credit_in_account_currency": 120.5}
	assert "[HP-OPEN-INT-LOAN-1]" in je_int["user_remark"] and je_int["posting_date"] == "2026-11-20"
	assert lba["reference_number"] == "HP-PEN-LOAN-1-OPEN" and lba["adjustment_account"] == "2015 - DP"
	assert lba["adjustment_type"] == "Debit Adjustment" and lba["amount"] == 12.3
	assert je_fee["accounts"][0]["party"] == "CUST" and je_fee["accounts"][0]["account"] == "1120 - ACC"
	assert je_fee["accounts"][1]["account"] == "1910 - ACC"
	assert not f.flags.hp_conversion  # flag restored
	# second run: everything already exists
	created.clear()
	f.db.exists = Mock(return_value=True)
	module.on_loan_submit(_loan(loan_application="APP-1"))
	assert created == []


def test_paid_to_date_then_paid_ahead(env, monkeypatch):
	f, module, created = _conversion(env, monkeypatch)
	f.flags.in_import = None
	seen = []
	real = f.get_doc.side_effect
	f.get_doc = Mock(side_effect=lambda values, *a: (seen.append(getattr(f.flags, "in_import", None)), real(values))[1])
	module.on_loan_submit(_loan(custom_hp_opening_unearned=0, custom_hp_opening_penalty=0, custom_hp_opening_fees=0,
		loan_amount=2248.53, custom_hp_opening_outstanding=608.78, custom_hp_opening_advance=126.35))
	paid, advance = created
	# paid before cut-over against instalments due = 2248.53 - 608.78 - 126.35
	assert paid["is_imported"] == 1 and paid["amount_paid"] == paid["principal_amount_paid"] == 1513.40
	assert paid["reference_number"] == "HP-OPEN-PAID-LOAN-1" and paid["repayment_type"] == "Normal Repayment"
	assert advance["repayment_type"] == "Advance Payment" and advance["amount_paid"] == 126.35
	assert advance["payment_account"] == "1910 - ACC" and advance["reference_number"] == "HP-OPEN-ADV-LOAN-1"
	assert f.flags.in_import is None  # restored after the imported repayment


def test_paid_to_date_skipped_for_brand_new_balance(env, monkeypatch):
	_, module, created = _conversion(env, monkeypatch)
	module.on_loan_submit(_loan(custom_hp_opening_unearned=0, custom_hp_opening_penalty=0, custom_hp_opening_fees=0,
		loan_amount=1000, custom_hp_opening_outstanding=1000, custom_hp_opening_advance=0))
	assert created == []


def test_arrears_demand_is_dated_at_oldest_unpaid_instalment(env, monkeypatch):
	f, module, created = _conversion(env, monkeypatch)
	lending = ModuleType("lending.loan_management.doctype.loan.loan")
	lending.update_days_past_due_in_loans = Mock()
	for name in ("lending", "lending.loan_management", "lending.loan_management.doctype", "lending.loan_management.doctype.loan"):
		monkeypatch.setitem(sys.modules, name, ModuleType(name))
	monkeypatch.setitem(sys.modules, "lending.loan_management.doctype.loan.loan", lending)
	module.on_loan_submit(_loan(custom_hp_opening_unearned=0, custom_hp_opening_penalty=0, custom_hp_opening_fees=0,
		custom_hp_oldest_unpaid_date="2026-06-11"))
	args = f.db.set_value.call_args
	assert args.args[0] == "Loan Demand" and args.args[1]["is_imported"] == 1 and args.args[3] == date(2026, 6, 11)
	assert lending.update_days_past_due_in_loans.call_args.kwargs["force_update_dpd_in_loan"] is True


def test_converted_loan_must_be_imported(env, monkeypatch):
	_, module, _ = _conversion(env, monkeypatch)
	with pytest.raises(ValueError, match="imported"):
		module.on_loan_submit(_loan(is_imported=0))


def test_repayment_guard(env, monkeypatch):
	f, module, _ = _conversion(env, monkeypatch)
	base = f.db.get_value.side_effect
	f.db.get_value = Mock(side_effect=lambda dt, flt=None, fn=None, *a, **k: 1 if dt == "Loan" else base(dt, flt, fn))
	with pytest.raises(ValueError, match="bank or cash"):
		module.guard_repayment_account(Row(against_loan="LOAN-1", payment_account="1910 - ACC"))
	module.guard_repayment_account(Row(against_loan="LOAN-1", payment_account="1201 - BSP"))
	f.flags.hp_conversion = True
	module.guard_repayment_account(Row(against_loan="LOAN-1", payment_account="1910 - ACC"))


def test_workflow_gate_skips_converted_application(env):
	f, load = env
	module = load("hp_lending/hp_workflow.py")
	doc = Row(company=COMPANY, custom_hp_converted=1, workflow_state="Closed")
	doc.get_doc_before_save = Mock()
	module.validate_progress(doc)
	doc.get_doc_before_save.assert_not_called()


def test_conversion_report_registered():
	folder = ROOT / "comfort_homes" / "report" / "hp_conversion_check"
	meta = json.loads((folder / "hp_conversion_check.json").read_text())
	assert meta["report_type"] == "Script Report" and meta["ref_doctype"] == "Loan"
	assert (folder / "hp_conversion_check.py").exists() and (folder / "__init__.py").exists()


def test_loader_normalises_extraction_dates(env, monkeypatch):
	_, module, _ = _conversion(env, monkeypatch)
	assert module._iso({"a": "20-11-2026", "b": ["02-07-2025", "x"], "c": 3}) == {"a": "2026-11-20", "b": ["2025-07-02", "x"], "c": 3}


def test_loader_types_numbers_but_keeps_identifiers_as_text(env, monkeypatch):
	_, module, _ = _conversion(env, monkeypatch)
	row = module._iso({"loan_amount": "4261.55", "repayment_periods": "24", "custom_tin_number": "22988906",
		"customer": {"mobile_no": "9120393", "custom_ch_account_reference_no": "GRA2026085"}})
	assert row == {"loan_amount": 4261.55, "repayment_periods": 24, "custom_tin_number": "22988906",
		"customer": {"mobile_no": "9120393", "custom_ch_account_reference_no": "GRA2026085"}}
