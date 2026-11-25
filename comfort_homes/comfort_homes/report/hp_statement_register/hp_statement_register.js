frappe.query_reports["HP Statement Register"] = {
    filters: [
        {fieldname: "period", label: __("Period (e.g. 2026-Q4)"), fieldtype: "Data"},
        {fieldname: "needs_print", label: __("Only statements not emailed"), fieldtype: "Check"},
    ],
    formatter(value, row, column, data, default_formatter) {
        if (column.fieldname === "statement" && data && data.statement) return `<a href="${data.statement}" target="_blank">${__("Open PDF")}</a>`;
        return default_formatter(value, row, column, data);
    },
};
