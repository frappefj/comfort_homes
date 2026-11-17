/* Pure browser preview of the preserved Python origination rules. No listeners. */
(() => {
    const view = new DataView(new ArrayBuffer(8));
    const factor = {Weekly: 52 / 12, Fortnightly: 26 / 12, "Bi-monthly": 2, Monthly: 1};
    const annual = {Weekly: 52, Fortnightly: 26, "Bi-monthly": 24, Monthly: 12};
    const num = value => Number(String(value ?? "").replace(/[^0-9.eE+-]/g, "")) || 0;
    // Frappe's epsilon-based browser rounding differs from Python round(float, n).
    // Round the exact IEEE-754 rational, ties-to-even, then parse its decimal value.
    function round(value, precision = 2) {
        value = Number(value || 0);
        if (!Number.isFinite(value) || !Number.isInteger(precision) || precision < 0 || precision > 6) throw new Error("Invalid rounding input");
        if (value === 0) return value;
        const sign = value < 0 ? -1 : 1;
        view.setFloat64(0, Math.abs(value));
        const hi = view.getUint32(0), lo = view.getUint32(4), exp = (hi >>> 20) & 2047;
        let mantissa = (BigInt(hi & 1048575) << 32n) | BigInt(lo);
        if (exp) mantissa |= 1n << 52n;
        let numerator = mantissa * (10n ** BigInt(precision));
        const shift = (exp ? exp - 1023 : -1022) - 52;
        let result;
        if (shift >= 0) result = numerator << BigInt(shift);
        else {
            const denominator = 1n << BigInt(-shift);
            result = numerator / denominator;
            const twice = (numerator % denominator) * 2n;
            if (twice > denominator || (twice === denominator && result % 2n)) result++;
        }
        return sign * Number(`${result}e-${precision}`);
    }
    function profile(doc) {
        let income = 0, expenses = 0, obligations = 0, salary = 0, frequency = "Monthly";
        for (const row of doc.custom_flexi_income_entries || []) {
            const exact = num(row.amount) * (factor[row.frequency] || 1);
            row.monthly_amount = round(exact);
            income += row.income_type === "Salary Deductions" ? Math.abs(exact) : exact;
            if (row.income_type === "Net Salary / Wages") salary += num(row.amount) * (annual[row.frequency] || 12);
            if (frequency === "Monthly" && row.frequency) frequency = row.frequency;
        }
        for (const row of doc.custom_flexi_living_expenses || []) {
            const exact = num(row.expense_amount) * num(row.frequency_per_month || 1);
            row.monthly_amount = round(exact); expenses += exact;
        }
        for (const row of doc.custom_flexi_finance_obligations || []) {
            const exact = num(row.amount) * (factor[row.frequency] || 1);
            row.monthly_amount = round(exact); obligations += exact;
        }
        return {income, expenses, obligations, weeks: ["Weekly", "Fortnightly"].includes(frequency) ? 52 / 12 : 4,
            fields: {custom_flexi_total_monthly_income: round(income), custom_flexi_total_annual_income: round(income * 12),
                custom_flexi_total_monthly_expenses: round(expenses), custom_flexi_net_monthly_income: round(income - expenses),
                custom_flexi_dsr_total_income: round(income), custom_flexi_total_finance_obligations: round(obligations),
                custom_flexi_dsr_net_income: round(income - obligations), custom_flexi_annual_salary_after_fnpf: round(salary)}};
    }
    function evaluate(doc, source = "amount") {
        const p = profile(doc);
        let purchase = 0;
        for (const row of doc.custom_flexi_purchase_items || []) {
            row.total_amount = round(num(row.quantity) * num(row.unit_price)); purchase += row.total_amount;
        }
        purchase = purchase || num(doc.custom_flexi_cash_price || doc.loan_amount);
        const surcharge = round(purchase * Math.max(num(doc.custom_flexi_surcharge_rate), 0) / 100);
        const deposit = round(source === "percent" ? Math.min(Math.ceil(purchase * num(doc.custom_flexi_deposit_percent) / 100), purchase) : num(doc.custom_flexi_deposit_amount));
        const after = round(Math.max(purchase - deposit, 0) + surcharge);
        const fee = num(doc.custom_flexi_documentation_fee || 25);
        const admin = round(after * num(doc.custom_flexi_admin_fee_rate || 2.5) / 100);
        const financed = purchase ? round(after + fee + admin + num(doc.custom_flexi_stamp_duty_fee) + num(doc.custom_flexi_other_fee)) : 0;
        const months = num(doc.custom_flexi_term_months || 12);
        const charges = round(financed * num(doc.custom_flexi_interest_rate) / 100 * months / 12);
        const total = round(financed + charges), counts = {weekly: round(months * 52 / 12, 0), fortnightly: round(months * 26 / 12, 0), bimonthly: months * 2, monthly: months};
        const amounts = Object.fromEntries(Object.entries(counts).map(([k, v]) => [k, v ? total / v : 0]));
        const monthly = ({Weekly: amounts.weekly * 52 / 12, Fortnightly: amounts.fortnightly * 26 / 12, "Bi-monthly": amounts.bimonthly * 2, Monthly: amounts.monthly})[doc.custom_flexi_payment_period] ?? amounts.monthly;
        const maximum = num(doc.custom_flexi_selected_dsr || 35), pre = p.income ? p.obligations / p.income * 100 : 0;
        const post = p.income ? (p.obligations + monthly) / p.income * 100 : 0;
        const limit = Math.max((p.income * maximum / 100 - p.obligations) / p.weeks, 0);
        const assessed = p.income > 0 && total > 0, eligible = assessed && amounts.weekly <= limit && post <= maximum;
        const fields = {...p.fields, custom_flexi_cash_price: round(purchase), custom_flexi_surcharge_amount: surcharge,
            custom_flexi_full_value: round(purchase + surcharge), custom_flexi_deposit_amount: deposit,
            custom_flexi_deposit_percent: source === "percent" ? num(doc.custom_flexi_deposit_percent) : (purchase ? round(deposit / purchase * 100) : 0), custom_flexi_value_after_deposit: after,
            custom_flexi_admin_fee: admin, custom_flexi_net_amount_financed: financed, custom_flexi_term_charges: charges,
            custom_flexi_total_receivable: total, custom_flexi_gross_contract_value: purchase ? round(total + deposit) : 0,
            custom_flexi_term_years: round(months / 12), loan_amount: financed, repayment_periods: months,
            repayment_amount: round(amounts.monthly), total_payable_amount: total, total_payable_interest: charges,
            custom_flexi_pre_dsr: round(pre), custom_flexi_post_dsr: round(post), custom_flexi_dsr_margin: round(Math.max(maximum - pre, 0)),
            custom_flexi_regulatory_max_dsr: 50, custom_flexi_weekly_eligibility: round(limit),
            custom_flexi_eligibility_status: assessed ? (eligible ? "Achieved" : "Not Achieved") : "Not Assessed"};
        for (const [key, count] of Object.entries(counts)) {
            fields[`custom_flexi_${key}_instalments`] = count;
            fields[`custom_flexi_${key}_instalment_amount`] = round(amounts[key]);
        }
        for (const weeks of [52, 78, 104, 156]) fields[`custom_flexi_max_hp_${weeks}_weeks`] = round(limit * weeks);
        return fields;
    }
    function readWeb(form) {
        const names = ["loan_amount", "custom_flexi_cash_price", "custom_flexi_deposit_amount", "custom_flexi_deposit_percent", "custom_flexi_surcharge_rate", "custom_flexi_documentation_fee", "custom_flexi_admin_fee_rate", "custom_flexi_stamp_duty_fee", "custom_flexi_other_fee", "custom_flexi_term_months", "custom_flexi_interest_rate", "custom_flexi_selected_dsr", "custom_flexi_payment_period", "custom_flexi_purchase_items", "custom_flexi_income_entries", "custom_flexi_living_expenses", "custom_flexi_finance_obligations"];
        return Object.fromEntries(names.map(name => [name, form.get_value(name)]));
    }
    window.comfort_hp_math = Object.freeze({round, profile, evaluate, readWeb});
})();
