"""Current-state overview; native per-document permissions apply."""
from comfort_homes.hp_lending.hp_portfolio import portfolio

def execute(filters=None):
    specs=[("application","Application","Link","Loan Application"),("loan","Loan","Link","Loan"),
        ("branch","Branch","Link","Cost Center"),("workflow","Application workflow","Data",None),
        ("loan_status","Native Loan status","Data",None),("contract_flat_rate","Flat contractual %","Percent",None),
        ("loan_balance","Loan balance","Currency",None),("own_dpd","Own stored DPD","Int",None),
        ("borrower_npa","Borrower-wide NPA","Check",None),("independent_fees","Independent fee debt","Currency",None),
        ("legacy_fees","Legacy fee exceptions","Currency",None),("customer_position","Recorded customer position","Currency",None),
        ("po_status","Actual LPO status","Data",None),("supplier_outstanding","CHD outstanding","Currency",None),
        ("delivery","Delivery evidence","Data",None),("chd_terms","CHD terms","Data",None),("exceptions","Exceptions","Data",None)]
    columns=[dict(fieldname=f,label=l,fieldtype=t,options=o,width=170) for f,l,t,o in specs]
    return columns,portfolio(filters), "Current permission-filtered state, not a historical payoff quote. Stored DPD may be stale. Inaccessible balances are blank, not zero. PI/PO status does not prove sending or delivery. Independent fees never change the Loan balance."
