frappe.query_reports["HP Closure Review"] = {
    filters: [
        {fieldname: "action", label: __("Next Step"), fieldtype: "Select", options: "\nRelease\nRebate owed\nNothing deferred"},
    ],
};
