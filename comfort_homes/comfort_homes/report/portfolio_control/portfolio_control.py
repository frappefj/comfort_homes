"""Portfolio snapshot: traffic light per case, most problematic first. 7.1: the one portfolio view (Flexi Manager Overview merged in)."""
from comfort_homes.hp_lending import hp_control as c


def execute(filters=None):
    rows = c.fetch(filters)
    text = lambda f, l, w=90: dict(fieldname=f, label=l, fieldtype="Data", width=w)
    columns = [
        dict(fieldname="light", label="Light", fieldtype="Data", width=70),
        dict(fieldname="stage", label="Stage", fieldtype="Data", width=85),
        dict(fieldname="loan", label="Loan", fieldtype="Link", options="Loan", width=130),
        dict(fieldname="application", label="App", fieldtype="Link", options="Loan Application", width=130),
        text("customer", "Customer", 150),
        dict(fieldname="location", label="Location", fieldtype="Link", options="Cost Center", width=110),
        dict(fieldname="idle_hours", label="Idle h", fieldtype="Float", precision=0, width=65),
        text("bucket", "Bucket", 75),
        dict(fieldname="dpd", label="DPD", fieldtype="Int", width=55),
        dict(fieldname="overdue", label="Overdue", fieldtype="Currency", width=100),
        dict(fieldname="next_due", label="Next due", fieldtype="Date", width=95),
        dict(fieldname="balance", label="Balance", fieldtype="Currency", width=105),
        text("co_app", "Co-app", 70), text("pi", "PI", 95), text("delivery", "Deliv", 70),
        # 7.1: merged from Flexi Manager Overview
        dict(fieldname="fees_due", label="Fees due", fieldtype="Currency", width=90),
        dict(fieldname="chd_owed", label="CHD owed", fieldtype="Currency", width=95),
        text("exceptions", "Exceptions", 260),
    ]
    s = c.summary(rows)
    summary = [
        dict(value=s["red"], label="Red", datatype="Int", indicator="Red"),
        dict(value=s["orange"], label="Orange", datatype="Int", indicator="Orange"),
        dict(value=s["green"], label="Green", datatype="Int", indicator="Green"),
        dict(value=s["npa"], label="NPA", datatype="Int", indicator="Red"),
        dict(value=s["overdue"], label="Overdue", datatype="Currency", indicator="Red"),
        dict(value=s["book"], label="Active book", datatype="Currency", indicator="Blue"),
        dict(value=s["fees"], label="Fees due", datatype="Currency", indicator="Orange"),
        dict(value=s["exceptions"], label="Exceptions", datatype="Int", indicator="Red" if s["exceptions"] else "Green"),
    ]
    order = ["Current", "Grace", "D30", "D60", "D90", "NPA"]
    chart = dict(type="bar", data=dict(labels=order,
        datasets=[dict(name="Loans", values=[sum(1 for r in rows if r["bucket"] == b) for b in order])]))
    return columns, rows, None, chart, summary
