frappe.query_reports["HP Customer Statement"] = {
    filters: [
        {
            fieldname: "loan",
            label: __("Loan"),
            fieldtype: "Link",
            options: "Loan",
            reqd: 1,
        },
    ],
};
