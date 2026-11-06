"""HP Customer Statement — gross view for hire-purchase customers.

Shows: purchase items, fees, deposit, all payments, penalties, and running balance.
This aggregates the Lending module's separate principal/interest/charges into
a single customer-friendly statement.
"""

import frappe
from frappe import _
from frappe.utils import flt, getdate


def execute(filters=None):
    filters = frappe._dict(filters or {})
    columns = get_columns()
    data = get_data(filters)
    return columns, data


def get_columns():
    return [
        {"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
        {"fieldname": "description", "label": _("Description"), "fieldtype": "Data", "width": 280},
        {"fieldname": "reference", "label": _("Reference"), "fieldtype": "Data", "width": 150},
        {"fieldname": "charges", "label": _("Charges"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "payments", "label": _("Payments"), "fieldtype": "Currency", "width": 120},
        {"fieldname": "balance", "label": _("Balance"), "fieldtype": "Currency", "width": 130},
    ]


def get_data(filters):
    loan_name = filters.get("loan")
    if not loan_name:
        return []

    loan = frappe.get_doc("Loan", loan_name)
    application = None
    if loan.loan_application:
        application = frappe.get_doc("Loan Application", loan.loan_application)

    rows = []
    running_balance = 0

    # ── 1. Purchase items (from application) ──
    if application:
        for item in application.get("custom_flexi_purchase_items") or []:
            amount = flt(item.get("quantity", 1)) * flt(item.get("unit_price", 0))
            running_balance += amount
            rows.append({
                "posting_date": application.get("creation"),
                "description": f"Purchase: {item.get('product_description', 'HP Item')}",
                "reference": application.name,
                "charges": amount,
                "payments": 0,
                "balance": running_balance,
            })

        # ── 2. Fees ──
        admin_fee = flt(application.get("custom_flexi_admin_fee"))
        if admin_fee:
            running_balance += admin_fee
            rows.append({
                "posting_date": application.get("creation"),
                "description": "Administration Fee (2.5%)",
                "reference": application.name,
                "charges": admin_fee,
                "payments": 0,
                "balance": running_balance,
            })

        doc_fee = flt(application.get("custom_flexi_documentation_fee"))
        if doc_fee:
            running_balance += doc_fee
            rows.append({
                "posting_date": application.get("creation"),
                "description": "Documentation Fee",
                "reference": application.name,
                "charges": doc_fee,
                "payments": 0,
                "balance": running_balance,
            })

        stamp_duty = flt(application.get("custom_flexi_stamp_duty_fee"))
        if stamp_duty:
            running_balance += stamp_duty
            rows.append({
                "posting_date": application.get("creation"),
                "description": "Stamp Duty",
                "reference": application.name,
                "charges": stamp_duty,
                "payments": 0,
                "balance": running_balance,
            })

        # ── 3. Interest (total term charges) ──
        term_charges = flt(application.get("custom_flexi_term_charges"))
        if term_charges:
            running_balance += term_charges
            rows.append({
                "posting_date": application.get("creation"),
                "description": f"Interest ({application.get('custom_flexi_interest_rate', 22)}% p.a. × {application.get('custom_flexi_term_months', 12)} months)",
                "reference": application.name,
                "charges": term_charges,
                "payments": 0,
                "balance": running_balance,
            })

    # ── 4. Deposit ──
    deposit_invoices = frappe.get_all(
        "Sales Invoice",
        filters={"custom_loan_application": loan.loan_application, "docstatus": 1},
        fields=["name", "posting_date", "grand_total"],
        order_by="posting_date asc",
    ) if loan.loan_application else []

    for si in deposit_invoices:
        # Check if this SI has a "Loan Deposit" item
        has_deposit = frappe.db.exists(
            "Sales Invoice Item",
            {"parent": si.name, "item_code": "Loan Deposit"},
        )
        if has_deposit:
            payments = frappe.get_all(
                "Payment Entry Reference",
                filters={"reference_name": si.name, "docstatus": 1},
                fields=["parent"],
            )
            for pe_ref in payments:
                pe = frappe.get_doc("Payment Entry", pe_ref.parent)
                if pe.docstatus == 1:
                    running_balance -= flt(pe.paid_amount)
                    rows.append({
                        "posting_date": pe.posting_date,
                        "description": "Deposit Received",
                        "reference": pe.name,
                        "charges": 0,
                        "payments": flt(pe.paid_amount),
                        "balance": running_balance,
                    })

    # ── 5. Loan Repayments ──
    repayments = frappe.get_all(
        "Loan Repayment",
        filters={"against_loan": loan_name, "docstatus": 1},
        fields=["name", "posting_date", "amount_paid", "repayment_type"],
        order_by="posting_date asc, creation asc",
    )

    for rep in repayments:
        running_balance -= flt(rep.amount_paid)
        desc = "Payment Received"
        if rep.repayment_type == "Advance Payment":
            desc = "Advance Payment Received"
        rows.append({
            "posting_date": rep.posting_date,
            "description": desc,
            "reference": rep.name,
            "charges": 0,
            "payments": flt(rep.amount_paid),
            "balance": running_balance,
        })

    # ── 6. Penalty / Balance Adjustments ──
    adjustments = frappe.get_all(
        "Loan Balance Adjustment",
        filters={"loan": loan_name, "docstatus": 1},
        fields=["name", "posting_date", "adjustment_amount", "remarks"],
        order_by="posting_date asc",
    )

    for adj in adjustments:
        if flt(adj.adjustment_amount) > 0:
            running_balance += flt(adj.adjustment_amount)
            rows.append({
                "posting_date": adj.posting_date,
                "description": "Default Charge" if "penalty" in (adj.remarks or "").lower() else "Balance Adjustment",
                "reference": adj.name,
                "charges": flt(adj.adjustment_amount),
                "payments": 0,
                "balance": running_balance,
            })

    # Sort all rows by date
    rows.sort(key=lambda r: (getdate(r["posting_date"]), rows.index(r)))

    # Recalculate running balance after sort
    running = 0
    for row in rows:
        running += flt(row["charges"]) - flt(row["payments"])
        row["balance"] = round(running, 2)

    # Add totals row
    total_charges = sum(flt(r["charges"]) for r in rows)
    total_payments = sum(flt(r["payments"]) for r in rows)
    rows.append({
        "posting_date": "",
        "description": "TOTAL",
        "reference": "",
        "charges": total_charges,
        "payments": total_payments,
        "balance": round(total_charges - total_payments, 2),
    })

    return rows
