"""Consistency checks that need no Frappe site: Chart of Accounts file, account keys, permission matrix."""

import csv
import importlib.util
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(__file__))


def _load(path, name):
	sys.modules.setdefault("frappe", types.ModuleType("frappe"))
	frappe = sys.modules["frappe"]
	frappe.whitelist = getattr(frappe, "whitelist", lambda *a, **k: (lambda f: f))
	spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, path))
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def _coa():
	with open(os.path.join(ROOT, "setup_data", "chart_of_accounts_flexi.csv"), newline="") as f:
		return list(csv.DictReader(f))


def test_chart_numbers_are_unique_and_parents_exist():
	rows = _coa()
	numbers = [r["Account Number"] for r in rows]
	assert len(numbers) == len(set(numbers))
	assert all(r["Parent Account Number"] in numbers for r in rows if r["Parent Account Number"])
	assert {r["Root Type"] for r in rows if r["Root Type"]} == {"Asset", "Liability", "Equity", "Income", "Expense"}


def test_every_account_the_app_posts_to_is_in_the_chart():
	settings = _load("hp_lending/hp_settings.py", "hp_settings_t")
	rows = {r["Account Number"]: r for r in _coa()}
	for key, number in settings.ACCOUNT_NUMBERS.items():
		assert number in rows, key
		assert rows[number]["Is Group"] == "0", key
	assert rows[settings.ACCOUNT_NUMBERS["ACC_DEPOSITS"]]["Account Type"] == "Receivable"
	assert rows[settings.ACCOUNT_NUMBERS["ACC_RECEIVABLE"]]["Account Type"] == "Receivable"


def test_lending_required_accounts_exist():
	numbers = {r["Account Number"] for r in _coa()}
	assert {"1105", "1106", "1107", "1108", "5029", "5030", "4016", "4006", "2008"} <= numbers


def test_nobody_can_delete_and_duties_are_separated():
	perms = _load("hp_lending/hp_permissions.py", "hp_permissions_t")
	for doctype, grants in perms.MATRIX.items():
		for role, letters in grants.items():
			assert "d" not in letters, (doctype, role)
	m = perms.MATRIX
	assert "s" not in m["Purchase Invoice"].get("Disbursement Officer", "")
	assert "Disbursement Officer" not in m["Payment Entry"]
	assert "s" not in m["Loan Disbursement"].get("Accounts Officer", "")
	assert "Guest" not in {r for g in m.values() for r in g}
