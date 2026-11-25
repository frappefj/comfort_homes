"""dev26 (batch 3): customer credit, receipt allocation, penalty re-test, contract-month rebate, settlement."""
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pytest

from comfort_homes.tests.test_gross_controls import Row, env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
D = Decimal


@pytest.fixture
def credit(env, monkeypatch):
	f, _ = env
	import comfort_homes.hp_lending.hp_credit as module
	monkeypatch.setattr(module, "frappe", f)
	monkeypatch.setattr(module, "credit_account", lambda required=True: "2017 - CR")
	return f, module


@pytest.mark.parametrize("start,end,months", [
	(date(2026, 1, 15), date(2026, 2, 14), 0), (date(2026, 1, 15), date(2026, 2, 15), 1),
	(date(2025, 7, 11), date(2026, 2, 10), 6), (date(2025, 7, 11), date(2026, 2, 11), 7), (date(2026, 3, 1), date(2026, 2, 1), 0)])
def test_complete_months(env, start, end, months):
	from comfort_homes.hp_lending import hp_math as m
	assert m.complete_months(start, end) == months


def test_rebate_uses_contract_months_not_schedule_rows(env, monkeypatch):
	f, load = env
	module = load("hp_lending/hp_settlement.py")
	loan = Row(name="L", posting_date=date(2025, 7, 11), company="C")
	monkeypatch.setattr(module, "_contract", lambda name: (loan, D("687.05"), 24))
	monkeypatch.setattr(module, "_loan_balance", lambda account, name: 600)
	monkeypatch.setattr(module, "s", Row(ACC_DEF_INTEREST="2013"))
	f.db.get_value = Mock(return_value=None)
	fig = module.settlement_figures("L", "2026-02-11")  # weekly or fortnightly: still 7 of 24 months
	from comfort_homes.hp_lending import hp_math as m
	assert fig["periods"] == 24 and fig["periods_paid"] == 7
	assert fig["rebate"] == float(m.rule_of_78_unearned(D("687.05"), 24, 7))
	src = (ROOT / "hp_lending" / "hp_settlement.py").read_text()
	assert 'frappe.only_for(("Finance Manager", "System Manager"))' in src and "def book_rebate(loan, figures)" in src


@pytest.mark.parametrize("charged,reversed_,should,expected", [(10, 0, 4, 6), (10, 6, 4, 0), (10, 0, 12, 0), (3.33, 1, 0, 2.33)])
def test_penalty_reversal_never_increases(env, charged, reversed_, should, expected):
	_, load = env
	module = load("hp_lending/hp_penalty_retest.py")
	assert module.reversal_due(charged, reversed_, should) == D(str(expected)).quantize(D("0.01"))


def _alloc(credit, monkeypatch, net=1000, due=50, fees=None):
	f, module = credit
	loan = Row(name="L", company="C", cost_center="CC", applicant="CUST")
	f.get_doc = Mock(side_effect=lambda x, *a: loan if x == "Loan" else Row(name="NEW", insert=Mock(), submit=Mock(), **x))
	monkeypatch.setattr(module, "payoff_figures", lambda *a: {"balance": D(1500), "rebate": D(100), "credit": D(0), "net": D(net)})
	monkeypatch.setattr(module, "due_outstanding", lambda *a: D(due))
	posted = {}
	monkeypatch.setattr(module, "_repayment", lambda loan, amount, account, value, key, *a, **k: posted.setdefault("repay", (amount, account, key)) and "LR-1")
	monkeypatch.setattr(module, "hold_credit", lambda loan, amount, debit, d, key, remark, party=None: posted.setdefault("credit", (amount, debit, key)) and "JE-1")
	monkeypatch.setattr(module, "_pay_fees", lambda loan, cust, avail, bank, d, key, ba=None: ("PE-1", min(avail, D(fees))) if fees else (None, D(0)))
	return module, posted


def test_receipt_pays_due_then_fees_then_credit(credit, monkeypatch):
	module, posted = _alloc(credit, monkeypatch, due=50, fees=10)
	out = module.allocate_receipt("L", "CUST", 80, "2026-11-20", "BANK", "KEY")
	assert posted["repay"] == (D("50.00"), "BANK", "KEY") and posted["credit"] == (D("20.00"), "BANK", "KEY-CR")
	assert out["loan_repayment"] == "LR-1" and out["fee_payment"] == "PE-1" and out["credit_entry"] == "JE-1"
	assert out["allocation"] == "instalments 50.00, fees 10.00, credit 20.00"


def test_receipt_with_nothing_due_is_all_credit(credit, monkeypatch):
	module, posted = _alloc(credit, monkeypatch, due=0)
	out = module.allocate_receipt("L", "CUST", 43.24, "2026-11-20", "BANK", "KEY")
	assert "repay" not in posted and posted["credit"][0] == D("43.24") and out["status"] == "Customer Credit Held"


def test_payoff_is_held_for_finance_manager(credit, monkeypatch):
	module, posted = _alloc(credit, monkeypatch, net=1400, due=50)
	out = module.allocate_receipt("L", "CUST", 1400, "2026-11-20", "BANK", "KEY")
	assert "repay" not in posted and posted["credit"] == (D("1400.00"), "BANK", "KEY")
	assert out["status"] == "Payoff - confirm settlement"


def test_credit_pays_due_instalments_on_their_due_date(credit, monkeypatch):
	f, module = credit
	monkeypatch.setattr(module, "_credit_loans", lambda account: ["L"])
	monkeypatch.setattr(module, "credit_balance", lambda name, account=None: D("100.00"))
	f.get_all = Mock(return_value=[Row(demand_date=datetime(2026, 12, 4, 0, 0), outstanding_amount=43.24)])
	f.get_doc = Mock(return_value=Row(name="L", company="C", cost_center="CC"))
	f.db.sql = Mock(return_value=[[date(2026, 11, 20)]])  # credit held on 20 Nov; also the last repayment date
	f.db.count = Mock(return_value=0)
	calls = []
	monkeypatch.setattr(module, "_repayment", lambda loan, amount, account, value, key, *a, **k: calls.append((amount, account, value, key)))
	assert module.apply_credit("2026-12-04") == ["L"]
	amount, account, value, key = calls[0]
	assert amount == D("43.24") and account == "2017 - CR" and key == "HP-CREDIT-L-1"
	assert value == datetime(2026, 12, 4, 0, 0)  # the instalment's due date: never overdue


def test_settlement_refuses_shortfall(credit, monkeypatch):
	f, module = credit
	import comfort_homes.hp_lending.hp_settlement as settlement
	monkeypatch.setattr(settlement, "settlement_figures", lambda loan, as_of: {"rebate": 100.0})
	monkeypatch.setattr(settlement, "book_rebate", Mock())
	f.get_doc = Mock(return_value=Row(name="L", status="Disbursed", applicant="C"))
	monkeypatch.setattr(module, "credit_balance", lambda name, account=None: D("1000.00"))
	monkeypatch.setattr(module, "pending_balance", lambda name: D("1500.00"))
	with pytest.raises(ValueError, match="short of the settlement amount 1400.00"):
		module.confirm_settlement("L", "2026-11-20")
	settlement.book_rebate.assert_not_called()


def test_settlement_books_rebate_pays_from_credit_and_refunds_surplus(credit, monkeypatch):
	f, module = credit
	import comfort_homes.hp_lending.hp_settlement as settlement
	monkeypatch.setattr(settlement, "settlement_figures", lambda loan, as_of: {"rebate": 100.0})
	booked = Mock()
	monkeypatch.setattr(settlement, "book_rebate", booked)
	f.get_doc = Mock(return_value=Row(name="L", status="Disbursed", applicant="C"))
	balances = iter([D("1450.00"), D("50.00")])  # before settlement; surplus after the settlement repayment
	monkeypatch.setattr(module, "credit_balance", lambda name, account=None: next(balances))
	pending = iter([D("1500.00"), D("1400.00")])  # before and after the rebate credit adjustment
	monkeypatch.setattr(module, "pending_balance", lambda name: next(pending))
	monkeypatch.setattr(module, "_not_before_last", lambda loan, v: v)
	repay, refund = Mock(return_value="LR-S"), Mock(return_value="JE-R")
	monkeypatch.setattr(module, "_repayment", repay)
	monkeypatch.setattr(module, "_transfer_to_refund", refund)
	f.db.get_value = Mock(return_value="Closed")
	out = module.confirm_settlement("L", "2026-11-20")
	booked.assert_called_once()
	assert repay.call_args.args[1] == D("1400.00") and repay.call_args.args[2] == "2017 - CR"
	assert refund.call_args.args[1] == D("50.00") and out["refund_owed"] == 50.0 and out["status"] == "Closed"


def test_converted_paid_ahead_is_credit_once_account_exists(env, monkeypatch):
	f, load = env
	module = load("hp_lending/hp_conversion.py")
	import comfort_homes.hp_lending.hp_credit as credit_mod
	monkeypatch.setattr(credit_mod, "credit_account", lambda required=True: "2017 - CR")
	held = Mock(return_value="JE")
	monkeypatch.setattr(credit_mod, "hold_credit", held)
	monkeypatch.setattr(module, "_account", lambda number: f"{number} - ACC")
	doc = Row(name="LN", custom_hp_opening_advance=126.35, migration_date=date(2026, 11, 21))
	module._post_opening_advance(doc)
	args = held.call_args.args
	assert args[1] == D("126.35") and args[2] == "1910 - ACC" and args[3] == date(2026, 11, 20) and args[4] == "HP-OPEN-CREDIT-LN"


def test_wiring():
	hooks = (ROOT / "hooks.py").read_text()
	assert '"30 1 * * *"' in hooks and "hp_credit.apply_credit" in hooks and "hp_penalty_retest.retest_penalties" in hooks
	assert hooks.index("hp_credit.apply_credit") < hooks.index("hp_penalty_retest.retest_penalties")
	assert "public/js/hp_loan_settlement.js" in hooks
	item = [d for d in json.loads((ROOT / "fixtures" / "doctype.json").read_text()) if d["name"] == "Loan Bank Reconciliation Item"][0]
	fields = {f["fieldname"]: f for f in item["fields"]}
	assert fields["credit_entry"]["options"] == "Journal Entry" and fields["fee_payment"]["options"] == "Payment Entry"
	assert all(fields[n]["read_only"] == 1 for n in ("credit_entry", "fee_payment", "allocation"))
	je = [c for c in json.loads((ROOT / "fixtures" / "custom_field.json").read_text()) if c["name"] == "Journal Entry-custom_hp_entry_type"][0]
	assert "Customer Credit" in je["options"].split("\n")
	api = (ROOT / "api.py").read_text()
	assert "allocate_receipt(" in api and "flt(existing.amount_paid, 2) > flt(row.amount, 2)" in api
	closure = (ROOT / "hp_lending" / "hp_closure.py").read_text()
	assert 'f"HP-REBATE-REFUND-{loan}"' in closure


def test_fee_invoices_never_touch_the_loan(env):
	src = (ROOT / "hp_lending" / "hp_credit.py").read_text()
	pay = src[src.index("def _pay_fees"):src.index("def payoff_figures")]
	assert '"doctype": "Payment Entry"' in pay and "Loan Repayment" not in pay and "Loan Balance Adjustment" not in pay
