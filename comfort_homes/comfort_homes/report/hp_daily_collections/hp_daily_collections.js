frappe.query_reports["HP Daily Collections"] = {
	filters: [
		{fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1},
		{fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1},
		{fieldname: "location", label: __("Received at (exact name)"), fieldtype: "Data"},
		{fieldname: "totals_only", label: __("One line per location for the period"), fieldtype: "Check"},
		{fieldname: "show_receipts", label: __("Show each receipt"), fieldtype: "Check"},
	],
};
