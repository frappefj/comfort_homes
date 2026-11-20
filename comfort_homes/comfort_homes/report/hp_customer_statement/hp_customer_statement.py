"""HP Customer Statement — gross view for hire-purchase customers.

Shows: purchase items, fees, interest, deposit, all payments, late charges, rebates, and running balance.
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
    from comfort_homes.hp_lending.hp_portfolio import fee_position
    fees = fee_position(filters.loan) if filters.get("loan") else {}
    balance = data[-1]["balance"] if data else 0
    summary = [{"label":"Contractual Loan statement balance", "value":balance,"datatype":"Currency","currency":"FJD"}]
    if fees.get("independent_fees") is not None:
        summary += [{"label":"Independent service fee debt", "value":fees["independent_fees"],"datatype":"Currency","currency":"FJD"},
            {"label":"Legacy fee exceptions — preserved", "value":fees["legacy_fee_exceptions"],"datatype":"Currency","currency":"FJD"},
            {"label":"Recorded customer position (not payoff)", "value":round(balance+fees["independent_fees"]+fees["legacy_fee_exceptions"],2),"datatype":"Currency","currency":"FJD"}]
    return columns, data, "Current contractual statement; independent invoice balances shown separately, never counted again as Loan Demands. Origination rows use actual gross-booking date, not application creation or invented historical GL.", None, summary


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
    loan.check_permission("read")
    application = None
    if loan.loan_application:
        application = frappe.get_doc("Loan Application", loan.loan_application)

    rows = []
    running_balance = 0
    booking_date = frappe.db.get_value("Journal Entry", {"custom_hp_loan":loan_name,"custom_hp_entry_type":"Gross Booking Reclass","docstatus":1}, "posting_date")
    if application and not booking_date:
        frappe.throw("No submitted gross-booking journal: a contractual account statement is not yet available")

    # ── 1. Purchase items (from application) ──
    if application:
        for item in application.get("custom_flexi_purchase_items") or []:
            amount = flt(item.get("total_amount"), 2) if item.get("total_amount") is not None else round(flt(item.get("quantity", 1)) * flt(item.get("unit_price", 0)), 2)
            running_balance += amount
            rows.append({
                "posting_date": booking_date,
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
                "posting_date": booking_date,
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
                "posting_date": booking_date,
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
                "posting_date": booking_date,
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
                "posting_date": booking_date,
                "description": f"Interest ({flt(loan.get('custom_hp_contract_rate') or application.get('custom_flexi_interest_rate') or 22):g}% flat p.a. × {application.get('custom_flexi_term_months', 12)} months)",
                "reference": application.name,
                "charges": term_charges,
                "payments": 0,
                "balance": running_balance,
            })

    # ── 4. Deposit (a Payment Entry linked on the Loan Application; no invoice is raised) ──
    deposit_entry = application.get("custom_hp_deposit_payment_entry") if application else None
    if deposit_entry:
        pe = frappe.db.get_value(
            "Payment Entry", deposit_entry, ["posting_date", "paid_amount", "docstatus"], as_dict=True
        )
        if pe and pe.docstatus == 1:
            running_balance -= flt(pe.paid_amount)
            rows.append({
                "posting_date": pe.posting_date,
                "description": "Deposit Received",
                "reference": deposit_entry,
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

    # ── 6. Late-payment charges (debits) and rebates / waivers (credits) ──
    adjustments = frappe.get_all(
        "Loan Balance Adjustment",
        filters={"loan": loan_name, "docstatus": 1},
        fields=["name", "posting_date", "amount", "remarks", "adjustment_type", "reference_number"],
        order_by="posting_date asc",
    )

    for adj in adjustments:
        amount = flt(adj.amount)
        if not amount:
            continue
        ref = adj.reference_number or ""
        if adj.adjustment_type == "Debit Adjustment":
            description = "Late Payment Charge" if ref.startswith("HP-PEN") else "Balance Adjustment"
            charges, payments = amount, 0
        else:
            description = "Early Settlement Rebate" if ref.startswith("HP-REBATE") else "Waiver / Credit"
            charges, payments = 0, amount
        rows.append({
            "posting_date": adj.posting_date,
            "description": description,
            "reference": adj.name,
            "charges": charges,
            "payments": payments,
            "balance": 0,
        })

    # Sort all rows by date
    rows.sort(key=lambda r: getdate(r["posting_date"]))

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
