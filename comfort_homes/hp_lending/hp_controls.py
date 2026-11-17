"""Pure validation rules; no database access or postings."""
from comfort_homes.hp_lending.hp_math import money


def deposit_issues(application, payment, deposit_account, require_submitted=True):
    problems = []
    if payment.get("docstatus") == 2 or (require_submitted and payment.get("docstatus") != 1):
        problems.append("Deposit Payment Entry must be submitted and not cancelled")
    expected = {"payment_type": "Receive", "party_type": "Customer",
                "party": application.get("applicant"), "company": application.get("company"),
                "paid_from": deposit_account, "paid_from_account_currency": "FJD",
                "paid_to_account_currency": "FJD"}
    for key, value in expected.items():
        if not value or payment.get(key) != value:
            problems.append("Deposit Payment Entry mismatch: " + key)
    amount = money(application.get("custom_flexi_deposit_amount"))
    for field in ("paid_amount", "received_amount"):
        if money(payment.get(field)) != amount:
            problems.append("Deposit Payment Entry mismatch: " + field)
    if money(payment.get("total_allocated_amount")) != 0 or payment.get("references"):
        problems.append("Deposit must not be allocated to an invoice or another document")
    for field in ("source_exchange_rate", "target_exchange_rate"):
        if payment.get(field) is not None and payment.get(field) != 1:
            problems.append("Deposit exchange rates must be 1 for FJD")
    return problems
