"""Run with:  python -m pytest comfort_homes/tests  (no Frappe needed)."""

from datetime import date
from decimal import Decimal as D

import pytest

from comfort_homes.hp_lending import hp_math as m

# The worked example agreed for the build: $849 goods, $170 deposit, 22%, 12 months.
EX = dict(
	loan_amount=D("879.59"), deposit=D("170.00"), goods_value=D("849.00"),
	term_charges=D("158.61"), admin_fee=D("16.98"), doc_fee_net=D("22.22"), doc_fee_vat=D("2.78"),
)


def test_documentation_fee_split():
	assert m.split_documentation_fee("25.00") == (D("22.22"), D("2.78"))


def test_reclass_balances_and_matches_agreed_ledger():
	r = m.reclass_entry(**EX)
	assert r["debit"] == {"deposit": D("170.00"), "clearing": D("30.59")}
	# Native Cr Clearing 879.59 less this Dr 30.59 leaves 849.00 owed to CHD.
	assert EX["loan_amount"] - r["debit"]["clearing"] == D("849.00")
	assert sum(r["credit"].values()) == D("200.59")


def test_reclass_rejects_unbalanced_figures():
	with pytest.raises(ValueError):
		m.reclass_entry(**{**EX, "admin_fee": D("20.00")})


@pytest.mark.parametrize("periods,each,last", [(12, "73.30", "73.29"), (26, "33.83", "33.84"), (52, "16.92", "16.67")])
def test_instalments_sum_exactly(periods, each, last):
	rows = m.instalment_schedule("879.59", periods)
	assert len(rows) == periods and sum(rows) == D("879.59")
	assert rows[0] == D(each) and rows[-1] == D(last)


def test_interest_release_straight_line_and_final_cap():
	released = D(0)
	for month in range(1, 13):
		due = m.interest_release_due("158.61", 12, month, released)
		assert due == (D("13.22") if month < 12 else D("13.19"))
		released += due
	assert released == D("158.61")
	assert m.interest_release_due("158.61", 12, 13, released) == 0  # never over-releases


def test_interest_release_catches_up_after_overdue():
	# Months 1-3 skipped while overdue, customer cures in month 4.
	assert m.interest_release_due("158.61", 12, 4, 0) == D("52.88")


def test_months_elapsed_counts_disbursement_month():
	assert m.months_elapsed(date(2026, 11, 15), date(2026, 11, 30)) == 1
	assert m.months_elapsed(date(2026, 11, 15), date(2027, 10, 31)) == 12


@pytest.mark.parametrize("freq,pct", [("Monthly", "2.5"), ("Bi-Weekly", "1.153846"), ("Weekly", "0.576923")])
def test_periodic_penalty_rate(freq, pct):
	assert (m.periodic_penalty_rate("2.5", freq) * 100).quantize(D("0.000001")) == D(pct)


def test_penalty_on_overdue_base_only():
	assert m.penalty_amount("73.30", "2.5", "Monthly") == D("1.83")
	assert m.penalty_amount("146.60", "2.5", "Monthly") == D("3.67")


def test_rule_of_78():
	assert m.rule_of_78_unearned("158.61", 12, 0) == D("158.61")
	assert m.rule_of_78_unearned("158.61", 12, 12) == D("0.00")
	assert m.rule_of_78_unearned("780.00", 12, 6) == D("210.00")  # 21 of 78 digit-units remain
