"""Read-only GL audit trail for a loan: customer and supplier postings in one view."""
from comfort_homes.hp_lending import hp_gl_trace as t


def execute(filters=None):
    filters = dict(filters or {})
    rows = t.fetch(filters)
    view = filters.get("view") or "Lines"
    money = lambda f, l, w=105: dict(fieldname=f, label=l, fieldtype="Currency", width=w)
    if view == "By account":
        data = t.by_account(rows)
        columns = [dict(fieldname="account", label="Account", fieldtype="Link", options="Account", width=360), money("debit", "Debit"), money("credit", "Credit"), money("net", "Net")]
    elif view == "By voucher":
        data = t.by_voucher(rows)
        columns = [dict(fieldname="type", label="Type", fieldtype="Data", width=130), dict(fieldname="voucher", label="Voucher", fieldtype="Dynamic Link", options="type", width=170),
                   dict(fieldname="date", label="Date", fieldtype="Date", width=95), dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=140),
                   dict(fieldname="party", label="Party", fieldtype="Data", width=200), money("debit", "Debit"), money("credit", "Credit"), dict(fieldname="check", label="Check", fieldtype="Data", width=120)]
    else:
        data = rows
        columns = [dict(fieldname="posted", label="Posted", fieldtype="Datetime", width=150), dict(fieldname="date", label="Date", fieldtype="Date", width=95),
                   dict(fieldname="type", label="Type", fieldtype="Data", width=130), dict(fieldname="voucher", label="Voucher", fieldtype="Dynamic Link", options="type", width=170),
                   dict(fieldname="account", label="Account", fieldtype="Link", options="Account", width=360), money("debit", "Debit"), money("credit", "Credit"),
                   dict(fieldname="party", label="Party", fieldtype="Data", width=200), dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=140),
                   dict(fieldname="check", label="Check", fieldtype="Data", width=120), dict(fieldname="cancelled", label="Status", fieldtype="Data", width=80)]
    vouchers = {r["voucher"] for r in rows}
    flagged = {r["voucher"] for r in rows if r["check"]}
    summary = [dict(value=len(vouchers), label="Vouchers", datatype="Int", indicator="Blue"),
               dict(value=round(sum(r["debit"] for r in rows), 2), label="Debit", datatype="Currency", indicator="Blue"),
               dict(value=round(sum(r["credit"] for r in rows), 2), label="Credit", datatype="Currency", indicator="Blue"),
               dict(value=len(flagged), label="Flagged", datatype="Int", indicator="Red" if flagged else "Green")]
    return columns, data, None, None, summary
