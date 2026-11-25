frappe.query_reports["HP GL Trace"] = {
    filters: [
        {fieldname: "loan", label: __("Loan"), fieldtype: "Link", options: "Loan"},
        {fieldname: "application", label: __("Loan Application"), fieldtype: "Link", options: "Loan Application"},
        {fieldname: "since", label: __("Since"), fieldtype: "Datetime", description: __("Only GL lines posted at or after this time")},
        {fieldname: "voucher_type", label: __("Voucher type"), fieldtype: "Select",
         options: "\nPayment Entry\nLoan Disbursement\nJournal Entry\nLoan Repayment\nLoan Demand\nLoan Interest Accrual\nLoan Balance Adjustment\nLoan Write Off\nSales Invoice\nPurchase Invoice"},
        {fieldname: "account", label: __("Account"), fieldtype: "Link", options: "Account"},
        {fieldname: "include_supplier", label: __("Include supplier side"), fieldtype: "Check", default: 1},
        {fieldname: "view", label: __("View"), fieldtype: "Select", options: "Lines\nBy account\nBy voucher", default: "Lines"},
    ],
    formatter(value, row, column, data, default_formatter) {
        const out = default_formatter(value, row, column, data);
        if (column.fieldname === "check" && value) return `<b style="color:#d32f2f">${out}</b>`;
        return out;
    },
};
