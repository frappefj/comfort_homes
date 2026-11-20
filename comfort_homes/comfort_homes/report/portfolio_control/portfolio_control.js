frappe.query_reports["Portfolio Control"] = {
    filters: [
        {fieldname: "location", label: __("Location"), fieldtype: "Link", options: "Cost Center"},
        {fieldname: "stage", label: __("Stage"), fieldtype: "Select",
         options: ["", "Draft", "Intake", "Review", "Approval", "Approved", "Contract", "Disburse", "Activated", "LPO", "Release", "Sent", "Delivered", "Active", "Closed", "Rejected"].join("\n")},
        {fieldname: "light", label: __("Light"), fieldtype: "Select", options: "\nRed\nOrange\nGreen"},
        {fieldname: "bucket", label: __("Bucket"), fieldtype: "Select", options: "\nCurrent\nGrace\nD30\nD60\nD90\nNPA"},
        {fieldname: "include_closed", label: __("Show closed"), fieldtype: "Check", default: 1},
        {fieldname: "include_rejected", label: __("Show rejected"), fieldtype: "Check", default: 0},
    ],
    formatter(value, row, column, data, default_formatter) {
        const out = default_formatter(value, row, column, data);
        if (column.fieldname === "light" && data) {
            const colour = {Red: "#d32f2f", Orange: "#ef6c00", Green: "#2e7d32", Grey: "#757575"}[data.light] || "#757575";
            return `<span style="font-weight:600;color:${colour}">&#9679; ${frappe.utils.escape_html(data.light)}</span>`;
        }
        if (column.fieldname === "bucket" && data && ["NPA", "D90"].includes(data.bucket)) return `<b style="color:#d32f2f">${out}</b>`;
        return out;
    },
};
