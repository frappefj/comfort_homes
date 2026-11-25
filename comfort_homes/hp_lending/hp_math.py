"""Pure calculation helpers for the Gross HP model.

Nothing in this module imports Frappe, so every rule can be unit tested with
plain Python.  All money is rounded half-up to cents using Decimal, never with
binary floats, so a figure here always equals the figure on the ledger.
"""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")
FIJI_VAT_RATE = Decimal("0.125")
PERIODS_PER_YEAR = {"Monthly": 12, "Bi-Weekly": 26, "Fortnightly": 26, "Weekly": 52}


def d(value) -> Decimal:
	return value if isinstance(value, Decimal) else Decimal(str(value or 0))


def money(value) -> Decimal:
	return d(value).quantize(CENT, rounding=ROUND_HALF_UP)


def split_documentation_fee(vip_amount, vat_rate=FIJI_VAT_RATE):
	"""Split a VAT-inclusive fee into (net, vat).  25.00 -> (22.22, 2.78)."""
	vip = money(vip_amount)
	net = money(vip / (Decimal(1) + d(vat_rate)))
	return net, vip - net


def instalment_schedule(total, periods):
	"""Equal instalments rounded to the cent; the last one absorbs the difference.

	This mirrors what Lending does at 0% interest, so the schedule can be
	checked against the Loan once it is created.
	"""
	total, periods = money(total), int(periods)
	if periods < 1:
		raise ValueError("periods must be at least 1")
	each = money(total / periods)
	rows = [each] * (periods - 1)
	rows.append(total - sum(rows, Decimal(0)))
	return rows


def reclass_entry(*, loan_amount, deposit, goods_value, term_charges, admin_fee, doc_fee_net, doc_fee_vat):
	"""Journal lines that turn the native disbursement into the Gross HP booking.

	Lending books  Dr HP Receivable / Cr Clearing  for ``loan_amount`` (the
	balance after the deposit).  This returns the single extra entry that leaves:

	    Clearing           Cr goods_value        (owed to CHD)
	    Deferred interest  Cr term_charges
	    Admin / Doc / VAT  Cr each
	    Customer deposits  Dr deposit            (cleared against the advance)

	Returned as {"debit": {...}, "credit": {...}}.  Raises if the figures do not
	balance, which happens when a surcharge, stamp duty or other fee has been
	added to the loan amount, because those need an account of their own.
	"""
	loan_amount, deposit, goods = money(loan_amount), money(deposit), money(goods_value)
	credits = {
		"deferred_interest": money(term_charges),
		"admin_fee": money(admin_fee),
		"doc_fee": money(doc_fee_net),
		"vat": money(doc_fee_vat),
	}
	clearing = loan_amount - goods  # positive: debit Clearing, negative: credit it
	debits = {"deposit": deposit, "clearing": clearing}
	credit_total = sum(credits.values(), Decimal(0))
	debit_total = deposit + clearing
	if debit_total != credit_total:
		raise ValueError(
			f"Gross HP booking does not balance: debits {debit_total} vs credits {credit_total}. "
			"Check for a surcharge, stamp duty or other fee that has no income account."
		)
	return {"debit": debits, "credit": credits}


def interest_release_due(total_deferred, term_months, months_elapsed, released_to_date):
	"""Straight-line release of deferred interest, with catch-up.

	The monthly slice is total/term rounded to the cent (13.22), and the final
	month takes whatever is left (13.19), so the total is exact.  Because the
	target is cumulative, a loan that was overdue and has come current releases
	every month it missed in one go (catch-up).
	"""
	total, released = money(total_deferred), money(released_to_date)
	n = int(term_months)
	elapsed = max(0, min(int(months_elapsed), n))
	slice_ = money(total / n)
	target = total if elapsed >= n else min(total, slice_ * elapsed)
	return max(target - released, Decimal(0))


def months_elapsed(disbursement_date, as_of_date):
	"""Month-ends passed, counting the disbursement month: 15 Nov -> 30 Nov is 1."""
	return (as_of_date.year - disbursement_date.year) * 12 + as_of_date.month - disbursement_date.month + 1


def complete_months(start_date, end_date):
	"""Whole months from the contract date to ``end_date`` (CCA Schedule 3): 15 Jan -> 14 Feb is 0, -> 15 Feb is 1."""
	months = (end_date.year - start_date.year) * 12 + end_date.month - start_date.month
	if end_date.day < start_date.day:
		months -= 1
	return max(0, months)


def periodic_penalty_rate(monthly_rate_percent, frequency):
	"""2.5% a month is 30% a year; spread over the repayment frequency."""
	periods = PERIODS_PER_YEAR.get(frequency)
	if not periods:
		raise ValueError(f"Unknown repayment frequency: {frequency}")
	return d(monthly_rate_percent) * 12 / 100 / periods


def penalty_amount(overdue_base, monthly_rate_percent, frequency):
	return money(d(overdue_base) * periodic_penalty_rate(monthly_rate_percent, frequency))


def rule_of_78_unearned(total_interest, periods, periods_paid):
	"""Interest still unearned after ``periods_paid`` of ``periods`` instalments.

	Used for the early-settlement rebate.  12 periods, none paid -> all of it;
	the divisor is the sum of digits (78 for 12, 351 for 26, 1,378 for 52).
	"""
	n, k = int(periods), max(0, min(int(periods_paid), int(periods)))
	remaining = n - k
	return money(d(total_interest) * remaining * (remaining + 1) / (n * (n + 1)))
