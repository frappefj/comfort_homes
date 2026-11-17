"""Pure origination calculations preserving Flexi's observed UAT policy.

These calculations intentionally use the origination float/round sequence,
not the separate Decimal ledger helpers. Approval amount is net financed;
Loan mapping, not the application, carries the gross post-deposit balance.
"""

from math import ceil, isfinite


FACTORS = {"Weekly": 52 / 12, "Fortnightly": 26 / 12, "Bi-monthly": 2, "Monthly": 1}
ANNUAL_FACTORS = {"Weekly": 52, "Fortnightly": 26, "Bi-monthly": 24, "Monthly": 12}


def number(value):
    if value is None or value == "":
        return 0.0
    result = float(value)
    if not isfinite(result):
        raise ValueError("Financial inputs must be finite numbers")
    return result


def money(value):
    return round(number(value), 2)


def profile_values(income_rows=(), expense_rows=(), obligation_rows=()):
    """Sum exact monthly equivalents; round only displayed/stored summaries.

    The assessment-frequency selection deliberately mirrors UAT, including
    continuing past Monthly rows until a non-Monthly frequency is encountered.
    Salary Deductions contribute their absolute monthly value to income.
    """
    income = expenses = obligations = annual_salary = 0.0
    assessment_frequency = "Monthly"
    income_monthly, expense_monthly, obligation_monthly = [], [], []
    for row in income_rows:
        frequency = row.get("frequency")
        exact = number(row.get("amount")) * FACTORS.get(frequency, 1)
        income_monthly.append(money(exact))
        income += abs(exact) if row.get("income_type") == "Salary Deductions" else exact
        if row.get("income_type") == "Net Salary / Wages":
            annual_salary += number(row.get("amount")) * ANNUAL_FACTORS.get(frequency, 12)
        if assessment_frequency == "Monthly" and frequency:
            assessment_frequency = frequency
    for row in expense_rows:
        exact = number(row.get("expense_amount")) * number(row.get("frequency_per_month") or 1)
        expense_monthly.append(money(exact))
        expenses += exact
    for row in obligation_rows:
        exact = number(row.get("amount")) * FACTORS.get(row.get("frequency"), 1)
        obligation_monthly.append(money(exact))
        obligations += exact
    return {
        "income_exact": income,
        "expenses_exact": expenses,
        "obligations_exact": obligations,
        "assessment_frequency": assessment_frequency,
        "income_row_monthly": income_monthly,
        "expense_row_monthly": expense_monthly,
        "obligation_row_monthly": obligation_monthly,
        "fields": {
            "custom_flexi_total_monthly_income": money(income),
            "custom_flexi_total_annual_income": money(income * 12),
            "custom_flexi_total_monthly_expenses": money(expenses),
            "custom_flexi_net_monthly_income": money(income - expenses),
            "custom_flexi_dsr_total_income": money(income),
            "custom_flexi_total_finance_obligations": money(obligations),
            "custom_flexi_dsr_net_income": money(income - obligations),
            "custom_flexi_annual_salary_after_fnpf": money(annual_salary),
        },
    }


def contract_values(values, purchase_rows=()):
    """Keep the approved origination rounding order and application semantics."""
    line_totals = [money(number(r.get("quantity")) * number(r.get("unit_price"))) for r in purchase_rows]
    purchase = sum(line_totals) or number(values.get("custom_flexi_cash_price") or values.get("loan_amount"))
    percent = number(values.get("custom_flexi_deposit_percent"))
    deposit = number(values.get("custom_flexi_deposit_amount"))
    if not 0 <= percent <= 100:
        raise ValueError("Deposit % must be between 0 and 100")
    if deposit < 0 or deposit > purchase:
        raise ValueError("Deposit amount must be between zero and the purchase value")
    # Preserve the existing app's API safeguard for percentage-only entry.
    # Normal Desk/Web Form submissions already supply the calculated amount.
    if purchase and percent and not deposit:
        deposit = min(ceil(purchase * percent / 100), purchase)
    deposit = money(deposit)
    surcharge = money(purchase * max(number(values.get("custom_flexi_surcharge_rate")), 0) / 100)
    after_deposit = money(max(purchase - deposit, 0) + surcharge)
    doc_fee = number(values.get("custom_flexi_documentation_fee") or 25)
    admin_rate = number(values.get("custom_flexi_admin_fee_rate") or 2.5)
    admin = money(after_deposit * admin_rate / 100)
    financed = money(after_deposit + doc_fee + admin
                     + number(values.get("custom_flexi_stamp_duty_fee"))
                     + number(values.get("custom_flexi_other_fee"))) if purchase else 0
    raw_months = number(values.get("custom_flexi_term_months") or 12)
    if raw_months <= 0 or not raw_months.is_integer():
        raise ValueError("Term must be a positive whole number of months")
    months = int(raw_months)
    rate = number(values.get("custom_flexi_interest_rate"))
    term_charges = money(financed * rate / 100 * months / 12)
    total = money(financed + term_charges)
    counts = {"weekly": round(months * 52 / 12), "fortnightly": round(months * 26 / 12),
              "bimonthly": months * 2, "monthly": months}
    amounts_exact = {key: total / count if count else 0 for key, count in counts.items()}
    # Documentation-fee VAT uses the existing ledger helper's half-up split.
    from comfort_homes.hp_lending.hp_math import split_documentation_fee
    doc_net, doc_vat = split_documentation_fee(doc_fee)
    fields = {
        "custom_flexi_cash_price": money(purchase),
        "custom_flexi_deposit_amount": deposit,
        "custom_flexi_deposit_percent": money(deposit / purchase * 100) if purchase else 0,
        "custom_flexi_surcharge_amount": surcharge,
        "custom_flexi_full_value": money(purchase + surcharge),
        "custom_flexi_value_after_deposit": after_deposit,
        "custom_flexi_admin_fee": admin,
        "custom_flexi_net_amount_financed": financed,
        "custom_flexi_term_charges": term_charges,
        "custom_flexi_total_receivable": total,
        "custom_flexi_gross_contract_value": money(total + deposit) if purchase else 0,
        "custom_flexi_doc_fee_net": float(doc_net),
        "custom_flexi_doc_fee_vat": float(doc_vat),
        "custom_flexi_term_years": money(months / 12),
        "loan_amount": financed,
        "repayment_periods": counts["monthly"],
        "repayment_amount": money(amounts_exact["monthly"]),
        "total_payable_amount": total,
        "total_payable_interest": term_charges,
    }
    for key, count in counts.items():
        fields[f"custom_flexi_{key}_instalments"] = count
        fields[f"custom_flexi_{key}_instalment_amount"] = money(amounts_exact[key])
    return {"fields": fields, "amounts_exact": amounts_exact, "line_totals": line_totals}


def assessment_values(profile, contract, selected_dsr=35, payment_period="Monthly"):
    """Decision comparisons use exact figures, not rounded display amounts."""
    income, obligations = profile["income_exact"], profile["obligations_exact"]
    maximum = number(selected_dsr or 35)
    weeks = 52 / 12 if profile["assessment_frequency"] in ("Weekly", "Fortnightly") else 4
    weekly_limit = max((income * maximum / 100 - obligations) / weeks, 0)
    amounts = contract["amounts_exact"]
    monthly = {"Weekly": amounts["weekly"] * 52 / 12,
               "Fortnightly": amounts["fortnightly"] * 26 / 12,
               "Bi-monthly": amounts["bimonthly"] * 2,
               "Monthly": amounts["monthly"]}.get(payment_period, amounts["monthly"])
    pre = obligations / income * 100 if income else 0
    post = (obligations + monthly) / income * 100 if income else 0
    assessed = income > 0 and contract["fields"]["custom_flexi_total_receivable"] > 0
    eligible = assessed and amounts["weekly"] <= weekly_limit and post <= maximum
    fields = {
        "custom_flexi_pre_dsr": money(pre),
        "custom_flexi_regulatory_max_dsr": 50,
        "custom_flexi_dsr_margin": money(max(maximum - pre, 0)),
        "custom_flexi_weekly_eligibility": money(weekly_limit),
        "custom_flexi_post_dsr": money(post),
        "custom_flexi_eligibility_status": "Achieved" if eligible else ("Not Achieved" if assessed else "Not Assessed"),
    }
    for weeks_count in (52, 78, 104, 156):
        fields[f"custom_flexi_max_hp_{weeks_count}_weeks"] = money(weekly_limit * weeks_count)
    return fields
