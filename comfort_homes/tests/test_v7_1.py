"""7.1 Lean and Clean: server scripts as app code, merged Portfolio Control, Conversion Check credit, rebuild."""
import ast
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from comfort_homes.tests.patch_lists import retire_lists
from comfort_homes.tests.test_gross_controls import Row, env

ROOT = Path(__file__).resolve().parents[1]
D = Decimal


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


# ---------------------------------------------------------------- server scripts -> app code

def test_every_retired_script_is_moved_or_dormant():
	moved = {
		"Flexi Copy HP Items to Loan": ("Loan", "before_validate", "copy_hp_items_to_loan"),
		"Flexi Default Loan Disbursement Payment Method": ("Loan Disbursement", "before_validate", "default_disbursement_mode"),
		"Flexi Default Loan Repayment Payment Method": ("Loan Repayment", "before_validate", "default_repayment_mode"),
		"Flexi Create Customer From Loan Application": ("Loan Application", "before_validate", "create_customer"),
		"Flexi Prepare Loan Amount": ("Loan Application", "before_validate", "prepare_loan_amount"),
		"Flexi Default Customer Agreements": ("Loan Application", "validate", "default_customer_agreements"),
		"Loan Status Updation": ("Loan Application", "validate", "status_display"),
		"link documents to loan application": ("Loan Application", "on_update", "link_documents"),
		"Flexi Sync Prepared By From Assignment": ("ToDo", "on_update", "sync_prepared_by"),
	}
	events = _hooks()["doc_events"]
	for script, (doctype, event, fn) in moved.items():
		wired = events[doctype][event]
		wired = wired if isinstance(wired, list) else [wired]
		assert f"comfort_homes.flexi_events.{fn}" in wired, script
		# server scripts ran after the app's own hooks: the moved function is last
		if event == "before_validate" and doctype != "Loan Application":
			assert wired[-1] == f"comfort_homes.flexi_events.{fn}", script
	la = events["Loan Application"]["before_validate"]
	assert la[-2:] == ["comfort_homes.flexi_events.create_customer", "comfort_homes.flexi_events.prepare_loan_amount"]
	methods = {w["name"]: w["method"] for w in _hooks()["workflow_methods"]}
	assert methods["Flexi Loan Application Task"] == "comfort_homes.flexi_events.application_transition"
	assert set(moved) | {"Flexi Loan Application Workflow Task", "Dynamic Laon P"} <= set(retire_lists().SERVER_SCRIPTS)
	assert len(retire_lists().SERVER_SCRIPTS) == 30
	assert not (ROOT / "fixtures" / "server_script.json").exists()
	assert "Server Script" not in (ROOT / "hooks.py").read_text().split("fixtures = [")[1].split("doc_events")[0]


def test_patch_repoints_task_before_deleting_and_is_registered():
	src = (ROOT / "patches" / "v7_1" / "retire_scripts_and_reports.py").read_text()
	body = src.split("def execute():")[1]
	assert body.index("_repoint_transition_task()") < body.index('"Server Script"')
	assert "comfort_homes.patches.v7_1.retire_scripts_and_reports" in (ROOT / "patches.txt").read_text().split("[post_model_sync]")[1]
	assert set(retire_lists().REPORTS) == {"Flexi Manager Overview", "HP Customer Statement"}


def test_moved_functions_behave_like_the_scripts(env, monkeypatch):  # noqa: F811
	f, load = env
	ev = load("flexi_events.py")
	# Loan Status Updation
	doc = Row(status="Open")
	ev.status_display(doc)
	assert doc.custom_loan_status_display == "Open"
	# Flexi Prepare Loan Amount: web-form rate normalised, surcharge from items
	doc = Row(custom_flexi_interest_rate="22.000000000", custom_flexi_purchase_items=[Row(quantity=2, unit_price=500)],
		custom_flexi_cash_price=0, custom_flexi_surcharge_rate=10)
	ev.prepare_loan_amount(doc)
	assert doc.custom_flexi_interest_rate == "22" and doc.rate_of_interest == 22
	assert doc.custom_flexi_surcharge_amount == 100 and doc.custom_flexi_full_value == 1100
	# payment method mapping kept
	f.db.get_value = Mock(side_effect=["APP-1", "Cash / Direct Payment"])
	doc = Row(mode_of_payment=None, against_loan="L")
	ev.default_disbursement_mode(doc)
	assert doc.mode_of_payment == "Cash"
	# like Server Scripts, nothing runs during migrate or install
	monkeypatch.setattr(f, "flags", Row(in_migrate=True, in_install=False), raising=False)
	doc = Row(status="Open")
	ev.status_display(doc)
	assert not hasattr(doc, "custom_loan_status_display") or doc.custom_loan_status_display is None


def test_web_form_uses_the_app_loan_product_lookup():
	src = (ROOT / "flexi_events.py").read_text()
	assert "@frappe.whitelist(allow_guest=True" in src.split("def get_loan_products")[0].splitlines()[-1]
	body = src.split("def get_loan_products")[1]
	assert '"name", "rate_of_interest"' in body and '"disabled": 0' in body


# ---------------------------------------------------------------- Portfolio Control (merged)

from comfort_homes.tests.test_portfolio_control import (
	c,
)


@pytest.fixture
def rebuild(env, monkeypatch):  # noqa: F811
	f, load = env
	for name, value in (("now_datetime", lambda: datetime(2026, 11, 26, 9, 0)), ("cint", lambda v: int(v or 0))):
		if not hasattr(f.utils, name):
			monkeypatch.setattr(f.utils, name, value, raising=False)
	return load("hp_lending/hp_rebuild.py")


def test_portfolio_control_carries_the_overview_exceptions(c):  # noqa: F811
	now = datetime(2026, 11, 26, 9, 0)
	app = {"name": "A", "workflow_state": "LPO Approved", "custom_branchlocation": "Suva", "applicant_name": "X",
		"modified": now, "custom_hp_converted": 0}
	row = c._row(app, None, {}, {}, {}, {}, now, {}, {}, {}, {})
	assert "Missing LPO" in row["exceptions"] and "No loan" in row["exceptions"]
	converted = c._row({**app, "custom_hp_converted": 1}, None, {}, {}, {}, {}, now, {}, {}, {}, {})
	assert converted["exceptions"] == ""
	loan = {"name": "L", "status": "Disbursed", "docstatus": 1, "is_npa": 0, "total_payment": 100, "total_principal_paid": 0,
		"debit_adjustment_amount": 0, "credit_adjustment_amount": 0, "disbursed_amount": 100, "modified": now}
	full = c._row({**app, "workflow_state": "Active Loan Account"}, loan, {}, {}, {"A": [{"grand_total": 900, "outstanding_amount": 300}]},
		{}, now, {}, {"A": "2026-11-20"}, {"A": {"any": 1, "submitted": 1}}, {"L": {"independent": 5.0, "legacy": 2.5}})
	assert full["fees_due"] == 7.5 and full["chd_owed"] == 300
	assert full["exceptions"] == "Fee invoice linked to the loan (legacy); do not correct"
	assert not (ROOT / "comfort_homes" / "report" / "flexi_manager_overview").exists()


# ---------------------------------------------------------------- HP Conversion Check

def test_conversion_check_deducts_opening_customer_credit():
	src = (ROOT / "comfort_homes" / "report" / "hp_conversion_check" / "hp_conversion_check.py").read_text()
	assert "return round(opening - advance - _opening_credit(loan), 2)" in src
	body = src.split("def _opening_credit(")[1].split("\ndef ")[0]
	assert "HP-OPEN-CREDIT-{loan}" in body and "jea.account = %s" in body


# ---------------------------------------------------------------- rebuild in place

def _rows(start, n, step_days, amount):
	from datetime import timedelta
	return [(start + timedelta(days=step_days * i), amount) for i in range(n)]


@pytest.mark.parametrize("paid,overdue,advance,oldest,dpd", [
	(0, "200.00", "0.00", date(2026, 10, 1), 51),      # nothing paid: Oct and Nov due
	(100, "100.00", "0.00", date(2026, 11, 1), 20),    # one paid
	(200, "0.00", "0.00", None, 0),                    # exactly up to date
	(250, "0.00", "50.00", None, 0),                   # paid ahead
])
def test_opening_position_on_a_new_schedule(rebuild, paid, overdue, advance, oldest, dpd):
	r = rebuild
	rows = [(date(2026, 10, 1), 100), (date(2026, 11, 1), 100), (date(2026, 12, 1), 100)]
	pos = r.opening_position(300, 300 - paid, rows, date(2026, 11, 21))
	assert pos["overdue"] == D(overdue) and pos["advance"] == D(advance) and pos["oldest"] == oldest and pos["dpd"] == dpd
	assert pos["paid_against_due"] == D(paid) - D(advance)


def test_rows_on_the_migration_date_are_not_history(rebuild):
	r = rebuild
	rows = [(date(2026, 11, 7), 50), (date(2026, 11, 21), 50), (date(2026, 12, 5), 50)]
	pos = r.opening_position(150, 150, rows, date(2026, 11, 21))  # Lending: payment_date < migration date
	assert pos["overdue"] == D("50.00") and pos["oldest"] == date(2026, 11, 7)


def test_rebuild_is_gated_and_previewed_by_default():
	src = (ROOT / "hp_lending" / "hp_rebuild.py").read_text()
	api = src.split("def rebuild_schedules(")[1]
	assert 'frappe.only_for("System Manager")' in api and "dry_run=1" in src.split("def rebuild_schedules(")[1].split(")")[0]
	assert "frappe.db.rollback()" in api and "frappe.db.savepoint" not in api
	assert 'frappe.has_permission("Loan Repayment Schedule", "cancel")' in src
	after_verify = "\n".join(src.split("check = conv.verify_loan(name)")[1].split("\n")[:3])
	assert 'if not check["ok"]:' in after_verify and "frappe.throw" in after_verify
	# MYOB money is never written by the rebuild
	writes = "".join(l for l in src.splitlines() if "set_value(\"Loan\"" in l or 'set_value("Loan"' in l)
	for field in ("custom_hp_opening_outstanding", "custom_hp_opening_unearned", "custom_hp_opening_penalty",
			"custom_hp_opening_fees", "loan_amount"):
		assert field not in writes
