"""dev22: interest pause after 30 days past due, closure true-up of deferred interest, no self-approval."""
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
APPROVALS = {"Approve", "Director Approve", "Compliance Sign-off", "Director Compliance Sign-off", "Approve LPO"}


def _settings(entry_exists=lambda *a, **k: False):
	return SimpleNamespace(COMPANY="Comfort Home Furnishing PTE Limited", ACC_DEF_INTEREST="2013 - Def",
		ACC_INTEREST_INCOME="4001 - Int", ACC_DEF_PENALTY="2015 - Pen", INTEREST_PAUSE_DPD=30,
		entry_exists=entry_exists)


def test_pause_threshold_is_thirty_days():
	src = (ROOT / "hp_lending" / "hp_settings.py").read_text()
	assert "INTEREST_PAUSE_DPD = 30" in src
	for name in ("hp_interest.py", "hp_conversion.py"):
		text = (ROOT / "hp_lending" / name).read_text()
		assert "> s.INTEREST_PAUSE_DPD" in text
		assert "days_past_due\") or 0) > 0" not in text and "days_past_due or 0) > 0" not in text


def test_release_skips_only_loans_over_thirty_days(env, monkeypatch):
	f, load = env
	module = load("hp_lending/hp_interest.py")
	monkeypatch.setattr(module, "s", _settings())
	monkeypatch.setattr(module, "released_to_date", lambda loan: 0)
	loans = [Row(name=n, loan_application="APP-" + n, cost_center="CC", posting_date=date(2026, 6, 1)) for n in ("L0", "L30", "L31")]
	dpd = {"L0": 0, "L30": 30, "L31": 31}
	f.get_all = Mock(return_value=loans)

	def get_value(doctype, name, field=None, *args, **kwargs):
		if doctype == "Loan":
			return dpd[name]
		if doctype == "Loan Application":
			return Row(custom_flexi_term_charges=1200, custom_flexi_term_months=12)
		return date(2026, 6, 1)
	f.db.get_value = Mock(side_effect=get_value)
	posted = []
	f.get_doc = Mock(side_effect=lambda d: posted.append(d) or Mock())
	released = module.release_deferred_interest(as_of="2026-11-30")
	assert released == ["L0", "L30"]
	assert all(p["accounts"][0]["debit_in_account_currency"] == 600.0 for p in posted)  # 6 months x 100, catch-up


@pytest.mark.parametrize("closure,maturity,rebate,balance,expected", [
	(date(2026, 12, 1), date(2026, 12, 1), False, 13.19, "Release"),       # paid on the last instalment date
	(date(2027, 1, 5), date(2026, 12, 1), False, 40.0, "Release"),         # paid late, after maturity
	(date(2026, 9, 1), date(2026, 12, 1), False, 120.0, "Rebate owed"),   # early payoff, rebate owed
	(date(2026, 9, 1), date(2026, 12, 1), True, 3.5, "Release"),           # rebate already booked
	(date(2026, 9, 1), date(2026, 12, 1), False, 0, "Nothing deferred"),
	(date(2026, 9, 1), None, False, 5.0, "Release"),                       # no schedule found: no rebate basis
])
def test_closure_classification(env, closure, maturity, rebate, balance, expected):
	_, load = env
	module = load("hp_lending/hp_closure.py")
	assert module.classify(closure, maturity, rebate, balance) == expected


def test_closure_release_posts_once_and_waits_for_rebate(env, monkeypatch):
	f, load = env
	module = load("hp_lending/hp_closure.py")
	done = {"L-DONE"}
	monkeypatch.setattr(module, "s", _settings(entry_exists=lambda kind, loan, tag=None: loan in done))
	loans = [Row(name=n, applicant="C", loan_application="A", cost_center="CC", closure_date=d)
		for n, d in (("L-MATURED", date(2026, 12, 1)), ("L-EARLY", date(2026, 9, 1)), ("L-DONE", date(2026, 12, 1)), ("L-ZERO", date(2026, 12, 1)))]
	monkeypatch.setattr(module, "_closed_loans", lambda: loans)
	monkeypatch.setattr(module, "deferred_left", lambda loan: {"L-MATURED": 13.19, "L-EARLY": 120, "L-DONE": 5, "L-ZERO": 0}[loan])
	monkeypatch.setattr(module, "contract_maturity", lambda loan: date(2026, 12, 1))
	monkeypatch.setattr(module, "rebate_booked", lambda loan: False)
	posted = []
	f.get_doc = Mock(side_effect=lambda d: posted.append(d) or Mock())
	assert module.release_on_closure() == ["L-MATURED"]
	je = posted[0]
	assert je["custom_hp_entry_type"] == "Closure Release" and je["posting_date"] == date(2026, 12, 1)
	assert je["accounts"][0] == {"account": "2013 - Def", "debit_in_account_currency": 13.19}
	assert je["accounts"][1]["account"] == "4001 - Int" and je["accounts"][1]["credit_in_account_currency"] == 13.19
	assert "HP-CLOSE-L-MATURED" in je["user_remark"]


def test_closure_job_scheduled_after_penalty_realisation():
	hooks = (ROOT / "hooks.py").read_text()
	pen = hooks.index("hp_penalties.realise_closed_loan_penalties")
	assert hooks.index("hp_closure.release_on_closure") > pen


def test_entry_type_option_added():
	field = [c for c in json.loads((ROOT / "fixtures" / "custom_field.json").read_text())
		if c["name"] == "Journal Entry-custom_hp_entry_type"][0]
	assert "Closure Release" in field["options"].split("\n")


def test_no_self_approval_on_approval_steps_only():
	wf = [w for w in json.loads((ROOT / "fixtures" / "zz_workflow.json").read_text()) if w["name"] == "Loan Application Workflow-1"][0]
	approvals = [t for t in wf["transitions"] if t["action"] in APPROVALS]
	assert len(approvals) == 6 and all(t["allow_self_approval"] == 0 for t in approvals)
	others = [t for t in wf["transitions"] if t["action"] not in APPROVALS]
	assert others and all(t["allow_self_approval"] == 1 for t in others)


def test_closure_review_report_is_standard_and_role_gated():
	base = ROOT / "comfort_homes" / "report" / "hp_closure_review"
	meta = json.loads((base / "hp_closure_review.json").read_text())
	assert meta["is_standard"] == "Yes" and meta["module"] == "Comfort Homes"
	assert {r["role"] for r in meta["roles"]} == {"Director", "Finance Manager", "Accounts Manager", "Accounts Officer"}
	assert (base / "__init__.py").exists() and (base / "hp_closure_review.js").exists()
