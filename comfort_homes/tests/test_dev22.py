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


# ---------------------------------------------------------------- batch 2: contract truth and fees

PF = {x["name"]: x["html"] for x in json.loads((ROOT / "fixtures" / "print_format.json").read_text())}


def test_contracts_print_the_product_default_charge_not_a_hard_coded_rate():
	hp, s1, sofo = PF["Hire Purchase Agreement"], PF["Schedule 1"], PF["Summary of Financial Obligations"]
	assert "2.2%" not in hp and "custom_default_rate" not in s1
	for html in (hp, s1):
		assert "custom_hp_default_rate_monthly" in html and "dc_rate * dc_factor" in html
		assert "No default charge is charged on a default charge." in html
	assert "Default Charge" in sofo and "custom_hp_default_rate_monthly" in sofo


@pytest.mark.parametrize("period,count,amount,per_period,unit", [
	("Weekly", 78, 32.17, "0.58", "week"), ("Fortnightly", 52, 43.24, "1.15", "fortnight"), ("Monthly", 24, 93.69, "2.50", "month")])
def test_schedule_1_renders_actual_frequency_and_per_period_charge(period, count, amount, per_period, unit):
	import re
	import jinja2
	h = PF["Schedule 1"]
	sets = h[h.index("{% set period_label"):h.index("%}", h.index("{% set dc_unit")) + 2]
	f_block = h[h.index("{% set total_receivable"):h.index("</p>", h.index("{% set total_receivable")) + 4]
	l_block = h[h.index("<p>III. If the Hirer"):h.index("</p>", h.index("<p>III. If the Hirer")) + 4]
	key = period.lower()

	class Doc(dict):
		__getattr__ = dict.get
	doc = Doc({"custom_flexi_payment_period": period, f"custom_flexi_{key}_instalments": count,
		f"custom_flexi_{key}_instalment_amount": amount, "custom_flexi_total_receivable": 2248.53,
		"custom_flexi_term_months": 18, "custom_hp_default_rate_monthly": 2.5, "custom_hp_default_grace_days": 7,
		"custom_hp_default_stop_dpd": 90})
	money = '{% macro money(v) %}{{ "{:,.2f}".format((v or 0)|float) }}{% endmacro %}'
	text = re.sub(r"<[^>]+>", "", jinja2.Environment().from_string(money + sets + f_block + l_block).render(doc=doc))
	final = round(2248.53 - amount * (count - 1), 2)
	assert f"{count} {key} instalments over 18 months: {count - 1} instalments of $ {amount:,.2f}" in text
	assert f"final instalment of $ {final:,.2f}" in text
	assert f"2.50% per month" in text and f"at {per_period}% per {unit} for {key} repayments" in text
	assert "more than 7 days" in text and "90 days in arrears" in text


def test_fee_schedule_matches_system_rates():
	sofo = PF["Summary of Financial Obligations"]
	assert "Bailiff" not in sofo and "50 cents" not in sofo and "$1.00 per call" not in sofo
	for text in ("$2.50 per SMS", "$2.50 per call", "$25.00 per visit", "Fixed $2.00 for accounts in arrears",
			"$25.00 per demand", "Fixed at $25.00", "$2.00 per page per print", "include VAT at 12.5%"):
		assert text in sofo, text


def test_fee_catalogue(env):
	_, load = env
	ro = load("hp_lending/hp_rollout.py")
	assert ro.FEE_RATES == {"SMS Charge": (2.50, "4011"), "Call Charge": (2.50, "4011"), "Field Visit": (25.00, "4008"),
		"Reminder Letter": (2.00, "4012"), "Demand Notice": (25.00, "4012"),
		"Dishonoured Cheque": (25.00, "4008"), "Statement Print": (2.00, "4008")}
	assert ro.PER_UNIT_FEES == {"Statement Print": 50}
	ref = json.loads((ROOT / "setup_data" / "required_servicing_fee_configuration.json").read_text())
	assert {f["item_code"]: (f["price_list_rate"], f["income_account_number"]) for f in ref["fees"]} == ro.FEE_RATES


@pytest.mark.parametrize("code,qty,ok", [("Statement Print", 3, True), ("Statement Print", 50, True), ("Statement Print", 51, False),
	("Statement Print", 1.5, False), ("Statement Print", 0, False), ("SMS Charge", 1, True), ("SMS Charge", 2, False)])
def test_only_statement_pages_take_a_quantity(env, code, qty, ok):
	_, load = env
	fees = load("hp_lending/hp_service_fees.py")
	item = Row(item_code=code, qty=qty)
	if not ok and code == "SMS Charge":
		assert fees._units(item) == 1 and qty != 1  # _rules then rejects qty != units
		return
	if ok:
		assert fees._units(item) == (int(qty) if code == "Statement Print" else 1)
	else:
		with pytest.raises(ValueError, match="whole number of pages"):
			fees._units(item)


def _origination(env, product_row):
	f, load = env
	f.db.get_value = Mock(return_value=product_row)
	return load("origination.py")


class Meta:
	def __init__(self, fields): self.fields = fields
	def has_field(self, f): return f in self.fields
	def get_label(self, f): return {"custom_flexi_stamp_duty_fee": "Stamp Duty", "custom_flexi_other_fee": "Other Fee",
		"custom_flexi_surcharge_rate": "Surcharge Rate"}[f]


def _app(**values):
	doc = Row(company="Comfort Home Furnishing PTE Limited", loan_product="HP-STD", **values)
	doc["meta"] = Meta({"custom_flexi_surcharge_rate", "custom_flexi_stamp_duty_fee", "custom_flexi_other_fee",
		"custom_hp_default_rate_monthly", "custom_hp_default_grace_days", "custom_hp_default_stop_dpd"})
	return doc


def test_unbookable_fees_are_blocked(env):
	o = _origination(env, {})
	o.block_unbookable_fees(_app(custom_flexi_stamp_duty_fee=0, custom_flexi_other_fee=None))
	with pytest.raises(ValueError, match="Stamp Duty, Other Fee cannot be charged"):
		o.block_unbookable_fees(_app(custom_flexi_stamp_duty_fee=10, custom_flexi_other_fee=5))
	o.block_unbookable_fees(_app(custom_flexi_surcharge_rate=3, custom_hp_converted=1))  # converted MYOB record untouched


def test_default_charge_snapshot_from_product(env):
	o = _origination(env, {"custom_hp_penalty_rate_monthly": 2.5, "grace_period_in_days": 7, "custom_hp_penalty_stop_dpd": 90})
	doc = _app()
	o.apply_default_charge_terms(doc)
	assert (doc.custom_hp_default_rate_monthly, doc.custom_hp_default_grace_days, doc.custom_hp_default_stop_dpd) == (2.5, 7, 90)
	converted = _app(custom_hp_converted=1)
	o.apply_default_charge_terms(converted)
	assert "custom_hp_default_rate_monthly" not in converted


def test_loan_refuses_product_that_differs_from_signed_charge(env, monkeypatch):
	f, load = env
	mapping = load("loan_mapping.py")
	import comfort_homes.origination as origination
	monkeypatch.setattr(origination, "default_charge_terms", lambda product: (3.0, 7, 90))
	signed = Row(custom_hp_default_rate_monthly=2.5, custom_hp_default_grace_days=7, custom_hp_default_stop_dpd=90, loan_product="HP-STD")
	with pytest.raises(ValueError, match="signed with a default charge of 2.5%"):
		mapping._check_default_charge_terms(signed, "HP-STD")
	monkeypatch.setattr(origination, "default_charge_terms", lambda product: (2.5, 7, 90))
	mapping._check_default_charge_terms(signed, "HP-STD")
	mapping._check_default_charge_terms(Row(custom_hp_converted=1), "HP-CONV")


def test_fields_hidden_added_and_fingerprinted():
	cf = {c["name"]: c for c in json.loads((ROOT / "fixtures" / "custom_field.json").read_text())}
	for f in ("surcharge_rate", "surcharge_amount", "stamp_duty_fee", "other_fee"):
		assert cf["Loan Application-custom_flexi_" + f]["hidden"] == 1
	for f in ("rate_monthly", "grace_days", "stop_dpd"):
		assert cf["Loan Application-custom_hp_default_" + f]["read_only"] == 1
	order = json.loads([p for p in json.loads((ROOT / "fixtures" / "property_setter.json").read_text())
		if p["name"] == "Loan Application-main-field_order"][0]["value"])
	i = order.index("custom_flexi_interest_rate")
	assert order[i + 1:i + 4] == ["custom_hp_default_rate_monthly", "custom_hp_default_grace_days", "custom_hp_default_stop_dpd"]
	web = {f["fieldname"]: f for f in json.loads((ROOT / "fixtures" / "web_form.json").read_text())[0]["web_form_fields"]}
	assert web["custom_flexi_stamp_duty_fee"]["hidden"] == 1 and web["custom_flexi_other_fee"]["hidden"] == 1
	assert "'custom_hp_default_rate_monthly'" in (ROOT / "document_controls.py").read_text()
