"""Re-test default charges after late-imported or back-dated receipts (dev26, batch 3).

The daily charge job (hp_penalties) can only see payments already posted when it runs.  A bank statement
imported later, with receipts value-dated before a charge date, can show the customer was not in default
by as much as was charged.  Daily, every late charge of the last ``WINDOW_DAYS`` days is recomputed from
the payments now known:

    base = instalments due before (charge date - grace), less everything paid on them with a value date
           before the charge date
    should = the same rate and frequency as the original charge

If less than was charged (net of earlier reversals) the difference is reversed with a native Loan Balance
Adjustment (Credit Adjustment to 2015 Deferred Penalty), reference ``HP-PEN-REV-<loan>-<date>-<n>``.
A charge is never increased.  Converted MYOB opening penalties (``-OPEN``) are not re-tested.
"""

import frappe
from frappe.utils import add_days, getdate, today

from comfort_homes.hp_lending import hp_math as m
from comfort_homes.hp_lending import hp_settings as s
from comfort_homes.hp_lending.hp_penalties import KEY, _product_settings

WINDOW_DAYS = 120


def base_as_known(loan, charge_date, grace_days):
	"""Overdue instalments at ``charge_date`` as the ledger now shows them."""
	cutoff = add_days(getdate(charge_date), -int(grace_days))
	rows = frappe.db.sql(
		"""SELECT d.name, d.demand_amount,
		          COALESCE((SELECT SUM(rd.paid_amount) FROM `tabLoan Repayment Detail` rd
		                    JOIN `tabLoan Repayment` lr ON lr.name = rd.parent
		                    WHERE rd.loan_demand = d.name AND lr.docstatus = 1 AND lr.value_date < %s), 0) AS paid
		   FROM `tabLoan Demand` d
		   WHERE d.loan = %s AND d.docstatus = 1 AND d.demand_type = 'EMI' AND d.demand_date < %s""",
		(f"{getdate(charge_date)} 00:00:00", loan, cutoff), as_dict=True)
	return m.money(sum((max(m.d(r.demand_amount) - m.d(r.paid), m.d(0)) for r in rows), m.d(0)))


def reversal_due(charged, reversed_already, should):
	"""Pure: how much of a charge to reverse now (never negative)."""
	return max(m.money(m.d(charged) - m.d(reversed_already) - m.d(should)), m.d(0))


def retest_penalties(as_of=None):
	"""Scheduled 01:30 daily, after customer credit is applied."""
	as_of = getdate(as_of or today())
	since = add_days(as_of, -WINDOW_DAYS)
	charges = frappe.db.sql(
		"""SELECT lba.loan, lba.posting_date, lba.amount, lba.reference_number, l.loan_product, l.repayment_frequency,
		          l.applicant, l.cost_center
		   FROM `tabLoan Balance Adjustment` lba JOIN `tabLoan` l ON l.name = lba.loan
		   WHERE lba.docstatus = 1 AND lba.adjustment_type = 'Debit Adjustment' AND lba.reference_number LIKE %s
		     AND lba.reference_number NOT LIKE %s AND lba.posting_date >= %s AND l.company = %s
		     AND l.status IN ('Disbursed', 'Partially Disbursed')""",
		(f"{KEY}-%", "%-OPEN", since, s.COMPANY), as_dict=True)
	reversed_loans = []
	for c in charges:
		frappe.db.sql("SELECT name FROM `tabLoan` WHERE name = %s FOR UPDATE", (c.loan,))
		rate, _stop, grace = _product_settings(c.loan_product)
		should = m.penalty_amount(base_as_known(c.loan, c.posting_date, grace), rate, c.repayment_frequency)
		prefix = f"{KEY}-REV-{c.loan}-{getdate(c.posting_date)}"
		done = frappe.db.sql("""SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM `tabLoan Balance Adjustment`
			WHERE docstatus = 1 AND loan = %s AND adjustment_type = 'Credit Adjustment' AND reference_number LIKE %s""",
			(c.loan, prefix + "-%"))[0]
		amount = reversal_due(c.amount, done[0], should)
		if amount <= m.d("0.00"):
			continue
		adj = frappe.get_doc({
			"doctype": "Loan Balance Adjustment", "loan": c.loan, "applicant_type": "Customer", "applicant": c.applicant,
			"company": s.COMPANY, "posting_date": as_of, "cost_center": c.cost_center,
			"adjustment_type": "Credit Adjustment", "amount": float(amount), "adjustment_account": s.ACC_DEF_PENALTY,
			"reference_number": f"{prefix}-{int(done[1]) + 1}",
			"remarks": f"Default charge of {getdate(c.posting_date)} reduced from {c.amount} to {should}: payments since "
				f"posted show a smaller overdue amount on that date",
		})
		adj.insert(ignore_permissions=True)
		adj.submit()
		reversed_loans.append(c.loan)
	return reversed_loans
