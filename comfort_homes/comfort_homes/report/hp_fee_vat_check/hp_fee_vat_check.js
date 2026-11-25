frappe.query_reports["HP Fee VAT Check"] = {
    filters: [{fieldname: "only_issues", label: __("Only issues"), fieldtype: "Check", default: 0}],
    formatter(value, row, column, data, default_formatter) {
        const out = default_formatter(value, row, column, data);
        if (column.fieldname === "status" && value && value !== "OK") return `<b style="color:#d32f2f">${out}</b>`;
        if (column.fieldname === "status" && value === "OK") return `<b style="color:#2e7d32">${out}</b>`;
        return out;
    },
};
