"""Servicing-fee invoices against the approved VAT-inclusive 12.5% split. Read-only."""
from comfort_homes.hp_lending import hp_fee_vat as f


def execute(filters=None):
    rows = f.fetch(filters)
    m = lambda n, l: dict(fieldname=n, label=l, fieldtype="Currency", width=100)
    columns = [dict(fieldname="status", label="Status", fieldtype="Data", width=110),
               dict(fieldname="invoice", label="Invoice", fieldtype="Link", options="Sales Invoice", width=170),
               dict(fieldname="date", label="Date", fieldtype="Date", width=95),
               dict(fieldname="customer", label="Customer", fieldtype="Link", options="Customer", width=170),
               dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=140),
               dict(fieldname="route", label="Route", fieldtype="Data", width=90),
               dict(fieldname="items", label="Items", fieldtype="Data", width=200),
               m("billed", "Billed"), m("vat_booked", "VAT booked"), m("expected_net", "Net"), m("expected_vat", "VAT due")]
    bad = sum(1 for r in rows if r["status"] != "OK")
    summary = [dict(value=len(rows), label="Fee invoices", datatype="Int", indicator="Blue"),
               dict(value=bad, label="Need review", datatype="Int", indicator="Red" if bad else "Green")]
    return columns, rows, None, None, summary
