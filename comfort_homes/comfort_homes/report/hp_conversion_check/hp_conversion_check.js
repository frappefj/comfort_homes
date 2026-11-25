frappe.query_reports["HP Conversion Check"] = {
    filters: [
        {fieldname: "show", label: __("Show"), fieldtype: "Select", options: "All\nDifferences only\nDraft\nSubmitted", default: "All"},
        {fieldname: "loan", label: __("Loan"), fieldtype: "Link", options: "Loan"},
    ],
    formatter(value, row, column, data, default_formatter) {
        const out = default_formatter(value, row, column, data);
        if (column.fieldname === "check" && value) return `<b style="color:#d32f2f">${out}</b>`;
        return out;
    },
};
