"""dev27 (batch 5): re-approval, override reason, joint assessment, statements, customer activation, refunds."""
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


@pytest.fixture
def approval(env, monkeypatch):
	f, _ = env
	import comfort_homes.hp_lending.hp_approval as module
	monkeypatch.setattr(module, "frappe", f)
	monkeypatch.setattr(module, "s", SimpleNamespace(COMPANY="C"))
	import comfort_homes.print_helpers as helpers
	monkeypatch.setattr(helpers, "get_co_applicants", lambda doc: [])
	f.msgprint = Mock()
	return f, module


class App(Row):
	def __init__(self, previous=None, **values):
		super().__init__(company="C", applicant="CUST", custom_flexi_cash_price=1000, custom_flexi_term_months=12,
			custom_flexi_purchase_items=[Row(product_description="Bed", quantity=1, unit_price=1000, total_amount=1000)], **values)
		self["_previous"] = previous
		self["add_comment"] = Mock()

	def get_doc_before_save(self):
		return self["_previous"]


def test_changed_terms_names_what_moved(approval):
	_, module = approval
	a, b = App(), App()
	b["custom_flexi_cash_price"] = 1200
	b["custom_flexi_purchase_items"] = [Row(product_description="Bed", quantity=2, unit_price=600, total_amount=1200)]
	assert module.changed_terms(module.approved_terms(a), module.approved_terms(b)) == ["custom_flexi_cash_price", "goods"]


def test_approval_fingerprints_terms_and_enables_its_customer(approval):
	f, module = approval
	f.db.get_value = Mock(return_value=Row(disabled=1, custom_hp_pending_application="APP"))
	f.db.set_value = Mock()
	doc = App(previous=App(workflow_state="Pending Approval"), name="APP", workflow_state="Approved", custom_hp_decision_eligibility="Achieved")
	module.bind_approval(doc)
	assert doc.custom_hp_approved_terms_hash == module.terms_hash(doc)
	f.db.set_value.assert_called_once_with("Customer", "CUST", {"disabled": 0, "custom_hp_pending_application": None})


def test_customer_disabled_by_staff_is_not_enabled(approval):
	f, module = approval
	f.db.get_value = Mock(return_value=Row(disabled=1, custom_hp_pending_application=None))
	f.db.set_value = Mock()
	module.bind_approval(App(previous=App(workflow_state="Pending Approval"), name="APP", workflow_state="Approved", custom_hp_decision_eligibility="Achieved"))
	f.db.set_value.assert_not_called()


@pytest.mark.parametrize("status,reason,ok", [("Not Achieved", "", False), ("Not Achieved", "  ", False), ("Not Assessed", None, False),
	("Not Achieved", "Spouse income verified by payslips", True), ("Achieved", "", True)])
def test_override_reason_required_to_approve(approval, status, reason, ok):
	f, module = approval
	f.db.get_value = Mock(return_value=None)
	doc = App(previous=App(workflow_state="Pending Approval"), name="APP", workflow_state="Approved",
		custom_hp_decision_eligibility=status, custom_hp_eligibility_override_reason=reason)
	if ok:
		module.bind_approval(doc)
	else:
		with pytest.raises(ValueError, match="Override Reason"):
			module.bind_approval(doc)


@pytest.mark.parametrize("state", ["Approved", "Contract Signing"])
def test_terms_change_after_approval_returns_for_reapproval(approval, state):
	_, module = approval
	approved = App(workflow_state=state)
	approved["custom_hp_approved_terms_hash"] = module.terms_hash(approved)
	doc = App(previous=approved, workflow_state=state, status="Approved", custom_hp_approved_terms_hash=approved.custom_hp_approved_terms_hash)
	doc["custom_flexi_term_months"] = 18
	module.bind_approval(doc)
	assert doc.workflow_state == "Pending Approval" and doc.status == "Open" and doc.custom_hp_approved_terms_hash is None
	assert "custom_flexi_term_months" in doc.add_comment.call_args.args[1]


def test_unchanged_terms_stay_approved_and_changed_terms_cannot_move_on(approval):
	_, module = approval
	approved = App(workflow_state="Contract Signing")
	h = module.terms_hash(approved)
	same = App(previous=approved, workflow_state="Contract Signing", custom_hp_approved_terms_hash=h)
	module.bind_approval(same)
	assert same.workflow_state == "Contract Signing"
	moving = App(previous=approved, workflow_state="Ready for Disbursement", custom_hp_approved_terms_hash=h)
	moving["custom_flexi_cash_price"] = 999
	with pytest.raises(ValueError, match="Save the change first"):
		module.bind_approval(moving)


def test_joint_profile_adds_co_applicants(env):
	from comfort_homes import origination
	joint = origination.joint_profile({"income_exact": 1500.0, "obligations_exact": 300.0, "assessment_frequency": "Monthly"},
		[Row(custom_flexi_total_monthly_income=1000, custom_flexi_total_finance_obligations=200)])
	assert joint["income_exact"] == 2500.0 and joint["obligations_exact"] == 500.0


def test_existing_flexi_instalments_monthly(env, monkeypatch):
	f, _ = env
	from comfort_homes import origination
	monkeypatch.setattr(origination, "frappe", f)
	f.get_all = Mock(return_value=[Row(name="L1", repayment_frequency="Weekly", loan_application="OLD"),
		Row(name="L2", repayment_frequency="Bi-Weekly", loan_application="OLD2"), Row(name="L3", repayment_frequency="Monthly", loan_application="APP")])
	f.db.sql = Mock(side_effect=[[[30.0]], [[60.0]]])
	value = origination.existing_flexi_monthly(Row(company="Comfort Home Furnishing PTE Limited", applicant="CUST", name="APP"))
	assert round(value, 2) == round(30 * 52 / 12 + 60 * 26 / 12, 2)  # L3 is this application's own loan


def test_decision_eligibility_is_joint_with_co_applicants(env, monkeypatch):
	f, _ = env
	from comfort_homes import origination
	import comfort_homes.print_helpers as helpers
	monkeypatch.setattr(helpers, "get_co_applicants", lambda doc: [Row(custom_flexi_total_monthly_income=2000, custom_flexi_total_finance_obligations=0)])
	set_values = {}
	monkeypatch.setattr(origination, "_set", lambda doc, field, value: set_values.__setitem__(field, value))
	monkeypatch.setattr(origination, "assessment_values", lambda profile, *a: {"custom_flexi_post_dsr": 20.0,
		"custom_flexi_eligibility_status": "Achieved" if profile["income_exact"] > 1000 else "Not Achieved"})
	origination.apply_joint_assessment(Row(name="APP"), {"income_exact": 500.0, "obligations_exact": 0.0}, {}, {"custom_flexi_eligibility_status": "Not Achieved"})
	assert set_values["custom_hp_decision_eligibility"] == "Achieved" and set_values["custom_hp_joint_monthly_income"] == 2500.0


@pytest.mark.parametrize("run,start,end,label", [
	("2027-01-01", date(2026, 10, 1), date(2026, 12, 31), "2026-Q4"), ("2026-04-01", date(2026, 1, 1), date(2026, 3, 31), "2026-Q1"),
	("2026-07-01", date(2026, 4, 1), date(2026, 6, 30), "2026-Q2"), ("2026-10-01", date(2026, 7, 1), date(2026, 9, 30), "2026-Q3")])
def test_quarter_before(env, run, start, end, label):
	from comfort_homes.hp_lending import hp_statement
	assert hp_statement.quarter_before(run) == (start, end, label)


def test_statement_lines_opening_and_running_balance(env):
	from comfort_homes.hp_lending import hp_statement
	events = [(date(2025, 7, 11), "Contract", 2248.53, 0), (date(2026, 11, 20), "Payments before migration", 0, 1513.40),
		(date(2026, 11, 20), "Paid ahead at migration", 0, 126.35), (date(2026, 12, 9), "Payment received", 0, 43.24),
		(date(2027, 1, 2), "Payment received", 0, 43.24)]
	out = hp_statement.build_lines(events, "2026-10-01", "2026-12-31")
	assert out["opening"] == D("2248.53")
	assert [l["balance"] for l in out["lines"]] == [D("735.13"), D("608.78"), D("565.54")]
	assert out["closing"] == D("565.54")


def test_statement_print_format_renders(env):
	import jinja2
	html = [x for x in json.loads((ROOT / "fixtures" / "print_format.json").read_text()) if x["name"] == "HP Statement of Account"][0]
	assert html["doc_type"] == "Loan"
	st = dict(loan="L", application="A", customer="Jane Doe", address=["1 Main St", "Lautoka"], card_id="LTK 1", hp_account=None,
		contract_date=date(2025, 7, 11), term_months=24, frequency="Fortnightly", from_date=date(2026, 10, 1), to_date=date(2026, 12, 31),
		issued=date(2027, 1, 1), opening=D("735.13"), lines=[dict(date=date(2026, 12, 9), description="Payment received", debit=D(0), credit=D("43.24"), balance=D("691.89"))],
		closing=D("691.89"), overdue=D(0), fees_outstanding=D(0), next_due_date=date(2027, 1, 6), next_due_amount=D("43.24"), default_rate=2.5, email=None)
	frappe_ns = SimpleNamespace(format=lambda v, df: v.strftime("%d-%m-%Y") if hasattr(v, "strftime") else v)
	out = jinja2.Environment().from_string(html["html"]).render(doc=Row(name="L"), hp_statement=lambda doc: st, frappe=frappe_ns)
	assert "Statement of Account" in out and "Jane Doe" in out and "691.89" in out and "2.50% per month" in out and "06-01-2027" in out


def test_chd_invoice_age_flags_after_fourteen_days(env):
	from comfort_homes.hp_lending import hp_portfolio
	orders = [Row(docstatus=1, transaction_date=date(2026, 11, 1)), Row(docstatus=0, transaction_date=date(2026, 10, 1))]
	assert hp_portfolio.chd_invoice_age(orders, on=date(2026, 11, 16)) == 15 > hp_portfolio.CHD_INVOICE_DAYS
	assert hp_portfolio.chd_invoice_age([Row(docstatus=0, transaction_date=date(2026, 10, 1))]) is None
	src = (ROOT / "hp_lending" / "hp_portfolio.py").read_text()
	assert 'custom_hp_converted' in src and 'CHD invoice overdue - {age} days' in src


def test_fixtures_and_wiring():
	hooks = (ROOT / "hooks.py").read_text()
	assert '"30 6 1 1,4,7,10 *"' in hooks and "hp_statement.quarterly_statements" in hooks
	assert hooks.count("hp_approval.bind_approval") == 2 and "public/js/hp_application_controls.js" in hooks and '"HP Statement of Account",' in hooks
	wf = [w for w in json.loads((ROOT / "fixtures" / "zz_workflow.json").read_text()) if w["name"] == "Loan Application Workflow-1"][0]
	withdraw = [t for t in wf["transitions"] if t["action"] == "Withdraw"]
	assert {t["state"] for t in withdraw} == {"Initiated", "Under Review", "Pending Approval", "Approved", "Contract Signing"}
	assert {t["allowed"] for t in withdraw} == {"Loan Officer", "Finance Manager"} and all(t["next_state"] == "Withdrawn" for t in withdraw)
	assert any(s["state"] == "Withdrawn" and str(s["doc_status"]) == "0" for s in wf["states"])
	assert any(x["name"] == "Withdrawn" for x in json.loads((ROOT / "fixtures" / "01_loan_application_workflow_states.json").read_text()))
	assert any(x["name"] == "Withdraw" for x in json.loads((ROOT / "fixtures" / "02_loan_application_workflow_actions.json").read_text()))
	cf = {c["name"]: c for c in json.loads((ROOT / "fixtures" / "custom_field.json").read_text())}
	for f in ("existing_flexi_monthly", "joint_monthly_income", "joint_monthly_obligations", "joint_post_dsr", "decision_eligibility", "approved_terms_hash"):
		assert cf["Loan Application-custom_hp_" + f]["read_only"] == 1
	assert cf["Loan Application-custom_hp_eligibility_override_reason"]["read_only"] == 0
	assert cf["Customer-custom_hp_pending_application"]["fieldtype"] == "Data"
	ss = {x["name"]: x for x in json.loads((ROOT / "fixtures" / "server_script.json").read_text())}
	script = ss["Flexi Create Customer From Loan Application"]["script"]
	assert "customer.customer_type = 'Individual'" in script and "customer.disabled = 1" in script and "customer.type = 'Company'" not in script
	assert ss["Loan Application"]["disabled"] == 1 and ss["link documents to loan application"]["disabled"] == 0
	assert ss["Loan Application"]["script"] == ss["link documents to loan application"]["script"]


def test_deposit_refund_reverses_the_receipt(approval):
	f, module = approval
	f.only_for = Mock()
	app = Row(name="APP", workflow_state="Withdrawn", custom_hp_deposit_payment_entry="PE-DEP", check_permission=Mock())
	paid = Row(company="C", party="CUST", paid_to="1201 - BANK", paid_from="2010 - DEP", paid_amount=300.0)
	created = {}
	f.get_doc = Mock(side_effect=lambda x, *a: app if x == "Loan Application" else paid if x == "Payment Entry" else created.setdefault("pe", Row(name="PE-REF", insert=Mock(), **x)))
	f.db.get_value = Mock(side_effect=lambda dt, filters, field: 1 if field == "docstatus" else None)
	f.utils = SimpleNamespace(today=lambda: "2026-11-25")
	out = module.create_deposit_refund("APP")
	pe = created["pe"]
	assert out == {"payment_entry": "PE-REF", "existing": False} and pe.payment_type == "Pay"
	assert pe.paid_from == "1201 - BANK" and pe.paid_to == "2010 - DEP" and pe.paid_amount == 300.0 and pe.reference_no == "HP-DEP-REFUND-APP"
	pe.insert.assert_called_once_with()
